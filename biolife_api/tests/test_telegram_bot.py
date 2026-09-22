"""BioLife copywriter bot: free-form briefs, /create, inline buttons, edit FSM.
Real Telegram update JSON goes through the real Dispatcher; outgoing API calls are captured,
and the LLM is a deterministic fake injected the same way the app injects the real client."""
from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Update

from app.llm.base import LLMError
from app.llm.fake_client import FakeLLMClient
from app.telegram import texts
from app.telegram.handlers import split_for_telegram
from app.telegram.keyboards import CB_EDIT_SCRIPT, CB_VIDEO_PROMPT
from app.telegram.services import DateContext, season_key
from app.telegram.states import ContentStates
from tests.telegram_factories import CHAT_ID, USER_ID, callback_update, make_bot, message_update

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def bot_env(tg_dispatcher):
    bot, session = make_bot()
    llm = FakeLLMClient()
    from app.config import get_settings
    tg_dispatcher["llm"] = llm                       # same injection point as build_dispatcher()
    tg_dispatcher["settings"] = get_settings()
    key = StorageKey(bot_id=bot.id, chat_id=CHAT_ID, user_id=USER_ID)
    await tg_dispatcher.storage.set_state(key, None)
    await tg_dispatcher.storage.set_data(key, {})
    return bot, session, tg_dispatcher, llm


async def feed(bot, dp, payload: dict) -> None:
    await dp.feed_update(bot, Update.model_validate(payload, context={"bot": bot}))


async def fsm(dp, bot):
    key = StorageKey(bot_id=bot.id, chat_id=CHAT_ID, user_id=USER_ID)
    return await dp.storage.get_state(key), await dp.storage.get_data(key)


def buttons(call) -> list[str]:
    markup = getattr(call, "reply_markup", None)
    return [b.callback_data for row in (markup.inline_keyboard if markup else []) for b in row]


# ------------------------------------------------------------------- commands
async def test_start_and_help_are_uzbek(bot_env):
    bot, session, dp, _ = bot_env
    await feed(bot, dp, message_update("/start"))
    await feed(bot, dp, message_update("/help"))
    assert session.texts()[0] == texts.START and "brief" in session.texts()[0].lower()
    assert "AI video" in session.texts()[1]


async def test_create_generates_default_script_with_buttons(bot_env):
    bot, session, dp, llm = bot_env
    await feed(bot, dp, message_update("/create"))
    assert session.method_names() == ["SendMessage", "EditMessageText"]
    assert session.texts()[0] == texts.THINKING
    assert buttons(session.calls[1]) == [CB_VIDEO_PROMPT, CB_EDIT_SCRIPT]
    # the default brief carries today's date and season into the prompt
    prompt = llm.calls[0]["user"]
    ctx = DateContext.now()
    assert ctx.month_uz in prompt and f"{ctx.today:%d.%m.%Y}" in prompt
    assert "BioLife" in llm.calls[0]["system"]
    _, data = await fsm(dp, bot)
    assert data["active_script"].startswith("🎬")


# -------------------------------------------------------- free-form briefs
async def test_free_text_becomes_a_brief(bot_env):
    """Regression: arbitrary text used to answer 'Bu buyruqni bilmayman'."""
    bot, session, dp, llm = bot_env
    await feed(bot, dp, message_update("Serum uchun yozgi kampaniya, 15 soniya"))
    assert texts.UNKNOWN not in session.texts()
    assert session.texts()[0] == texts.THINKING
    assert "Serum uchun yozgi kampaniya" in llm.calls[0]["user"]
    assert buttons(session.calls[1]) == [CB_VIDEO_PROMPT, CB_EDIT_SCRIPT]
    _, data = await fsm(dp, bot)
    assert data["active_brief"].startswith("Serum uchun")


async def test_brand_rules_reach_the_system_prompt(bot_env):
    bot, session, dp, llm = bot_env
    await feed(bot, dp, message_update("Heatwave kampaniya"))
    system = llm.calls[0]["system"]
    for rule in ("Coca-Cola", "medical", "Latin script", "Ramadan"):
        assert rule in system


async def test_overlong_brief_is_refused(bot_env):
    bot, session, dp, llm = bot_env
    await feed(bot, dp, message_update("x" * 2100))
    assert "Brief juda uzun" in session.texts()[0] and llm.calls == []


async def test_non_text_message_is_answered(bot_env):
    bot, session, dp, _ = bot_env
    payload = {"update_id": 77_001, "message": {
        "message_id": 1, "date": 1700000000, "chat": {"id": CHAT_ID, "type": "private"},
        "from": {"id": USER_ID, "is_bot": False, "first_name": "Bobur"},
        "photo": [{"file_id": "f", "file_unique_id": "u", "width": 10, "height": 10}]}}
    await feed(bot, dp, payload)
    assert session.texts() == [texts.TEXT_ONLY]


# ----------------------------------------------------------- video prompt button
async def test_video_prompt_button_uses_active_script(bot_env):
    bot, session, dp, llm = bot_env
    await feed(bot, dp, message_update("Yozgi kampaniya"))
    llm.calls.clear()
    await feed(bot, dp, callback_update(CB_VIDEO_PROMPT))
    assert "AnswerCallbackQuery" in session.method_names()
    assert "Runway" in llm.calls[0]["system"] and "Luma" in llm.calls[0]["system"]
    assert "camera" in llm.calls[0]["system"].lower() and "lighting" in llm.calls[0]["system"].lower()
    assert "🎬 BioLife" in llm.calls[0]["user"]                 # the script was passed in
    assert session.texts()[-1].startswith("<pre>")              # one-tap copy block
    assert buttons(session.calls[-1]) == []                     # no keyboard on the prompt itself


async def test_buttons_without_a_script_explain_themselves(bot_env):
    bot, session, dp, llm = bot_env
    await feed(bot, dp, callback_update(CB_VIDEO_PROMPT))
    await feed(bot, dp, callback_update(CB_EDIT_SCRIPT))
    assert session.texts().count(texts.NO_ACTIVE_SCRIPT) == 2 and llm.calls == []
    state, _ = await fsm(dp, bot)
    assert state is None


# ------------------------------------------------------------------ edit flow
async def test_edit_flow_end_to_end(bot_env):
    bot, session, dp, llm = bot_env
    await feed(bot, dp, message_update("Kuzgi to‘y mavsumi"))
    await feed(bot, dp, callback_update(CB_EDIT_SCRIPT))
    state, _ = await fsm(dp, bot)
    assert state == ContentStates.waiting_for_script_edits.state
    assert texts.ASK_EDITS in session.texts()

    llm.calls.clear()
    await feed(bot, dp, message_update("Hookni qisqartir, oilaviy dasturxon qo‘sh"))
    user_prompt = llm.calls[0]["user"]
    assert "Hookni qisqartir" in user_prompt and "Current script" in user_prompt
    assert "REVISING" in llm.calls[0]["system"]
    assert buttons(session.calls[-1]) == [CB_VIDEO_PROMPT, CB_EDIT_SCRIPT]   # keyboard re-attached
    state, data = await fsm(dp, bot)
    assert state is None and data["active_script"]


async def test_edit_mode_does_not_swallow_commands(bot_env):
    bot, session, dp, llm = bot_env
    await feed(bot, dp, message_update("Brief"))
    await feed(bot, dp, callback_update(CB_EDIT_SCRIPT))
    await feed(bot, dp, message_update("/cancel"))
    state, _ = await fsm(dp, bot)
    assert state is None and texts.CANCELLED in session.texts()


async def test_edits_without_script_reset_state(bot_env):
    bot, session, dp, llm = bot_env
    key = StorageKey(bot_id=bot.id, chat_id=CHAT_ID, user_id=USER_ID)
    await dp.storage.set_state(key, ContentStates.waiting_for_script_edits.state)
    await feed(bot, dp, message_update("qisqartir"))
    state, _ = await fsm(dp, bot)
    assert state is None and texts.NO_ACTIVE_SCRIPT in session.texts()


# --------------------------------------------------------------- failure paths
async def test_llm_error_is_shown_in_uzbek(bot_env):
    bot, session, dp, llm = bot_env
    llm._queue.append(LLMError("Anthropic rejected the API key (401)"))
    await feed(bot, dp, message_update("Brief"))
    assert "Ssenariy yaratilmadi" in session.texts()[1] and "401" in session.texts()[1]
    _, data = await fsm(dp, bot)
    assert "active_script" not in data                 # a failed run stores nothing


async def test_timeout_is_reported(bot_env, monkeypatch):
    bot, session, dp, llm = bot_env
    monkeypatch.setattr("app.telegram.handlers.GENERATION_TIMEOUT_S", 0.05)

    async def slow(*a, **kw):
        await asyncio.sleep(1)
    monkeypatch.setattr("app.telegram.handlers.generate_script", slow)
    await feed(bot, dp, message_update("Brief"))
    assert "timeout" in session.texts()[1]


async def test_second_request_while_running_is_refused(bot_env, monkeypatch):
    bot, session, dp, llm = bot_env
    gate = asyncio.Event()

    async def slow(*a, **kw):
        await gate.wait()
        return "🎬 tayyor"
    monkeypatch.setattr("app.telegram.handlers.generate_script", slow)
    first = asyncio.create_task(feed(bot, dp, message_update("Birinchi")))
    await asyncio.sleep(0.05)
    await feed(bot, dp, message_update("Ikkinchi"))
    assert texts.BUSY in session.texts()
    gate.set()
    await first
    from app.telegram.handlers import _running
    assert USER_ID not in _running


async def test_html_from_llm_is_escaped(bot_env):
    bot, session, dp, llm = bot_env
    llm._queue.append("<b>hack</b> & co")
    await feed(bot, dp, message_update("Brief"))
    assert "&lt;b&gt;hack&lt;/b&gt; &amp; co" in session.texts()[1]


async def test_long_script_is_split_and_keyboard_lands_on_the_last_part(bot_env):
    bot, session, dp, llm = bot_env
    llm._queue.append("\n\n".join("A" * 1000 for _ in range(8)))
    await feed(bot, dp, message_update("Brief"))
    assert session.method_names().count("SendMessage") >= 2
    assert all(len(t) <= 4096 for t in session.texts())
    assert buttons(session.calls[-1]) == [CB_VIDEO_PROMPT, CB_EDIT_SCRIPT]


# ------------------------------------------------------------------- helpers
async def test_split_keeps_chunks_within_limit():
    chunks = split_for_telegram("\n\n".join("x" * 1000 for _ in range(10)))
    assert len(chunks) > 1 and all(len(c) <= 4096 for c in chunks)


@pytest.mark.parametrize("month,expected", [(1, "winter"), (4, "spring"), (7, "summer"), (10, "autumn")])
async def test_season_mapping(month, expected):
    assert season_key(month) == expected


# ============================ review findings: regressions ============================
async def test_long_video_prompt_is_split_into_valid_pre_blocks(bot_env):
    """Each chunk must be a complete <pre>…</pre>: a split tag makes Telegram answer 400."""
    bot, session, dp, llm = bot_env
    await feed(bot, dp, message_update("Brief"))
    llm._queue.append("\n\n".join(f"SCENE {i} " + "word " * 200 for i in range(6)))
    await feed(bot, dp, callback_update(CB_VIDEO_PROMPT))
    blocks = [t for t in session.texts() if t.startswith("<pre>")]
    assert len(blocks) >= 2
    for block in blocks:
        assert block.startswith("<pre>") and block.endswith("</pre>") and len(block) <= 4096


async def test_html_entities_are_never_cut_in_half(bot_env):
    bot, session, dp, llm = bot_env
    llm._queue.append("a" * 4093 + "&" + "b" * 5000)
    await feed(bot, dp, message_update("Brief"))
    import re
    for text in session.texts()[1:]:
        assert len(text) <= 4096
        assert not re.search(r"&[a-z]{0,6}$", text)      # no dangling entity at the edge
        assert not re.match(r"^[a-z]{0,6};", text)


async def test_parse_error_falls_back_to_plain_text(bot_env):
    """A formatting problem must not cost the whole answer."""
    from tests.telegram_factories import RecordingSession
    bot, _, dp, llm = bot_env
    session = RecordingSession(fail_html=True)
    bot.session = session
    llm._queue.append("🎬 BioLife & Co <tag>")            # escaping produces entities
    await feed(bot, dp, message_update("Brief"))
    plain = [c for c in session.calls if getattr(c, "parse_mode", "keep") is None]
    assert plain and "BioLife & Co <tag>" in plain[-1].text


async def test_llm_error_containing_html_is_escaped(bot_env):
    bot, session, dp, llm = bot_env
    llm._queue.append(LLMError("Anthropic API error: 502 - <html>Bad Gateway</html>"))
    await feed(bot, dp, message_update("Brief"))
    assert "&lt;html&gt;" in session.texts()[1] and "<html>" not in session.texts()[1]


async def test_create_leaves_edit_mode(bot_env):
    """Regression: /create used to keep waiting_for_script_edits, so the next brief was
    silently treated as edit feedback."""
    bot, session, dp, llm = bot_env
    await feed(bot, dp, message_update("Birinchi brief"))
    await feed(bot, dp, callback_update(CB_EDIT_SCRIPT))
    await feed(bot, dp, message_update("/create"))
    state, _ = await fsm(dp, bot)
    assert state is None
    llm.calls.clear()
    await feed(bot, dp, message_update("Ikkinchi brief"))
    assert "REVISING" not in llm.calls[0]["system"]          # a brief, not an edit


async def test_unknown_command_costs_nothing(bot_env):
    bot, session, dp, llm = bot_env
    await feed(bot, dp, message_update("/status"))
    assert session.texts() == [texts.UNKNOWN] and llm.calls == []


async def test_script_is_stored_even_if_delivery_fails(bot_env):
    from tests.telegram_factories import RecordingSession
    bot, _, dp, llm = bot_env
    bot.session = RecordingSession(fail_html=True)
    llm._queue.append("🎬 BioLife & Co")
    await feed(bot, dp, message_update("Brief"))
    _, data = await fsm(dp, bot)
    assert data.get("active_script")


async def test_telegram_llm_model_override_is_used(bot_env):
    bot, session, dp, llm = bot_env
    dp["settings"] = dp["settings"].model_copy(update={"telegram_llm_model": "claude-3-5-sonnet-20240620",
                                                       "telegram_llm_max_tokens": 1234})
    captured: dict = {}

    async def spy(*, system, user, model=None, max_tokens=None):
        captured.update(model=model, max_tokens=max_tokens)
        return "🎬 ok"
    llm.generate_text = spy
    await feed(bot, dp, message_update("Brief"))
    assert captured == {"model": "claude-3-5-sonnet-20240620", "max_tokens": 1234}


async def test_busy_lock_is_atomic_for_concurrent_updates(bot_env, monkeypatch):
    """Two updates in flight at the same time must not both reach the (paid) LLM."""
    bot, session, dp, llm = bot_env
    gate = asyncio.Event()
    calls: list[int] = []

    async def slow(*a, **kw):
        calls.append(1)
        await gate.wait()
        return "🎬 tayyor"
    monkeypatch.setattr("app.telegram.handlers.generate_script", slow)
    tasks = [asyncio.create_task(feed(bot, dp, message_update(f"Brief {i}"))) for i in range(2)]
    await asyncio.sleep(0.05)
    gate.set()
    await asyncio.gather(*tasks)
    assert len(calls) == 1 and texts.BUSY in session.texts()
