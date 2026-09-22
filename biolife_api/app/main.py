from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.db import Database
from app.dependencies import build_llm
from app.routers import ad_generator, campaign, telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.db = Database(settings)
    await app.state.db.connect()
    app.state.llm_client = build_llm(settings)
    # Check the fallback model id in the background: never block startup on a third party.
    app.state.startup_tasks = set()
    fallback = getattr(app.state.llm_client, "fallback", None)
    if fallback is not None and hasattr(fallback, "warn_if_model_unknown"):
        # asyncio keeps only a weak reference to a task: without this set it can be collected
        # mid-flight, and it must be cancelled at shutdown rather than left dangling.
        task = asyncio.create_task(fallback.warn_if_model_unknown())
        app.state.startup_tasks.add(task)
        task.add_done_callback(app.state.startup_tasks.discard)

    # Telegram bot is optional: the API keeps working without a token.
    app.state.telegram_bot = None
    app.state.telegram_dispatcher = None
    app.state.webhook_ready = False
    if settings.telegram_bot_token:
        from app.telegram.bot import build_bot, build_dispatcher, setup_webhook
        bot = build_bot(settings)
        app.state.telegram_bot = bot
        app.state.telegram_dispatcher = build_dispatcher(settings, llm=app.state.llm_client,
                                                         db=app.state.db)
        try:
            await setup_webhook(bot, settings)
            app.state.webhook_ready = True
        except Exception:
            logging.getLogger("biolife").exception("webhook registration failed; bot stays reachable "
                                                   "once Telegram is set up manually")
    else:
        logging.getLogger("biolife").warning("BIOLIFE_TELEGRAM_BOT_TOKEN not set - Telegram bot disabled")

    yield

    for task in list(app.state.startup_tasks):
        task.cancel()

    # Order matters: in-flight updates still use the LLM client, so it is closed last.
    if app.state.telegram_bot is not None:
        from app.routers.telegram import drain_background
        from app.telegram.bot import shutdown_bot
        await drain_background()
        await shutdown_bot(app.state.telegram_bot, settings)
    dp = app.state.telegram_dispatcher
    if dp is not None:
        await dp.storage.close()
    close = getattr(app.state.llm_client, "aclose", None)
    if close:
        await close()
    await app.state.db.close()


app = FastAPI(title="BioLife AI Reel Generator", version="4.0.0", lifespan=lifespan)
app.include_router(ad_generator.router)
app.include_router(campaign.router)
app.include_router(telegram.router)


@app.get("/health", tags=["ops"])
async def health() -> dict[str, object]:
    """Reports the three things that fail SILENTLY: the pool, the schema and the webhook.

    "ok" here used to be true even with no database, no saved_scripts table and a failed webhook
    registration - i.e. with the whole learning loop dead."""
    s = get_settings()
    db = getattr(app.state, "db", None)
    telegram_on = bool(s.telegram_bot_token)
    webhook_ready = getattr(app.state, "webhook_ready", False)
    # "degraded" means CONFIGURED BUT BROKEN. Running without a database is a supported mode
    # (the bot uses the static baseline), so it must not keep a load balancer red forever.
    degraded = ((bool(s.database_url) and not (db and db.enabled and db.table_ready))
                or (telegram_on and not webhook_ready))
    return {
        "status": "degraded" if degraded else "ok",
        "llm_provider": s.llm_provider,
        "database": bool(db and db.enabled),
        "saved_scripts_table": bool(db and db.table_ready),
        "ad_context_function": bool(db and db.function_ready),
        "telegram_webhook": (getattr(app.state, "webhook_ready", False) if telegram_on else None),
    }
