import os


class OrchestratorConfig:
    # Internal security
    ORCHESTRATOR_API_KEY: str = os.environ.get("ORCHESTRATOR_API_KEY", "")

    # HMAC signing secret — loaded from Key Vault in production,
    # from COMMAND_SIGNING_SECRET env var in demo/dev mode.
    COMMAND_SIGNING_SECRET: str = os.environ.get("COMMAND_SIGNING_SECRET", "")

    # ServiceNow
    SERVICENOW_INSTANCE: str = os.environ.get("SERVICENOW_INSTANCE", "")
    SERVICENOW_USERNAME: str = os.environ.get("SERVICENOW_USERNAME", "")
    SERVICENOW_PASSWORD: str = os.environ.get("SERVICENOW_PASSWORD", "")

    # Azure AD / Microsoft Graph (for licence assignment)
    AZURE_TENANT_ID: str = os.environ.get("AZURE_TENANT_ID", "")
    AZURE_CLIENT_ID: str = os.environ.get("AZURE_CLIENT_ID", "")
    AZURE_CLIENT_SECRET: str = os.environ.get("AZURE_CLIENT_SECRET", "")

    # Azure Service Bus
    SERVICE_BUS_CONNECTION_STRING: str = os.environ.get("SERVICE_BUS_CONNECTION_STRING", "")

    # KB Advisory — Bing Search API
    BING_API_KEY: str = os.environ.get("BING_API_KEY", "")

    # KB Advisory — Azure OpenAI (LLM synthesis)
    AZURE_OPENAI_ENDPOINT: str = os.environ.get("AZURE_OPENAI_ENDPOINT", "")   # e.g. https://mycompany.openai.azure.com
    AZURE_OPENAI_KEY: str = os.environ.get("AZURE_OPENAI_KEY", "")
    AZURE_OPENAI_DEPLOYMENT: str = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")

    # URLs of sibling services
    TEAMS_BOT_URL: str = os.environ.get("TEAMS_BOT_URL", "http://localhost:3978")
    DEVICE_REGISTRY_URL: str = os.environ.get("DEVICE_REGISTRY_URL", "http://localhost:8001")

    PORT: int = int(os.environ.get("PORT", 8000))

    # ------------------------------------------------------------------
    # Gilligan's Island demo mode
    # ------------------------------------------------------------------
    # Set USE_GILLIGAN=true to swap real Azure/ServiceNow with Gilligan's
    # Island mocks and use HTTP polling instead of Azure Service Bus.
    USE_GILLIGAN: bool = os.environ.get("USE_GILLIGAN", "").lower() in ("1", "true", "yes")

    # Base URL of the Gilligan's Island mock server
    # On VirtualBox host-only network the host is typically 192.168.56.1
    GILLIGAN_URL: str = os.environ.get("GILLIGAN_URL", "http://192.168.56.1:3000")
