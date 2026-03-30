"""
Entry point for the IT Helpdesk Teams Bot.
Runs an aiohttp web server that handles:
  - POST /api/messages  — incoming Bot Framework activities from Teams
  - POST /api/proactive — internal endpoint called by the Orchestrator to push
                          status updates back into Teams conversations
"""
import json
import logging
import sys

from aiohttp import web
from aiohttp.web import Request, Response
from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    ConversationState,
    MemoryStorage,
    TurnContext,
    UserState,
)
from botbuilder.core.integration import aiohttp_error_middleware
from botbuilder.schema import Activity, ConversationReference

from bot import ITHelpdeskBot
from config import BotConfig
from integrations.servicenow_client import ServiceNowClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Framework setup
# ------------------------------------------------------------------

settings = BotFrameworkAdapterSettings(BotConfig.APP_ID, BotConfig.APP_PASSWORD)
adapter = BotFrameworkAdapter(settings)

# Use MemoryStorage for local dev; swap for CosmosDbStorage in production
storage = MemoryStorage()
conversation_state = ConversationState(storage)
user_state = UserState(storage)

snow_client = ServiceNowClient(
    instance=BotConfig.SERVICENOW_INSTANCE,
    username=BotConfig.SERVICENOW_USERNAME,
    password=BotConfig.SERVICENOW_PASSWORD,
)

bot = ITHelpdeskBot(conversation_state, user_state, snow_client)


async def on_error(context: TurnContext, error: Exception):
    logger.exception("Bot error: %s", error)
    await context.send_activity("Sorry, something went wrong. Please try again.")
    await conversation_state.clear_state(context)
    await conversation_state.save_changes(context)


adapter.on_turn_error = on_error

# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------


async def messages(req: Request) -> Response:
    """Receive all Bot Framework activities (messages, events, etc.)."""
    if "application/json" not in req.headers.get("Content-Type", ""):
        return Response(status=415)

    body = await req.json()
    activity = Activity().deserialize(body)
    auth_header = req.headers.get("Authorization", "")

    invoke_response = await adapter.process_activity(activity, auth_header, bot.on_turn)
    if invoke_response:
        return Response(
            status=invoke_response.status,
            content_type="application/json",
            body=json.dumps(invoke_response.body),
        )
    return Response(status=201)


async def proactive(req: Request) -> Response:
    """
    Called by the Orchestrator (internal, API-key protected) to push a
    status update into an existing Teams conversation.

    Expected JSON body:
    {
        "conversation_ref": "<serialised ConversationReference>",
        "event_type": "approved" | "install_complete" | "rejected",
        "payload": {
            "software_name": "Adobe Acrobat Pro",
            "ticket_number": "RITM0001234",
            "reason": ""          // only for rejected
        }
    }
    """
    # Validate internal API key
    api_key = req.headers.get("X-API-Key", "")
    if api_key != BotConfig.ORCHESTRATOR_API_KEY:
        return Response(status=403, text="Forbidden")

    body = await req.json()
    conv_ref_raw = body.get("conversation_ref")
    event_type = body.get("event_type", "")
    payload = body.get("payload", {})

    if not conv_ref_raw or not event_type:
        return Response(status=400, text="Missing conversation_ref or event_type")

    try:
        conv_ref = ConversationReference().deserialize(json.loads(conv_ref_raw))
    except Exception as exc:
        logger.error("Failed to deserialise conversation reference: %s", exc)
        return Response(status=400, text="Invalid conversation_ref")

    async def _send(turn_context: TurnContext):
        await ITHelpdeskBot.send_proactive_message(turn_context, event_type, payload)

    await adapter.continue_conversation(conv_ref, _send, BotConfig.APP_ID)
    return Response(status=200)


# ------------------------------------------------------------------
# App wiring
# ------------------------------------------------------------------

app = web.Application(middlewares=[aiohttp_error_middleware])
app.router.add_post("/api/messages", messages)
app.router.add_post("/api/proactive", proactive)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=BotConfig.PORT)
