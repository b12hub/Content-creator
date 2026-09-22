"""Bot and Dispatcher construction + webhook lifecycle.

One Bot and one Dispatcher per process, created on FastAPI startup and closed on shutdown.
Storage: Redis when BIOLIFE_REDIS_URL is set (survives restarts), otherwise in-memory.
"""
from __future__ import annotations

import logging

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.middleware import FSMContextMiddleware
from aiogram.fsm.storage.base import BaseEventIsolation, BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.types import BotCommand

from app.config import Settings
from app.db import Database
from app.llm.base import LLMClient
from app.telegram.handlers import router
from app.telegram.middlewares import (
    AccessMiddleware, BusyMiddleware, DedupeMiddleware, LoggingMiddleware, ThrottleMiddleware,
)

log = logging.getLogger("biolife.telegram.bot")

COMMANDS: list[BotCommand] = [
    BotCommand(command="create", description="Bugungi reklama ssenariysi"),
    BotCommand(command="start", description="Botni ishga tushirish"),
    BotCommand(command="cancel", description="Tahrirlashni bekor qilish"),
    BotCommand(command="help", description="Yordam"),
]


def register_before_fsm(dp: Dispatcher, middleware: BaseMiddleware) -> None:
    """Append an outer middleware, but ahead of aiogram's FSM middleware (and its isolation lock).

    Falls back to a normal registration if aiogram ever changes that internal list."""
    try:
        chain = dp.update.outer_middleware._middlewares      # noqa: SLF001 - documented fallback
        index = next(i for i, mw in enumerate(chain) if isinstance(mw, FSMContextMiddleware))
        chain.insert(index, middleware)
    except (AttributeError, StopIteration):                  # pragma: no cover - version drift
        log.warning("could not place %s before the FSM middleware; registering normally",
                    type(middleware).__name__)
        dp.update.outer_middleware(middleware)


class TelegramNotConfigured(RuntimeError):
    """Raised when bot endpoints are used without BIOLIFE_TELEGRAM_BOT_TOKEN."""


def build_storage(settings: Settings) -> BaseStorage:
    """Redis keeps the active script across restarts and across uvicorn workers; MemoryStorage
    loses it on every restart and is not shared between processes."""
    if settings.redis_url:
        from aiogram.fsm.storage.redis import RedisStorage
        log.info("FSM storage: Redis")
        return RedisStorage.from_url(settings.redis_url)
    log.warning("BIOLIFE_REDIS_URL is empty - FSM storage is in-memory: the active script is lost "
                "on restart and is not shared between workers")
    return MemoryStorage()


def build_isolation(settings: Settings, storage: BaseStorage | None = None) -> BaseEventIsolation:
    """One user's updates must not run concurrently: FSM data is read-modify-write.

    The isolation REUSES the storage's Redis client. RedisEventIsolation.from_url() would build a
    second connection pool, and aiogram's RedisEventIsolation.close() is a no-op - so that second
    pool could never be released at shutdown."""
    if not settings.redis_url:
        return SimpleEventIsolation()
    from aiogram.fsm.storage.redis import RedisEventIsolation, RedisStorage
    if isinstance(storage, RedisStorage):
        return RedisEventIsolation(redis=storage.redis)
    return RedisEventIsolation.from_url(settings.redis_url)


def build_bot(settings: Settings) -> Bot:
    if not settings.telegram_bot_token:
        raise TelegramNotConfigured("BIOLIFE_TELEGRAM_BOT_TOKEN is not set")
    return Bot(token=settings.telegram_bot_token.get_secret_value(),
               default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def build_dispatcher(settings: Settings, llm: "LLMClient | None" = None,
                     db: "Database | None" = None) -> Dispatcher:
    # FSM keeps the active script and the edit mode.
    storage = build_storage(settings)
    dp = Dispatcher(storage=storage, events_isolation=build_isolation(settings, storage),
                    llm=llm, settings=settings, db=db)        # injected into every handler
    # outer middlewares run before filters, so duplicates and floods never reach a handler
    # Order matters. aiogram registers its FSM middleware (which takes the per-user isolation
    # lock) in Dispatcher.__init__, so anything appended later runs AFTER that lock. A second
    # message from a busy user would then WAIT for the running generation instead of being
    # refused - and start a second paid call as soon as the lock is released. These guards are
    # therefore inserted before the FSM middleware, but after UserContextMiddleware, which is
    # what fills event_from_user.
    register_before_fsm(dp, DedupeMiddleware())
    register_before_fsm(dp, ThrottleMiddleware(settings.telegram_min_interval_s))
    allowed = frozenset(settings.telegram_allowed_user_ids)
    if not allowed:
        log.warning("BIOLIFE_TELEGRAM_ALLOWED_USER_IDS is empty - ANY Telegram user can generate "
                    "scripts with this internal bot. Set the marketing team's user ids.")
    register_before_fsm(dp, AccessMiddleware(allowed))
    register_before_fsm(dp, BusyMiddleware())
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
