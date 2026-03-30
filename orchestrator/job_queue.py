"""
HTTP Job Queue — replaces Azure Service Bus for Gilligan's Island demo mode.

The orchestrator holds an in-memory dict of jobs.  The Windows 11 agent
polls GET /jobs/pending?device_id=xxx every few seconds; when a job is
available it is returned and marked 'in_progress'.  The agent then runs its
PowerShell steps and POSTs the result to POST /jobs/{job_id}/result.

JobQueueDispatcher implements the same dispatch_install() interface as
AgentBusClient so approval_handler.py is unchanged.
"""
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

from clients.agent_bus_client import AgentEvent, InstallCommand
from security.command_signer import CommandSigner

logger = logging.getLogger(__name__)

_JOB_TTL_SECONDS = 3600   # prune completed jobs after 1 hour


# ------------------------------------------------------------------
# In-memory store
# ------------------------------------------------------------------

@dataclass
class _Job:
    job_id: str
    device_id: str
    status: str          # "pending" | "in_progress" | "done" | "failed"
    command: dict        # signed InstallCommand dict
    result: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    claimed_at: Optional[float] = None
    completed_at: Optional[float] = None


class JobStore:
    """Thread-safe in-memory job store."""

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, _Job] = {}   # job_id → _Job

    def add(self, job: _Job) -> None:
        with self._lock:
            self._jobs[job.job_id] = job

    def claim_pending(self, device_id: str) -> Optional[_Job]:
        """Return and claim the oldest pending job for this device, or None."""
        with self._lock:
            for job in sorted(self._jobs.values(), key=lambda j: j.created_at):
                if job.device_id == device_id and job.status == "pending":
                    job.status = "in_progress"
                    job.claimed_at = time.time()
                    logger.info("Job %s claimed by device %s", job.job_id, device_id)
                    return job
        return None

    def complete(self, job_id: str, result: dict) -> Optional[_Job]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            job.status = "done" if result.get("event_type") != "install_failed" else "failed"
            job.result = result
            job.completed_at = time.time()
            return job

    def list_all(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "job_id": j.job_id,
                    "device_id": j.device_id,
                    "status": j.status,
                    "software_name": j.command.get("software_name", ""),
                    "ticket_number": j.command.get("ticket_number", ""),
                    "created_at": j.created_at,
                    "claimed_at": j.claimed_at,
                    "completed_at": j.completed_at,
                    "result_event": j.result.get("event_type", ""),
                }
                for j in sorted(self._jobs.values(), key=lambda x: x.created_at, reverse=True)
            ]

    def prune(self) -> None:
        cutoff = time.time() - _JOB_TTL_SECONDS
        with self._lock:
            stale = [jid for jid, j in self._jobs.items() if j.created_at < cutoff]
            for jid in stale:
                del self._jobs[jid]
            if stale:
                logger.debug("Pruned %d stale jobs", len(stale))


# ------------------------------------------------------------------
# Dispatcher — AgentBusClient drop-in
# ------------------------------------------------------------------

class JobQueueDispatcher:
    """
    Implements dispatch_install() and a no-op listen_for_events() so it can
    be injected wherever AgentBusClient is used.

    Results arrive via HTTP (POST /jobs/{id}/result) rather than Service Bus,
    so listen_for_events is a no-op here — the orchestrator's FastAPI endpoint
    calls on_result_callback directly.
    """

    def __init__(self, store: JobStore, signer: CommandSigner):
        self._store = store
        self._signer = signer

    def dispatch_install(self, command: InstallCommand) -> None:
        raw = {k: v for k, v in asdict(command).items() if v is not None}
        signed = self._signer.sign(raw)

        job = _Job(
            job_id=command.command_id,
            device_id=command.device_id,
            status="pending",
            command=signed,
        )
        self._store.add(job)
        logger.info(
            "Job %s queued for device %s — '%s'",
            command.command_id,
            command.device_id,
            command.software_name,
        )

    def listen_for_events(self, callback: Callable[[AgentEvent], None]) -> None:
        """No-op — events arrive via HTTP in Gilligan's Island mode."""
        logger.info(
            "JobQueueDispatcher.listen_for_events called — "
            "events are delivered via HTTP in demo mode, nothing to listen for."
        )
