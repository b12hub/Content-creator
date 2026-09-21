"""Internal AI content generator for the BioLife marketing team.

Two commands only — no FSM, no catalog, no orders:
    /start   greeting
    /create  today's advertising script

/create answers immediately with "O‘ylayapman... ⏳", does the slow work, then edits that same
message with the result. Every user-facing string is Uzbek (Latin).
"""
from __future__ import annotations

import asyncio
import html
import logging
from typing import Final

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import ErrorEvent, Message

from app.telegram import texts
from app.telegram.services import (
    DateContext, GeneratedScript, fetch_templates_from_db, generate_llm_script,
)

log = logging.getLogger("biolife.telegram.handlers")
router = Router(name="biolife-copywriter")

# Channel posts and messages sent on behalf of a chat have from_user=None.
router.message.filter(F.from_user)

TELEGRAM_LIMIT: Final[int] = 4096
GENERATION_TIMEOUT_S: Final[float] = 90.0

# One generation per user at a time: /create spam would queue duplicate LLM calls.
_running: set[int] = set()


# ------------------------------------------------------------------ formatting
def render_script(script: GeneratedScript) -> str:
    """Two languages per scene. A real two-column table does not fit a phone screen in Telegram,
    so each scene shows RU and UZ one under the other, which stays readable and copy-pasteable."""
    head = (f"🎬 <b>BioLife — bugungi ssenariy</b>\n"
            f"📅 {html.escape(script.date_context.as_text())}\n"
            f"🎯 {html.escape(script.templates)}\n")
    blocks = [
        f"\n<b>{html.escape(line.scene)}</b>\n"
        f"🇷🇺 {html.escape(line.ru)}\n"
        f"🇺🇿 {html.escape(line.uz)}"
        for line in script.lines
    ]
    return head + "".join(blocks)


def split_for_telegram(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Telegram rejects messages over 4096 chars. Split on blank lines, never mid-tag."""
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = block[:limit]
        for extra in range(limit, len(block), limit):    # a single huge block
            chunks.append(current)
            current = block[extra:extra + limit]
    if current:
        chunks.append(current)
    return chunks


# --------------------------------------------------------------------- commands
@router.message(CommandStart())
async def start_command(message: Message) -> None:
    await message.answer(texts.START)


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await message.answer(texts.HELP)


@router.message(Command("create"))
async def create_command(message: Message) -> None:
    user_id = message.from_user.id
    if user_id in _running:
        await message.answer(texts.BUSY)
        return

    placeholder = await message.answer(texts.THINKING)   # instant feedback, edited later
    _running.add(user_id)
    try:
        date_context = DateContext.now()                 # today, month and season (Tashkent time)
        log.info("generation started user_id=%s context=%s", user_id, date_context.as_text())
        async with asyncio.timeout(GENERATION_TIMEOUT_S):
            templates = await fetch_templates_from_db()
            script = await generate_llm_script(date_context.season_key, templates)
        await deliver(placeholder, render_script(script))
        log.info("generation done user_id=%s lines=%d", user_id, len(script.lines))
    except asyncio.TimeoutError:
        log.warning("generation timed out user_id=%s", user_id)
        await deliver(placeholder, texts.GENERATION_FAILED.format(reason="vaqt tugadi (timeout)"))
    except Exception as exc:                             # the bot must always answer something
        log.exception("generation failed user_id=%s", user_id)
        await deliver(placeholder, texts.GENERATION_FAILED.format(reason=type(exc).__name__))
    finally:
        _running.discard(user_id)


async def deliver(placeholder: Message, text: str) -> None:
    """Replace the 'thinking' message with the result; long results continue as new messages."""
    chunks = split_for_telegram(text)
    try:
        await placeholder.edit_text(chunks[0])
    except TelegramBadRequest as exc:                    # message too old, deleted, or unchanged
        log.info("edit_text failed (%s), sending a new message", exc.message)
        await placeholder.answer(chunks[0])
    for chunk in chunks[1:]:
        await placeholder.answer(chunk)


@router.message(F.text)
async def unknown_message(message: Message) -> None:
    await message.answer(texts.UNKNOWN)


# ----------------------------------------------------------------- error trap
@router.error()
async def on_error(event: ErrorEvent) -> bool:
    update = event.update
    log.exception("handler failed update_id=%s: %s", getattr(update, "update_id", None), event.exception)
    try:
        if update.message:
            await update.message.answer(texts.ERROR)
    except TelegramAPIError as exc:                      # user blocked the bot, chat deleted, etc.
        log.warning("could not deliver error message: %s", exc)
    return True
