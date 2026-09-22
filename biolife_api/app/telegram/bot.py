"""Bot and Dispatcher construction + webhook lifecycle.

One Bot and one Dispatcher per process, created on FastAPI startup and closed on shutdown.
Storage: Redis when BIOLIFE_REDIS_URL is set (survives restarts), otherwise in-memory.
"""
from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from app.config import Settings
from app.llm.base import LLMClient
from app.telegram.handlers import router
from app.telegram.middlewares import (
    AccessMiddleware, DedupeMiddleware, LoggingMiddleware, ThrottleMiddleware,
)

log = logging.getLogger("biolife.telegram.bot")

COMMANDS: list[BotCommand] = [
    BotCommand(command="create", description="Bugungi reklama ssenariysi"),
    BotCommand(command="start", description="Botni ishga tushirish"),
    BotCommand(command="cancel", description="Tahrirlashni bekor qilish"),
    BotCommand(command="help", description="Yordam"),
]


class TelegramNotConfigured(RuntimeError):
    """Raised when bot endpoints are used without BIOLIFE_TELEGRAM_BOT_TOKEN."""


def build_bot(settings: Settings) -> Bot:
    if not settings.telegram_bot_token:
        raise TelegramNotConfigured("BIOLIFE_TELEGRAM_BOT_TOKEN is not set")
    return Bot(token=settings.telegram_bot_token.get_secret_value(),
               default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def build_dispatcher(settings: Settings, llm: "LLMClient | None" = None) -> Dispatcher:
    # FSM keeps the active script and the edit mode. MemoryStorage is per-process: with several
    # uvicorn workers a user can land on a worker that does not know their script.
    dp = Dispatcher(storage=MemoryStorage(), llm=llm, settings=settings)   # injected into handlers
    # outer middlewares run before filters, so duplicates and floods never reach a handler
    dp.update.outer_middleware(DedupeMiddleware())
    dp.update.outer_middleware(ThrottleMiddleware(settings.telegram_min_interval_s))
    allowed = frozenset(settings.telegram_allowed_user_ids)
    if not allowed:
        log.warning("BIOLIFE_TELEGRAM_ALLOWED_USER_IDS is empty - ANY Telegram user can generate "
                    "scripts with this internal bot. Set the marketing team's user ids.")
    dp.update.outer_middleware(AccessMiddleware(allowed))
    dp.message.middleware(LoggingMiddleware())
    dp.callback_query.middleware(LoggingMiddleware())
    dp.include_router(router)
    return dp


async def setup_webhook(bot: Bot, settings: Settings) -> None:
    """Idempotent: only calls setWebhook when the URL or secret actually changed."""
    if settings.base_url_had_path:
        log.warning("BIOLIFE_TELEGRAM_WEBHOOK_BASE_URL contained the path %r - it was ignored. "
                    "The base URL must be scheme + host only (https://<host>), the route comes from "
                    "BIOLIFE_TELEGRAM_WEBHOOK_PATH.", settings.base_url_had_path)
    if not settings.telegram_webhook_base_url:
        log.warning("BIOLIFE_TELEGRAM_WEBHOOK_BASE_URL is empty - webhook not registered")
        return
    url = settings.telegram_webhook_url            # single source of truth (validated in Settings)
    info = await bot.get_webhook_info()
    log.info("current webhook: url=%s pending=%s last_error=%s", info.url, info.pending_update_count,
             info.last_error_message)
    # Always call setWebhook: WebhookInfo does not expose the secret token, so we cannot tell
    # whether the secret still matches. Skipping it after a secret rotation would 403 every update.
    await bot.set_webhook(
        url=url,
        secret_token=settings.telegram_webhook_secret.get_secret_value() if settings.telegram_webhook_secret else None,
        drop_pending_updates=settings.telegram_drop_pending_updates,
        allowed_updates=["message", "callback_query"],
        max_connections=settings.telegram_max_connections,
    )
    await bot.set_my_commands(COMMANDS)
    # Both halves are logged so a mismatch is obvious at a glance.
    log.info("webhook set: public=%s -> local route POST %s", url, settings.telegram_webhook_path)


async def shutdown_bot(bot: Bot, settings: Settings) -> None:
    try:
        if settings.telegram_delete_webhook_on_shutdown:
            await bot.delete_webhook(drop_pending_updates=False)
    finally:
        await bot.session.close()
