"""Internal content-generator bot: /start, /create, access control, error paths.
Real Telegram update JSON goes through the real Dispatcher; outgoing API calls are captured."""
from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from aiogram.types import Update

from app.telegram import texts
from app.telegram.handlers import render_script, split_for_telegram
from app.telegram.services import DateContext, fetch_templates_from_db, generate_llm_script, season_key
from tests.telegram_factories import USER_ID, make_bot, message_update

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def bot_env(tg_dispatcher):
    bot, session = make_bot()
    return bot, session, tg_dispatcher


async def feed(bot, dp, payload: dict) -> None:
    await dp.feed_update(bot, Update.model_validate(payload, context={"bot": bot}))


# ------------------------------------------------------------------- commands
async def test_start_is_uzbek_and_points_to_create(bot_env):
    bot, session, dp = bot_env
    await feed(bot, dp, message_update("/start"))
    text = session.texts()[0]
    assert text == texts.START
    assert "/create" in text and "BioLife AI Kopirayter" in text


async def test_help_lists_only_the_two_commands(bot_env):
    bot, session, dp = bot_env
    await feed(bot, dp, message_update("/help"))
    text = session.texts()[0]
    assert "/create" in text and "/catalog" not in text and "/order" not in text


@pytest.mark.parametrize("command", ["/catalog", "/order", "/cancel"])
async def test_removed_ecommerce_commands_fall_through(bot_env, command):
    """The old shop commands must be gone, not silently working."""
    bot, session, dp = bot_env
    await feed(bot, dp, message_update(command))
    assert session.texts() == [texts.UNKNOWN]


async def test_all_bot_copy_is_uzbek_only():
    """No Cyrillic in the interface strings (the generated script itself is bilingual)."""
    import re
    ui = [v for k, v in vars(texts).items()
          if isinstance(v, str) and k.isupper() and k not in {"MONTHS_UZ", "SEASONS_UZ"}]
    assert ui and not any(re.search(r"[А-Яа-яЁё]", s) for s in ui)


# --------------------------------------------------------------------- /create
async def test_create_sends_thinking_then_edits_it(bot_env):
    bot, session, dp = bot_env
    await feed(bot, dp, message_update("/create"))
    assert session.method_names() == ["SendMessage", "EditMessageText"]
    assert session.texts()[0] == texts.THINKING
    result = session.texts()[1]
    assert "bugungi ssenariy" in result and "🇷🇺" in result and "🇺🇿" in result


async def test_create_includes_today_month_and_season(bot_env):
    bot, session, dp = bot_env
    ctx = DateContext.now()
    await feed(bot, dp, message_update("/create"))
    result = session.texts()[1]
    assert ctx.month_uz in result and ctx.season_uz in result and f"{ctx.today:%d.%m.%Y}" in result


async def test_create_uses_templates_from_db(bot_env, monkeypatch):
    bot, session, dp = bot_env
    seen: list[str] = []

    async def fake_templates() -> str:
        return "Tone: Calm, Focus: Family"

    async def fake_llm(season: str, templates: str):
        seen.append(f"{season}|{templates}")
        return await generate_llm_script(season, templates)

    monkeypatch.setattr("app.telegram.handlers.fetch_templates_from_db", fake_templates)
    monkeypatch.setattr("app.telegram.handlers.generate_llm_script", fake_llm)
    await feed(bot, dp, message_update("/create"))
    assert seen == [f"{DateContext.now().season_key}|Tone: Calm, Focus: Family"]
    assert "Tone: Calm" in session.texts()[1]


async def test_llm_failure_is_reported_in_uzbek(bot_env, monkeypatch):
    bot, session, dp = bot_env

    async def boom(season, templates):
        raise RuntimeError("llm down")
    monkeypatch.setattr("app.telegram.handlers.generate_llm_script", boom)
    await feed(bot, dp, message_update("/create"))
    assert session.texts()[0] == texts.THINKING
    assert "Ssenariy yaratilmadi" in session.texts()[1] and "RuntimeError" in session.texts()[1]


async def test_timeout_is_reported(bot_env, monkeypatch):
    bot, session, dp = bot_env
    monkeypatch.setattr("app.telegram.handlers.GENERATION_TIMEOUT_S", 0.05)

    async def slow(season, templates):
        await asyncio.sleep(1)
    monkeypatch.setattr("app.telegram.handlers.generate_llm_script", slow)
    await feed(bot, dp, message_update("/create"))
    assert "timeout" in session.texts()[1]


async def test_second_create_while_running_is_refused(bot_env, monkeypatch):
    bot, session, dp = bot_env
    gate = asyncio.Event()

    async def slow(season, templates):
        await gate.wait()
        return await generate_llm_script(season, templates)
    monkeypatch.setattr("app.telegram.handlers.generate_llm_script", slow)

    first = asyncio.create_task(feed(bot, dp, message_update("/create")))
    await asyncio.sleep(0.05)
    await feed(bot, dp, message_update("/create"))
    assert texts.BUSY in session.texts()
    gate.set()
    await first
    from app.telegram.handlers import _running
    assert USER_ID not in _running                  # lock always released


async def test_long_script_is_split_into_several_messages(bot_env, monkeypatch):
    bot, session, dp = bot_env
    from app.telegram.services import GeneratedScript, ScriptLine
    long_lines = [ScriptLine(scene=f"Sahna {i}", ru="Р" * 300, uz="U" * 300) for i in range(12)]

    async def big(season, templates):
        return GeneratedScript(date_context=DateContext.now(), templates=templates, lines=long_lines)
    monkeypatch.setattr("app.telegram.handlers.generate_llm_script", big)
    await feed(bot, dp, message_update("/create"))
    assert session.method_names().count("SendMessage") >= 2
    assert all(len(t) <= 4096 for t in session.texts())


# ------------------------------------------------------------------- helpers
async def test_split_keeps_chunks_within_limit():
    text = "\n\n".join("x" * 1000 for _ in range(10))
    chunks = split_for_telegram(text)
    assert len(chunks) > 1 and all(len(c) <= 4096 for c in chunks)
    assert "".join(c.replace("\n\n", "") for c in chunks).count("x") == 10_000


async def test_html_in_script_is_escaped():
    from app.telegram.services import GeneratedScript, ScriptLine
    script = GeneratedScript(date_context=DateContext.now(), templates="<b>hack</b>",
                             lines=[ScriptLine(scene="s", ru="<script>", uz="a & b")])
    out = render_script(script)
    assert "&lt;script&gt;" in out and "&lt;b&gt;hack" in out and "a &amp; b" in out


@pytest.mark.parametrize("month,expected", [(1, "winter"), (3, "spring"), (7, "summer"),
                                            (9, "autumn"), (11, "autumn"), (12, "winter")])
async def test_season_mapping(month, expected):
    assert season_key(month) == expected


async def test_fetch_templates_placeholder_returns_guidelines():
    assert "Tone:" in await fetch_templates_from_db()


# -------------------------------------------------------------- access control
async def test_allowlist_blocks_strangers():
    from aiogram.types import Update as U

    from app.config import Settings
    from app.telegram.middlewares import AccessMiddleware
    calls: list[int] = []

    async def handler(event, data):
        calls.append(1)

    bot, session = make_bot()
    update = U.model_validate(message_update("/create"), context={"bot": bot})
    mw = AccessMiddleware(frozenset({USER_ID + 1}))
    await mw(handler, update, {"event_from_user": update.message.from_user})
    assert calls == [] and session.texts() == [texts.NO_ACCESS]

    allowed = AccessMiddleware(frozenset({USER_ID}))
    await allowed(handler, update, {"event_from_user": update.message.from_user})
    assert calls == [1]
    assert Settings().telegram_allowed_user_ids == []      # default is open, with a startup warning
