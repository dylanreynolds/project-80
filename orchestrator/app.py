"""
Orchestrator — FastAPI application.

Endpoints:
  POST /webhook/servicenow   — receives approval/rejection events from ServiceNow Flow
  POST /devices/register     — local agents call this on startup to register themselves
  GET  /devices              — approval_handler uses this to look up user→device mapping
  GET  /health               — liveness probe

Gilligan's Island demo mode (USE_GILLIGAN=true):
  GET  /jobs/pending         — agent polls for next job (replaces Service Bus push)
  POST /jobs/{job_id}/result — agent posts install result (replaces Service Bus event)
  GET  /jobs                 — dashboard view of all jobs

Background threads:
  - Service Bus event listener (production only — skipped when USE_GILLIGAN=true)
"""
import logging
import sys
import threading
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from clients.agent_bus_client import AgentEvent
from config import OrchestratorConfig
from handlers.agent_event_handler import AgentEventHandler
from handlers.approval_handler import ApprovalHandler
from security.command_signer import CommandSigner, load_signing_secret_from_keyvault

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

cfg = OrchestratorConfig()

# ------------------------------------------------------------------
# Client wiring — Gilligan's Island demo mode vs production
# ------------------------------------------------------------------

if cfg.USE_GILLIGAN:
    logger.info("=== GILLIGAN'S ISLAND MODE — using mock services, HTTP job queue ===")

    from clients.gilligan_snow_adapter import GilliganServiceNowAdapter
    from clients.gilligan_iam_adapter import GilliganIAMAdapter
    from clients.static_advisor import StaticAdvisor, NoOpKBClient
    from job_queue import JobStore, JobQueueDispatcher

    snow = GilliganServiceNowAdapter(cfg.GILLIGAN_URL)
    iam = GilliganIAMAdapter(cfg.GILLIGAN_URL)
    kb = NoOpKBClient()
    advisor = StaticAdvisor()

    # HMAC signing — uses COMMAND_SIGNING_SECRET env var (already supported)
    if not cfg.COMMAND_SIGNING_SECRET:
        logger.critical("COMMAND_SIGNING_SECRET must be set in Gilligan's Island mode.")
        sys.exit(1)
    signer = CommandSigner(cfg.COMMAND_SIGNING_SECRET)

    _job_store = JobStore()
    bus = JobQueueDispatcher(_job_store, signer)

else:
    logger.info("=== PRODUCTION MODE — using real Azure / ServiceNow services ===")

    from clients.servicenow_client import ServiceNowClient
    from clients.iam_client import IAMClient
    from clients.kb_client import KBClient
    from clients.llm_advisor import LLMAdvisor
    from clients.agent_bus_client import AgentBusClient

    snow = ServiceNowClient(cfg.SERVICENOW_INSTANCE, cfg.SERVICENOW_USERNAME, cfg.SERVICENOW_PASSWORD)
    iam = IAMClient(cfg.AZURE_TENANT_ID, cfg.AZURE_CLIENT_ID, cfg.AZURE_CLIENT_SECRET)
    kb = KBClient(
        snow_instance=cfg.SERVICENOW_INSTANCE,
        snow_username=cfg.SERVICENOW_USERNAME,
        snow_password=cfg.SERVICENOW_PASSWORD,
        bing_api_key=cfg.BING_API_KEY,
    )
    advisor = LLMAdvisor(
        azure_openai_endpoint=cfg.AZURE_OPENAI_ENDPOINT,
        azure_openai_key=cfg.AZURE_OPENAI_KEY,
        deployment_name=cfg.AZURE_OPENAI_DEPLOYMENT,
    )
    signing_secret = load_signing_secret_from_keyvault(
        vault_url=cfg.AZURE_KEYVAULT_URL,
        secret_name=cfg.SIGNING_SECRET_NAME,
    )
    signer = CommandSigner(signing_secret)
    bus = AgentBusClient(cfg.SERVICE_BUS_CONNECTION_STRING, signer)
    _job_store = None

# Handlers (same regardless of mode — depend only on the interface)
approval_handler = ApprovalHandler(snow, iam, bus, kb, advisor, cfg)
event_handler = AgentEventHandler(snow, cfg)

# In-memory device registry (swap for Redis / Cosmos in production)
_device_registry: dict[str, dict] = {}   # device_id → {user_email, hostname, platform, ...}


# ------------------------------------------------------------------
# Service Bus background listener (production only)
# ------------------------------------------------------------------

def _event_listener_loop():
    def on_event(event: AgentEvent):
        event_handler.handle(event)
    bus.listen_for_events(on_event)


# ------------------------------------------------------------------
# FastAPI lifespan
# ------------------------------------------------------------------

@asynccontextmanager
async def lifespan(application: FastAPI):
    if not cfg.USE_GILLIGAN:
        t = threading.Thread(target=_event_listener_loop, daemon=True)
        t.start()
        logger.info("Service Bus event listener started.")
    else:
        logger.info("HTTP job queue active (no Service Bus listener needed).")
    yield
    logger.info("Orchestrator shutting down.")


app = FastAPI(title="IT Automation Orchestrator", lifespan=lifespan)


# ------------------------------------------------------------------
# Auth helper
# ------------------------------------------------------------------

def _check_api_key(x_api_key: str):
    if x_api_key != cfg.ORCHESTRATOR_API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")


# ------------------------------------------------------------------
# Pydantic models
# ------------------------------------------------------------------

class ServiceNowWebhookPayload(BaseModel):
    sys_id: str
    number: str
    approval: str
    rejection_reason: str = ""
    # Gilligan's Island extras — populated by demo/approve.py
    software_name: str = ""
    requester_email: str = ""
    device_id: str = ""
    teams_conversation_ref: str = ""


class DeviceRegistration(BaseModel):
    device_id: str
    user_email: str
    hostname: str
    platform: str = "windows"
    agent_version: str = ""


# ------------------------------------------------------------------
# Standard endpoints
# ------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "mode": "gilligan" if cfg.USE_GILLIGAN else "production"}


@app.post("/webhook/servicenow")
async def servicenow_webhook(
    payload: ServiceNowWebhookPayload,
    x_api_key: str = Header(...),
):
    _check_api_key(x_api_key)
    logger.info("ServiceNow webhook: ticket %s → %s", payload.number, payload.approval)

    # In Gilligan's Island mode, the webhook payload carries the extra fields
    # (software_name etc.) that Gilligan's Island doesn't store natively.
    if cfg.USE_GILLIGAN and payload.software_name:
        snow.register_extras(
            ticket_number=payload.number,
            software_name=payload.software_name,
            requester_email=payload.requester_email,
            device_id=payload.device_id,
            teams_conversation_ref=payload.teams_conversation_ref,
        )

    ticket = snow.get_ticket(payload.sys_id)

    if payload.approval == "approved":
        threading.Thread(
            target=approval_handler.handle, args=(ticket,), daemon=True
        ).start()
        return {"status": "processing"}

    elif payload.approval == "rejected":
        snow.add_work_note(
            payload.sys_id,
            f"[Orchestrator] Request rejected. Reason: {payload.rejection_reason or 'None provided'}",
        )
        import requests as _req
        if ticket.teams_conversation_ref:
            _req.post(
                f"{cfg.TEAMS_BOT_URL}/api/proactive",
                headers={"X-API-Key": cfg.ORCHESTRATOR_API_KEY, "Content-Type": "application/json"},
                json={
                    "conversation_ref": ticket.teams_conversation_ref,
                    "event_type": "rejected",
                    "payload": {
                        "software_name": ticket.software_name,
                        "ticket_number": ticket.number,
                        "reason": payload.rejection_reason,
                    },
                },
                timeout=10,
            )
        return {"status": "rejected_notified"}

    raise HTTPException(status_code=400, detail=f"Unknown approval state: {payload.approval}")


@app.post("/devices/register", status_code=201)
async def register_device(reg: DeviceRegistration, x_api_key: str = Header(...)):
    _check_api_key(x_api_key)
    _device_registry[reg.device_id] = reg.dict()
    logger.info("Device registered: %s → %s", reg.device_id, reg.user_email)
    return {"status": "registered"}


@app.get("/devices")
async def list_devices(user_email: str = "", x_api_key: str = Header(...)):
    _check_api_key(x_api_key)
    if user_email:
        devices = [d for d in _device_registry.values() if d["user_email"] == user_email]
    else:
        devices = list(_device_registry.values())
    return {"devices": devices}


# ------------------------------------------------------------------
# Gilligan's Island job queue endpoints
# ------------------------------------------------------------------

@app.get("/jobs/pending")
async def get_pending_job(device_id: str, x_api_key: str = Header(...)):
    """
    Agent polls this endpoint.  Returns the next pending job for this device
    and atomically marks it in_progress.  Returns 204 when there is nothing
    to do so the agent can back off and retry after POLL_INTERVAL_SECONDS.
    """
    _check_api_key(x_api_key)
    if _job_store is None:
        raise HTTPException(status_code=400, detail="Job queue not available in production mode")

    job = _job_store.claim_pending(device_id)
    if not job:
        return JSONResponse(status_code=204, content=None)

    return {"job": job.command}   # signed command dict — agent verifies before acting


@app.post("/jobs/{job_id}/result")
async def post_job_result(job_id: str, request: Request, x_api_key: str = Header(...)):
    """Agent posts install result here.  Triggers AgentEventHandler."""
    _check_api_key(x_api_key)
    if _job_store is None:
        raise HTTPException(status_code=400, detail="Job queue not available in production mode")

    result = await request.json()
    job = _job_store.complete(job_id, result)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Forward to the same AgentEventHandler used in production
    event = AgentEvent(
        command_id=job_id,
        device_id=result.get("device_id", ""),
        event_type=result.get("event_type", "install_failed"),
        software_name=result.get("software_name", ""),
        ticket_sys_id=result.get("ticket_sys_id", ""),
        ticket_number=result.get("ticket_number", ""),
        teams_conversation_ref=result.get("teams_conversation_ref", ""),
        detail=result.get("detail", ""),
    )
    threading.Thread(target=event_handler.handle, args=(event,), daemon=True).start()

    return {"status": "received", "job_id": job_id}


@app.get("/jobs")
async def list_jobs(x_api_key: str = Header(...)):
    """Dashboard view — lists all jobs and their current status."""
    _check_api_key(x_api_key)
    if _job_store is None:
        return {"jobs": [], "mode": "production"}
    return {"jobs": _job_store.list_all(), "mode": "gilligan"}


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=cfg.PORT, reload=False)
