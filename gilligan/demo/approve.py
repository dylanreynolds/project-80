#!/usr/bin/env python3
"""
Demo approval script — Gilligan's Island hackathon demo.

Usage:
    python approve.py                    # list open tickets, then prompt
    python approve.py RITM1000001        # approve a specific ticket
    python approve.py RITM1000001 --reject "Not approved by manager"

What this does:
  1. Approves the ticket in Gilligan's Island (visible in the dashboard)
  2. POSTs the approval to the orchestrator /webhook/servicenow endpoint
     including the extra fields (software_name, teams_conv_ref etc.) so the
     orchestrator can dispatch the install job to the Windows VM agent.

Run this from the Debian VM (or your laptop) after the Teams bot creates a
ticket.  The orchestrator will immediately queue the job; the Windows VM agent
will pick it up within 5 seconds and run the PowerShell install.
"""
import argparse
import json
import os
import sys

import requests

GILLIGAN_URL = os.environ.get("GILLIGAN_URL", "http://192.168.56.1:3000")
ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://127.0.0.1:8000")
ORCHESTRATOR_API_KEY = os.environ.get("ORCHESTRATOR_API_KEY", "demo-api-key-change-me")

# The bot stores extras in its adapter; for the demo we POST to /api/bot-extras
# or pass them via a shared JSON file.  Simplest approach: the bot writes a
# sidecar file; this script reads it.
EXTRAS_FILE = os.environ.get("EXTRAS_FILE", "/tmp/gilligan_ticket_extras.json")


def load_extras() -> dict:
    """Load ticket extras written by the teams-bot adapter."""
    try:
        with open(EXTRAS_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as exc:
        print(f"Warning: could not load extras file: {exc}", file=sys.stderr)
        return {}


def list_tickets() -> list[dict]:
    try:
        r = requests.get(f"{GILLIGAN_URL}/api/snow/tickets", timeout=10)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else data.get("tickets", [])
    except Exception as exc:
        print(f"Error fetching tickets: {exc}", file=sys.stderr)
        return []


def approve_in_gilligan(ticket_number: str) -> bool:
    try:
        r = requests.post(
            f"{GILLIGAN_URL}/api/snow/tickets/{ticket_number}/approve",
            timeout=10,
        )
        r.raise_for_status()
        print(f"Gilligan's Island: ticket {ticket_number} approved")
        return True
    except Exception as exc:
        print(f"Failed to approve in Gilligan's Island: {exc}", file=sys.stderr)
        return False


def notify_orchestrator(ticket_number: str, extras: dict, rejection_reason: str = "") -> bool:
    approval = "rejected" if rejection_reason else "approved"
    payload = {
        "sys_id": ticket_number,
        "number": ticket_number,
        "approval": approval,
        "rejection_reason": rejection_reason,
        # Extra fields Gilligan's Island doesn't store — carried here for the orchestrator
        "software_name": extras.get("software_name", ""),
        "requester_email": extras.get("requester_email", ""),
        "device_id": extras.get("device_id", ""),
        "teams_conversation_ref": extras.get("teams_conversation_ref", ""),
    }
    try:
        r = requests.post(
            f"{ORCHESTRATOR_URL}/webhook/servicenow",
            headers={
                "X-API-Key": ORCHESTRATOR_API_KEY,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )
        r.raise_for_status()
        print(f"Orchestrator notified — status: {r.json().get('status')}")
        return True
    except Exception as exc:
        print(f"Failed to notify orchestrator: {exc}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Gilligan's Island demo approval script")
    parser.add_argument("ticket", nargs="?", help="Ticket number to approve (e.g. RITM1000001)")
    parser.add_argument("--reject", metavar="REASON", help="Reject with this reason instead of approving")
    args = parser.parse_args()

    extras_all = load_extras()

    if not args.ticket:
        tickets = list_tickets()
        if not tickets:
            print("No open tickets in Gilligan's Island.")
            return
        print("\nOpen tickets:")
        for t in tickets:
            num = t.get("number", t.get("id", "?"))
            state = t.get("state", "?")
            extras = extras_all.get(num, {})
            sw = extras.get("software_name", "(software unknown)")
            req = extras.get("requester_email", "(requester unknown)")
            print(f"  {num}  [{state}]  {sw}  —  {req}")
        print()
        args.ticket = input("Enter ticket number to approve: ").strip()
        if not args.ticket:
            return

    ticket = args.ticket.upper()
    extras = extras_all.get(ticket, {})

    if not extras:
        print(
            f"Warning: no extras found for {ticket}. "
            "The orchestrator won't know which software to install or "
            "which Teams conversation to update.\n"
            "Enter them manually (or press Enter to skip):"
        )
        sw = input("  software_name: ").strip()
        req = input("  requester_email: ").strip()
        if sw:
            extras["software_name"] = sw
        if req:
            extras["requester_email"] = req

    rejection_reason = args.reject or ""

    if rejection_reason:
        print(f"Rejecting {ticket}: {rejection_reason}")
    else:
        ok = approve_in_gilligan(ticket)
        if not ok:
            print("Continuing to notify orchestrator anyway...")

    ok = notify_orchestrator(ticket, extras, rejection_reason)
    if ok:
        if rejection_reason:
            print(f"\nDone — {ticket} rejected. Teams user will be notified.")
        else:
            print(
                f"\nDone — {ticket} approved. The Windows VM agent will pick up the "
                "job within 5 seconds and run the PowerShell install."
            )


if __name__ == "__main__":
    main()
