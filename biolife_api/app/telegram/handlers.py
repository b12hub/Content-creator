"""BioLife AI copywriter — Telegram routing (aiogram 3.x).

    /start, /help          greeting
    /create                today's default campaign script
    any free-form text     that text becomes the brief
    🎬 button              turns the active script into AI-video prompts
    ✏️ button              asks for edits, then regenerates the script
    /cancel                leaves the edit mode

Every slow step answers with "O‘ylayapman... ⏳" first and edits that message with the result.
The LLM client is injected as workflow data by build_dispatcher(), so handlers stay testable.
"""
from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Callable
from typing import Final

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ErrorEvent, InlineKeyboardMarkup, Message

from app.config import Settings
from app.llm.base import LLMClient, LLMError
from app.telegram import texts
from app.telegram.keyboards import CB_EDIT_SCRIPT, CB_VIDEO_PROMPT, script_actions_keyboard
from app.telegram.services import DateContext, generate_script, generate_video_prompt, revise_script
from app.telegram.states import KEY_BRIEF, KEY_SCRIPT, ContentStates

log = logging.getLogger("biolife.telegram.handlers")
router = Router(name="biolife-copywriter")

# Channel posts and messages sent on behalf of a chat have from_user=None.
router.message.filter(F.from_user)
router.callback_query.filter(F.from_user)

TELEGRAM_LIMIT: Final[int] = 4096
GENERATION_TIMEOUT_S: Final[float] = 120.0
MAX_BRIEF_CHARS: Final[int] = 2000

# One generation per user at a time: double taps would queue duplicate (paid) LLM calls.
_running: set[int] = set()


def llm_options(settings: Settings) -> dict[str, object]:
    """BIOLIFE_TELEGRAM_LLM_MODEL lets the bot use a different (e.g. cheaper or older) model than
    the JSON endpoints, which require a structured-output capable model."""
    return {"model": settings.telegram_llm_model or None, "max_tokens": settings.telegram_llm_max_tokens}


def acquire(user_id: int) -> bool:
    """Claim the slot without awaiting in between: the webhook processes updates concurrently,
    so a check-then-await-then-add sequence would let two requests through."""
    if user_id in _running:
        return False
    _running.add(user_id)
    return True


# ------------------------------------------------------------------ formatting
def as_html(text: str) -> str:
    """LLM output is plain text: escape it so '<' or '&' cannot break Telegram HTML parsing."""
    return html.escape(text.strip())


def as_copy_block(text: str) -> str:
    """<pre> renders as a code block with one-tap copy in Telegram clients."""
    return f"<pre>{html.escape(text.strip())}</pre>"


def split_for_telegram(text: str, limit: int = TELEGRAM_LIMIT,
                       render: Callable[[str], str] = as_html) -> list[str]:
    """Split the RAW text so that every RENDERED chunk fits Telegram's 4096-character limit.

    Measuring the rendered size matters: escaping expands the text ('&' -> '&amp;') and a <pre>
    wrapper adds 11 characters. Splitting after rendering would cut an entity or a tag in half and
    Telegram answers 400 'can't parse entities'."""
    text = text.strip()
    if not text:
        return [""]
    if len(render(text)) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(render(candidate)) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        # a single block that is still too big: cut it down by characters
        piece = ""
        for char in block:
            if len(render(piece + char)) > limit:
                chunks.append(piece)
                piece = ""
            piece += char
        current = piece
    if current:
        chunks.append(current)
    return chunks


async def deliver(placeholder: Message, text: str, markup: InlineKeyboardMarkup | None = None,
                  render: Callable[[str], str] = as_html) -> None:
    """Replace the 'thinking' message with the result; overflow continues as new messages.

    The raw text is split first and rendered per chunk, so no HTML entity or <pre> tag is ever cut.
    If Telegram still rejects the markup, the same content is re-sent as plain text - a formatting
    problem must not cost the user the whole answer."""
    chunks = split_for_telegram(text, render=render)
    for index, chunk in enumerate(chunks):
        is_last = index == len(chunks) - 1
        reply_markup = markup if is_last else None
        body = render(chunk)
        if index == 0:
            try:
                await placeholder.edit_text(body, reply_markup=reply_markup)
                continue
            except TelegramBadRequest as exc:
                log.info("edit_text failed (%s); resending", exc.message)
        try:
            await placeholder.answer(body, reply_markup=reply_markup)
        except TelegramBadRequest as exc:
            log.warning("HTML rejected (%s); resending as plain text", exc.message)
            await placeholder.answer(chunk, reply_markup=reply_markup, parse_mode=None)


async def fail(placeholder: Message, reason: str) -> None:
    """Error text goes through the same escaping path: an SDK error can contain HTML."""
    await deliver(placeholder, texts.GENERATION_FAILED.format(reason=reason[:300]))


async def run_generation(placeholder: Message, user_id: int, coro, state: FSMContext | None,
                         *, keyboard: bool, store_script: bool, brief: str | None = None) -> None:
    """Shared wrapper: timeout, readable errors, FSM bookkeeping, keyboard."""
    try:
        async with asyncio.timeout(GENERATION_TIMEOUT_S):
            result = await coro
    except asyncio.TimeoutError:
        log.warning("generation timed out user_id=%s", user_id)
        await fail(placeholder, "vaqt tugadi (timeout)")
        return
    except LLMError as exc:
        log.error("LLM failed user_id=%s: %s", user_id, exc)
        await fail(placeholder, str(exc))
        return
    except Exception as exc:                          # the bot must always answer something
        log.exception("generation failed user_id=%s", user_id)
        await fail(placeholder, type(exc).__name__)
        return

    # Store BEFORE delivering: if sending fails, the buttons must still find the script.
    if store_script and state is not None:
        await state.update_data({KEY_SCRIPT: result, **({KEY_BRIEF: brief} if brief else {})})
    await deliver(placeholder, result, script_actions_keyboard() if keyboard else None,
                  render=as_html if store_script else as_copy_block)


# --------------------------------------------------------------------- commands
@router.message(CommandStart())
async def start_command(message: Message, state: FSMContext) -> None:
    await state.set_state(None)
    await message.answer(texts.START)


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await message.answer(texts.HELP)


@router.message(Command("cancel"))
async def cancel_command(message: Message, state: FSMContext) -> None:
    await state.set_state(None)
    await message.answer(texts.CANCELLED)


@router.message(Command("create"))
async def create_command(message: Message, state: FSMContext, llm: LLMClient,
                         settings: Settings) -> None:
    """Today's default campaign: date, month and season are resolved automatically."""
    user_id = message.from_user.id
    await state.set_state(None)                       # /create always leaves the edit mode
    if not acquire(user_id):
        await message.answer(texts.BUSY)
        return
    try:
        placeholder = await message.answer(texts.THINKING)
        log.info("default script user_id=%s context=%s", user_id, DateContext.now().as_text())
        await run_generation(placeholder, user_id, generate_script(llm, **llm_options(settings)), state,
                             keyboard=True, store_script=True)
    finally:
        _running.discard(user_id)


# ------------------------------------------------------- free-form brief (text)
@router.message(ContentStates.waiting_for_script_edits, F.text)
async def receive_edits(message: Message, state: FSMContext, llm: LLMClient,
                        settings: Settings) -> None:
    """Edit feedback for the active script."""
    user_id = message.from_user.id
    data = await state.get_data()
    script = data.get(KEY_SCRIPT)
    if not script:
        await state.set_state(None)
        await message.answer(texts.NO_ACTIVE_SCRIPT)
        return
    if not acquire(user_id):
        await message.answer(texts.BUSY)
        return
    try:
        placeholder = await message.answer(texts.THINKING_EDIT)
        await state.set_state(None)
        await run_generation(placeholder, user_id, revise_script(llm, script, message.text, **llm_options(settings)), state,
                             keyboard=True, store_script=True)
    finally:
        _running.discard(user_id)


@router.message(F.text.startswith("/"))
async def unknown_command(message: Message) -> None:
    """An unknown command is a typo, not a brief - never pay for an LLM call on it."""
    await message.answer(texts.UNKNOWN)


@router.message(F.text)
async def free_form_brief(message: Message, state: FSMContext, llm: LLMClient,
                          settings: Settings) -> None:
    """Any other text is treated as a brief and goes straight to the LLM."""
    user_id = message.from_user.id
    brief = (message.text or "").strip()
    if len(brief) > MAX_BRIEF_CHARS:
        await message.answer(texts.BRIEF_TOO_LONG.format(limit=MAX_BRIEF_CHARS))
        return
    if not acquire(user_id):
        await message.answer(texts.BUSY)
        return
    try:
        placeholder = await message.answer(texts.THINKING)
        log.info("custom brief user_id=%s chars=%d", user_id, len(brief))
        await run_generation(placeholder, user_id, generate_script(llm, brief=brief, **llm_options(settings)), state,
                             keyboard=True, store_script=True, brief=brief)
    finally:
        _running.discard(user_id)


@router.message(~F.text)
async def non_text_message(message: Message) -> None:
    await message.answer(texts.TEXT_ONLY)


# -------------------------------------------------------------- inline buttons
@router.callback_query(F.data == CB_VIDEO_PROMPT)
async def on_video_prompt(query: CallbackQuery, state: FSMContext, llm: LLMClient,
                          settings: Settings) -> None:
    await query.answer()                              # answer first: the query expires in ~15s
    user_id = query.from_user.id
    data = await state.get_data()
    script = data.get(KEY_SCRIPT)
    if not script or not isinstance(query.message, Message):
        await query.message.answer(texts.NO_ACTIVE_SCRIPT) if isinstance(query.message, Message) else None
        return
    if not acquire(user_id):
        await query.message.answer(texts.BUSY)
        return
    try:
        placeholder = await query.message.answer(texts.THINKING_VIDEO)
        await run_generation(placeholder, user_id, generate_video_prompt(llm, script, **llm_options(settings)), None,
                             keyboard=False, store_script=False)
    finally:
        _running.discard(user_id)


@router.callback_query(F.data == CB_EDIT_SCRIPT)
async def on_edit_script(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    if not isinstance(query.message, Message):
        return
    data = await state.get_data()
    if not data.get(KEY_SCRIPT):
        await query.message.answer(texts.NO_ACTIVE_SCRIPT)
        return
    await state.set_state(ContentStates.waiting_for_script_edits)
    await query.message.answer(texts.ASK_EDITS)


# ----------------------------------------------------------------- error trap
@router.error()
async def on_error(event: ErrorEvent) -> bool:
    update = event.update
    log.exception("handler failed update_id=%s: %s", getattr(update, "update_id", None), event.exception)
    try:
        if update.message:
            await update.message.answer(texts.ERROR)
        elif update.callback_query:
            await update.callback_query.answer(texts.ERROR, show_alert=True)
    except TelegramAPIError as exc:
        log.warning("could not deliver error message: %s", exc)
    return True
