"""SINGLE-PROCESS ONLY. This set, DedupeMiddleware._seen and ThrottleMiddleware._last all live
in module globals, so they are not shared between uvicorn workers. Run the bot with ONE worker
(`uvicorn app.main:app --workers 1`, the default): with two workers, two taps of the same button
can land on different workers and both start a paid generation. Redis FSM storage makes the
*state* shared; these guards are not.

One generation per user at a time.

Kept in its own module so both the handlers and the outer middleware can see the same set.
The middleware matters: aiogram's event isolation (needed for Redis FSM) takes a per-user lock
INSIDE the FSM middleware, so a second message would otherwise wait silently for the first
generation to finish instead of getting an immediate "busy" answer - and would then start a
second paid call.
"""
from __future__ import annotations

_running: set[int] = set()


def acquire(user_id: int) -> bool:
    """Claim the slot. No await between the check and the add, so two concurrent updates
    cannot both get through."""
    if user_id in _running:
        return False
    _running.add(user_id)
    return True


def release(user_id: int) -> None:
    _running.discard(user_id)


def is_busy(user_id: int) -> bool:
    return user_id in _running
