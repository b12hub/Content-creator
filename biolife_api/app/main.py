from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.dependencies import build_llm_client
from app.routers import ad_generator, campaign, telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.llm_client = build_llm_client(settings)

    # Telegram bot is optional: the API keeps working without a token.
    app.state.telegram_bot = None
    app.state.telegram_dispatcher = None
    if settings.telegram_bot_token:
        from app.telegram.bot import build_bot, build_dispatcher, setup_webhook
        bot = build_bot(settings)
        app.state.telegram_bot = bot
        app.state.telegram_dispatcher = build_dispatcher(settings)
        try:
            await setup_webhook(bot, settings)
        except Exception:
            logging.getLogger("biolife").exception("webhook registration failed; bot stays reachable "
                                                   "once Telegram is set up manually")
    else:
        logging.getLogger("biolife").warning("BIOLIFE_TELEGRAM_BOT_TOKEN not set - Telegram bot disabled")

    yield

    close = getattr(app.state.llm_client, "aclose", None)
    if close:
        await close()
    if app.state.telegram_bot is not None:
        from app.routers.telegram import drain_background
        from app.telegram.bot import shutdown_bot
        await drain_background()                      # finish in-flight orders before closing anything
        await shutdown_bot(app.state.telegram_bot, settings)
    dp = app.state.telegram_dispatcher
    if dp is not None:
        await dp.storage.close()


app = FastAPI(title="BioLife AI Reel Generator", version="4.0.0", lifespan=lifespan)
app.include_router(ad_generator.router)
app.include_router(campaign.router)
app.include_router(telegram.router)


@app.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    s = get_settings()
    return {"status": "ok", "llm_provider": s.llm_provider}
