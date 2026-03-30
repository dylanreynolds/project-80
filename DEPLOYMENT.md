# IT Automation — Deployment Notes

## Architecture overview

```
Teams User
    │ chat
    ▼
Teams Bot  (Azure App Service — Python/aiohttp)
    │ POST /api/messages
    │ creates ServiceNow ticket
    ▼
ServiceNow  (Flow / Business Rule)
    │ manager approves → POST /webhook/servicenow
    ▼
Orchestrator  (Azure App Service — Python/FastAPI)
    │ assigns Graph licence
    │ publishes to Azure Service Bus
    ▼
Azure Service Bus Topic: it-automation
    │ subscription: agent-commands  (filtered per device_id)
    ▼
Local Desktop Agent  (Windows Service on end-user machine)
    │ detects install state → winget / SCCM
    │ publishes result to Service Bus
    ▼
Orchestrator  (subscription: agent-events)
    │ closes ServiceNow ticket
    │ POST /api/proactive → Teams Bot
    ▼
Teams User  ← proactive card: "Adobe Pro is ready!"
```

---

## 1. Azure Bot Registration

1. Go to Azure Portal → **Azure Bot** → Create.
2. Set messaging endpoint to `https://<your-bot-url>/api/messages`.
3. Enable the **Microsoft Teams** channel.
4. Copy **App ID** and **App Password** into your `.env`.

---

## 2. Teams Bot (Azure App Service)

```bash
cd teams-bot
pip install -r requirements.txt
python app.py
```

For production: deploy to Azure App Service (Python 3.11 runtime).
Use Azure App Configuration or Key Vault references for secrets.

---

## 3. Orchestrator (Azure App Service)

```bash
cd orchestrator
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

### ServiceNow webhook configuration

In ServiceNow, create a **Business Rule** or **Flow** on `sc_request` that fires
on the `approval` field change and POSTs to:

```
POST https://orchestrator.internal.company.com/webhook/servicenow
X-API-Key: <ORCHESTRATOR_API_KEY>
Content-Type: application/json

{
  "sys_id": "${current.sys_id}",
  "number": "${current.number}",
  "approval": "${current.approval}",
  "rejection_reason": "${current.comments}"
}
```

### Azure Service Bus setup

1. Create a **Service Bus Namespace** (Standard or Premium tier).
2. Create a **Topic**: `it-automation`.
3. Create subscriptions:
   - `agent-commands` — SQL filter per device: `device_id = 'DEVICE-UUID'`
     (create one subscription per managed device, or use a shared subscription
     with client-side filtering for simplicity).
   - `agent-events` — no filter (orchestrator receives all).

---

## 4. Local Desktop Agent

### Deployment via Intune

Package the agent as a Win32 `.intunewin` app:

```powershell
# install.ps1 — run once per device at enrolment
pip install -r requirements.txt

# Set environment variables via Intune configuration profile
[System.Environment]::SetEnvironmentVariable("AGENT_SERVICE_BUS_CONN", "<conn>", "Machine")
[System.Environment]::SetEnvironmentVariable("ORCHESTRATOR_URL",        "<url>",  "Machine")
[System.Environment]::SetEnvironmentVariable("ORCHESTRATOR_API_KEY",    "<key>",  "Machine")
[System.Environment]::SetEnvironmentVariable("AGENT_USER_EMAIL",        $env:USERNAME + "@yourcompany.com", "Machine")

# Register as a Windows scheduled task (runs as SYSTEM for elevation)
schtasks /Create /TN "ITDesktopAgent" /TR "python C:\ITAgent\agent.py" `
         /SC ONSTART /RU SYSTEM /F
```

Running as **SYSTEM** ensures winget installs can succeed without UAC prompts
and without interrupting the logged-in user session.

### Touchless install notes

- `winget install --silent --disable-interactivity` suppresses all UI.
- Adobe products activate automatically when the user next launches the app
  if Named User Licensing is configured with Azure AD SSO in the Adobe Admin Console.
- Office / Microsoft products: licence flows through Graph — no user action needed.
- For apps requiring a reboot, add `--override "/quiet /norestart"` to the winget call
  and schedule the reboot for outside business hours via a follow-up scheduled task.

---

## 5. ServiceNow custom fields

Add these fields to the `sc_request` table:

| Field name                  | Type          | Label                        |
|-----------------------------|---------------|------------------------------|
| `u_software_name`           | String (100)  | Software Name                |
| `u_requester_email`         | String (200)  | Requester Email              |
| `u_device_id`               | String (100)  | Device ID                    |
| `u_teams_conversation_ref`  | String (4000) | Teams Conversation Reference |

---

## 6. Security checklist

- [ ] All inter-service calls protected by `X-API-Key` header (rotate regularly via Key Vault).
- [ ] Service Bus connections use **Managed Identity** in production (not connection strings).
- [ ] Graph App Registration has only `User.Read` and `LicenseAssignment.ReadWrite.All` scopes.
- [ ] Local agent runs as **SYSTEM** — ensure the device is enrolled in Intune / AAD.
- [ ] SCCM AdminService endpoint is on internal network only; agent communicates over VPN/ZTNA.
- [ ] Teams Bot `/api/proactive` endpoint is internal-only (behind Azure API Management or VNet).
