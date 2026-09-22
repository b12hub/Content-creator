"""Telegram webhook receiver.

Security: Telegram signs nothing, so the only proof of origin is the secret token that we set
with setWebhook and that Telegram echoes in X-Telegram-Bot-Api-Secret-Token. Compare it in
constant time and reject everything else with 403.

Delivery: Telegram re-sends an update if we answer slowly, so the endpoint validates the JSON,
schedules processing in the background and returns 200 immediately. Duplicates that still arrive
are dropped by DedupeMiddleware.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
from typing import Annotated, Any

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.dependencies import settings_dep

log = logging.getLogger("biolife.telegram.webhook")
router = APIRouter(tags=["telegram"])

_background: set[asyncio.Task[Any]] = set()


async def drain_background(timeout: float = 20.0) -> int:
    """Wait for updates that are still being processed. Called on shutdown: we already answered
    Telegram with 200, so it will never re-send them - losing them would lose real orders."""
    pending = list(_background)
    if not pending:
        return 0
    log.info("draining %d in-flight Telegram updates", len(pending))
    done, still_running = await asyncio.wait(pending, timeout=timeout)
    for task in still_running:
        log.error("update processing did not finish in %.0fs and was cancelled", timeout)
        task.cancel()
    return len(done)


def get_bot_and_dispatcher(request: Request) -> tuple[Bot, Dispatcher]:
    """Resolved INSIDE the handler, after the secret check: an unauthenticated caller must not
    learn whether the bot is configured."""
    bot = getattr(request.app.state, "telegram_bot", None)
    dp = getattr(request.app.state, "telegram_dispatcher", None)
    if bot is None or dp is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Telegram bot is not configured")
    return bot, dp


def verify_secret(settings: Settings, header_value: str | None) -> None:
    expected = settings.telegram_webhook_secret.get_secret_value() if settings.telegram_webhook_secret else ""
    if not expected:
        log.error("BIOLIFE_TELEGRAM_WEBHOOK_SECRET is empty - refusing webhook traffic")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Webhook secret not configured")
    # compare_digest raises TypeError on non-ASCII str, and headers are attacker-controlled
    if not header_value or not hmac.compare_digest(header_value.encode("utf-8", "surrogateescape"),
                                                   expected.encode("utf-8")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid secret token")


async def _process(dp: Dispatcher, bot: Bot, update: Update) -> None:
    try:
        await dp.feed_update(bot, update)
    except Exception:                                    # error router already handled user-facing part
        log.exception("update %s failed outside handlers", update.update_id)


# Registered from the validated setting, so this is always a local path like "/telegram/webhook"
# (a full URL pasted into the env var is reduced to its path by Settings).
@router.post(get_settings().telegram_webhook_path, include_in_schema=False)
async def telegram_webhook(
    request: Request,
    settings: Annotated[Settings, Depends(settings_dep)],
    x_telegram_bot_api_secret_token: Annotated[str | None, Header()] = None,
) -> Response:
    verify_secret(settings, x_telegram_bot_api_secret_token)
    bot, dp = get_bot_and_dispatcher(request)
    try:
        payload = await request.json()
        update = Update.model_validate(payload, context={"bot": bot})
    except ValidationError as exc:
        log.warning("malformed update rejected: %s", exc.errors()[:3])
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Malformed Telegram update") from exc
    except ValueError as exc:                            # not JSON at all
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Body is not valid JSON") from exc

    task = asyncio.create_task(_process(dp, bot, update))
    _background.add(task)                                # keep a reference: tasks are weakly held
    task.add_done_callback(_background.discard)
    return Response(status_code=status.HTTP_200_OK)      # answer Telegram immediately
