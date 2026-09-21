"""Outer middlewares: duplicate-update protection, per-user throttling, structured logging."""
from __future__ import annotations

import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

log = logging.getLogger("biolife.telegram")

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class DedupeMiddleware(BaseMiddleware):
    """Telegram re-sends an update if the webhook was slow. Process each update_id once."""

    def __init__(self, capacity: int = 10_000) -> None:
        self._seen: OrderedDict[int, float] = OrderedDict()
        self._capacity = capacity

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        if isinstance(event, Update):
            if event.update_id in self._seen:
                log.warning("duplicate update_id=%s ignored", event.update_id)
                return None
            self._seen[event.update_id] = time.monotonic()
            while len(self._seen) > self._capacity:
                self._seen.popitem(last=False)
        return await handler(event, data)


class ThrottleMiddleware(BaseMiddleware):
    """Minimum interval between updates from one user; extra updates are dropped silently."""

    def __init__(self, min_interval_s: float = 0.4, capacity: int = 10_000) -> None:
        self._last: OrderedDict[int, float] = OrderedDict()
        self._interval = min_interval_s
        self._capacity = capacity

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        user = data.get("event_from_user")
        if user is not None:
            now = time.monotonic()
            previous = self._last.get(user.id)
            self._last[user.id] = now
            self._last.move_to_end(user.id)
            while len(self._last) > self._capacity:
                self._last.popitem(last=False)
            if previous is not None and now - previous < self._interval:
                log.info("throttled user_id=%s", user.id)
                return None
        return await handler(event, data)


class AccessMiddleware(BaseMiddleware):
    """Internal tool: only the marketing team may use it.
    An empty allowlist means "everyone", which is logged loudly at startup."""

    def __init__(self, allowed_user_ids: frozenset[int]) -> None:
        self._allowed = allowed_user_ids

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        if not self._allowed:
            return await handler(event, data)
        user = data.get("event_from_user")
        if user is None or user.id not in self._allowed:
            log.warning("access denied user_id=%s username=%s", getattr(user, "id", None),
                        getattr(user, "username", None))
            if isinstance(event, Update) and event.message is not None:
                from app.telegram.texts import NO_ACCESS
                await event.message.answer(NO_ACCESS)
            return None
        return await handler(event, data)


class LoggingMiddleware(BaseMiddleware):
    """One structured line per handled event, with duration."""

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        started = time.perf_counter()
        user = data.get("event_from_user")
        kind = type(event).__name__
        detail = ""
        if isinstance(event, Message):
            detail = (event.text or event.caption or
                      ("<contact>" if event.contact else "<location>" if event.location else "<other>"))[:64]
        elif isinstance(event, CallbackQuery):
            detail = (event.data or "")[:64]
        try:
            return await handler(event, data)
        finally:
            log.info("tg %s user_id=%s %r %.0fms", kind, getattr(user, "id", None), detail,
                     (time.perf_counter() - started) * 1000)
