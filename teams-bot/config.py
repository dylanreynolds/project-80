"""
Configuration for the IT Helpdesk Teams Bot.
All values are loaded from environment variables (or a .env file in dev).
"""
import os


class BotConfig:
    # Azure Bot Service credentials
    APP_ID: str = os.environ.get("MicrosoftAppId", "")
    APP_PASSWORD: str = os.environ.get("MicrosoftAppPassword", "")
    PORT: int = int(os.environ.get("PORT", 3978))

    # ServiceNow
    SERVICENOW_INSTANCE: str = os.environ.get("SERVICENOW_INSTANCE", "")   # e.g. "mycompany"
    SERVICENOW_USERNAME: str = os.environ.get("SERVICENOW_USERNAME", "")
    SERVICENOW_PASSWORD: str = os.environ.get("SERVICENOW_PASSWORD", "")

    # Orchestrator service (receives approved tickets and drives installation)
    ORCHESTRATOR_URL: str = os.environ.get("ORCHESTRATOR_URL", "")
    ORCHESTRATOR_API_KEY: str = os.environ.get("ORCHESTRATOR_API_KEY", "")

    # Azure Bot conversation reference store (Cosmos DB or in-memory for dev)
    STORAGE_CONNECTION_STRING: str = os.environ.get("STORAGE_CONNECTION_STRING", "")
