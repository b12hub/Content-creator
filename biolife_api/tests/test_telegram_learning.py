"""Phase 12 at the bot level: the 💾 approve button, few-shot injection and the storage choice.

Real update JSON goes through the real Dispatcher, exactly like tests/test_telegram_bot.py; the
database is a stub that records what the handler asked it to do.
"""
from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Update

from app.db import SavedScript
from app.llm.fake_client import FakeLLMClient
from app.telegram import texts
from app.telegram.keyboards import CB_SAVE_FINAL
from app.telegram.services import FEW_SHOT_HEADER
from app.telegram.states import KEY_SAVED, KEY_SCRIPT
from tests.telegram_factories import CHAT_ID, USER_ID, callback_update, make_bot, message_update

class StubDb:
    """A Database look-alike: no pool, but the same surface the handlers use."""

    def __init__(self, *, saves: bool = True, examples: list[SavedScript] | None = None,
                 templates: str = "Season: autumn | SKU: BioLife 1.5L PET") -> None:
        self._saves = saves
        self._examples = examples or []
        self._templates = templates
        self.saved: list[dict] = []
        self.enabled = True

    async def templates(self, **kw) -> str:
        return self._templates

    async def recent_scripts(self, limit=None) -> list[SavedScript]:
        return list(self._examples)

    async def save_script(self, *, user_id: int, brief, script: str) -> bool:
        self.saved.append({"user_id": user_id, "brief": brief, "script": script})
        return self._saves


@pytest_asyncio.fixture
async def env(tg_dispatcher):
    from app.config import get_settings
    from app.telegram import busy
    busy._running.clear()
    bot, session = make_bot()
    llm = FakeLLMClient()
    tg_dispatcher["llm"] = llm
    tg_dispatcher["settings"] = get_settings()
    tg_dispatcher["db"] = None
    key = StorageKey(bot_id=bot.id, chat_id=CHAT_ID, user_id=USER_ID)
    await tg_dispatcher.storage.set_state(key, None)
    await tg_dispatcher.storage.set_data(key, {})
    yield bot, session, tg_dispatcher, llm, key
    tg_dispatcher["db"] = None


async def feed(bot, dp, payload: dict) -> None:
    await dp.feed_update(bot, Update.model_validate(payload, context={"bot": bot}))


# ------------------------------------------------------------- the 💾 button
@pytest.mark.asyncio
async def test_approving_a_script_stores_it_with_its_brief(env):
    bot, session, dp, llm, key = env
    db = StubDb()
    dp["db"] = db
    await feed(bot, dp, message_update("Kuzgi to'y kampaniyasi"))
    await feed(bot, dp, callback_update(CB_SAVE_FINAL))
    assert db.saved == [{"user_id": USER_ID, "brief": "Kuzgi to'y kampaniyasi",
                         "script": (await dp.storage.get_data(key))[KEY_SCRIPT]}]
    assert session.texts()[-1] == texts.SAVED
    assert (await dp.storage.get_data(key))[KEY_SAVED] is True


@pytest.mark.asyncio
async def test_approving_twice_does_not_write_a_duplicate(env):
    bot, session, dp, llm, key = env
    db = StubDb()
    dp["db"] = db
    await feed(bot, dp, message_update("Brief"))
    await feed(bot, dp, callback_update(CB_SAVE_FINAL))
    await feed(bot, dp, callback_update(CB_SAVE_FINAL))
    assert len(db.saved) == 1
    assert session.texts()[-1] == texts.ALREADY_SAVED


@pytest.mark.asyncio
async def test_approving_without_a_database_tells_the_marketer(env):
    """No DATABASE_URL: the bot must say so instead of pretending it learned something."""
    bot, session, dp, llm, key = env
    dp["db"] = None
    await feed(bot, dp, message_update("Brief"))
    await feed(bot, dp, callback_update(CB_SAVE_FINAL))
    assert session.texts()[-1] == texts.SAVE_FAILED
    assert KEY_SAVED not in (await dp.storage.get_data(key)) or \
        (await dp.storage.get_data(key))[KEY_SAVED] is False


@pytest.mark.asyncio
async def test_a_failed_write_is_not_reported_as_success(env):
    bot, session, dp, llm, key = env
    dp["db"] = StubDb(saves=False)
    await feed(bot, dp, message_update("Brief"))
    await feed(bot, dp, callback_update(CB_SAVE_FINAL))
    assert session.texts()[-1] == texts.SAVE_FAILED
    assert (await dp.storage.get_data(key)).get(KEY_SAVED) is False


@pytest.mark.asyncio
async def test_approving_with_no_active_script_is_refused(env):
    bot, session, dp, llm, key = env
    db = StubDb()
    dp["db"] = db
    await feed(bot, dp, callback_update(CB_SAVE_FINAL))
    assert db.saved == []
    assert session.texts()[-1] == texts.NO_ACTIVE_SCRIPT


@pytest.mark.asyncio
async def test_a_new_generation_clears_the_saved_flag(env):
    """Regression: the second script must be approvable too."""
    bot, session, dp, llm, key = env
    dp["db"] = StubDb()
    await feed(bot, dp, message_update("Birinchi"))
    await feed(bot, dp, callback_update(CB_SAVE_FINAL))
    await feed(bot, dp, message_update("Ikkinchi"))
    assert (await dp.storage.get_data(key))[KEY_SAVED] is False
    await feed(bot, dp, callback_update(CB_SAVE_FINAL))
    assert len(dp["db"].saved) == 2


# -------------------------------------------------- few-shot learning loop
@pytest.mark.asyncio
async def test_saved_scripts_are_injected_into_the_next_system_prompt(env):
    bot, session, dp, llm, key = env
    dp["db"] = StubDb(examples=[SavedScript("Kuz", "🎬 TASDIQLANGAN MISOL")])
    await feed(bot, dp, message_update("Yangi brief"))
    system = llm.calls[0]["system"]
    assert FEW_SHOT_HEADER in system
    assert "🎬 TASDIQLANGAN MISOL" in system
    assert "BioLife" in system                      # the brand rules are still there


@pytest.mark.asyncio
async def test_without_saved_scripts_the_prompt_has_no_examples_block(env):
    bot, session, dp, llm, key = env
    dp["db"] = StubDb(examples=[])
    await feed(bot, dp, message_update("Yangi brief"))
    assert FEW_SHOT_HEADER not in llm.calls[0]["system"]


@pytest.mark.asyncio
async def test_the_database_guidelines_reach_the_user_prompt(env):
    """The brand line comes from biolife.resolve_ad_context(), not from the hard-coded baseline."""
    bot, session, dp, llm, key = env
    dp["db"] = StubDb(templates="Season: autumn | Event: To'y | SKU: BioLife 1.5L PET")
    await feed(bot, dp, message_update("Brief"))
    assert "To'y" in llm.calls[0]["user"]


@pytest.mark.asyncio
async def test_edits_also_follow_the_approved_examples(env):
    bot, session, dp, llm, key = env
    dp["db"] = StubDb(examples=[SavedScript("", "🎬 USLUB NAMUNASI")])
    await feed(bot, dp, message_update("Brief"))
    from app.telegram.keyboards import CB_EDIT_SCRIPT
    await feed(bot, dp, callback_update(CB_EDIT_SCRIPT))
    await feed(bot, dp, message_update("Qisqartir"))
    assert "🎬 USLUB NAMUNASI" in llm.calls[-1]["system"]


# ------------------------------------------------- storage and middleware order
def test_redis_is_used_when_a_url_is_configured(monkeypatch):
    from aiogram.fsm.storage.memory import MemoryStorage
    from app.config import Settings
    from app.telegram.bot import build_isolation, build_storage

    plain = Settings(redis_url="")
    assert isinstance(build_storage(plain), MemoryStorage)

    with_redis = Settings(redis_url="redis://localhost:6379/0")
    storage = build_storage(with_redis)
    from aiogram.fsm.storage.redis import RedisEventIsolation, RedisStorage
    assert isinstance(storage, RedisStorage)
    assert isinstance(build_isolation(with_redis), RedisEventIsolation)
    # from_url() only builds a lazy pool, but leaving it open warns at interpreter exit
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(storage.close())


def test_the_guards_run_before_the_fsm_isolation_lock(tg_dispatcher):
    """If they ran after it, a busy user's second message would queue behind the running
    generation and then start a second paid call."""
    from aiogram.fsm.middleware import FSMContextMiddleware
    from app.telegram.middlewares import (AccessMiddleware, BusyMiddleware, DedupeMiddleware,
                                          ThrottleMiddleware)

    chain = tg_dispatcher.update.outer_middleware._middlewares
    types = [type(mw) for mw in chain]
    fsm_at = types.index(FSMContextMiddleware)
    for guard in (DedupeMiddleware, ThrottleMiddleware, AccessMiddleware, BusyMiddleware):
        assert types.index(guard) < fsm_at, f"{guard.__name__} must run before the FSM lock"


@pytest.mark.asyncio
async def test_create_after_a_custom_brief_does_not_reuse_the_old_brief(env):
    """Corpus poisoning: /create makes a DEFAULT script, so the previous brief must not be
    saved next to it - every later generation would then learn a brief/script pair that never
    belonged together."""
    bot, session, dp, llm, key = env
    db = StubDb()
    dp["db"] = db
    await feed(bot, dp, message_update("BRIEF-ONE"))
    await feed(bot, dp, message_update("/create"))
    await feed(bot, dp, callback_update(CB_SAVE_FINAL))
    assert db.saved[0]["brief"] is None


@pytest.mark.asyncio
async def test_the_configured_few_shot_limit_is_actually_applied(env):
    """BIOLIFE_FEW_SHOT_MAX_CHARS was dead config: both call sites took the hard-coded default."""
    from app.config import Settings
    bot, session, dp, llm, key = env
    db = StubDb(examples=[SavedScript("", "x" * 5000)])
    db.settings = Settings(few_shot_max_chars=120)
    dp["db"] = db
    await feed(bot, dp, message_update("Brief"))
    system = llm.calls[0]["system"]
    assert "[...]" in system and system.count("x") < 200


@pytest.mark.asyncio
async def test_the_two_database_round_trips_run_concurrently(env):
    """Sequentially they sat in front of every LLM call and broke the 120s timeout budget."""
    import time
    bot, session, dp, llm, key = env

    class SlowDb(StubDb):
        async def templates(self, **kw):
            await asyncio.sleep(0.20)
            return "Season: autumn"

        async def recent_scripts(self, limit=None):
            await asyncio.sleep(0.20)
            return []

    dp["db"] = SlowDb()
    started = time.perf_counter()
    await feed(bot, dp, message_update("Brief"))
    assert time.perf_counter() - started < 0.35      # ~0.2s together, ~0.4s one after the other


@pytest.mark.asyncio
async def test_a_long_script_keeps_its_buttons_on_the_last_message(env):
    """A 20k-char answer splits into many messages; the 💾/✏️/🎬 keyboard must survive, and the
    FSM must hold the WHOLE script, not the last chunk."""
    bot, session, dp, llm, key = env
    long_script = "🎬 Uzun ssenariy\n" + ("Sahna matni. " * 1500)
    llm._queue.append(long_script)                   # the fake answers from this queue first
    dp["db"] = StubDb()
    await feed(bot, dp, message_update("Brief"))
    sends = [c for c in session.calls if type(c).__name__ in {"SendMessage", "EditMessageText"}]
    assert len(sends) > 3
    assert all(len(getattr(c, "text", "")) <= 4096 for c in sends)
    with_markup = [c for c in sends if getattr(c, "reply_markup", None) is not None]
    assert len(with_markup) == 1 and with_markup[0] is sends[-1]
    assert (await dp.storage.get_data(key))[KEY_SCRIPT] == long_script


def test_the_isolation_reuses_the_storage_redis_client():
    """from_url() twice builds two connection pools, and aiogram's RedisEventIsolation.close()
    is a no-op - the second pool could never be released."""
    from aiogram.fsm.storage.redis import RedisEventIsolation, RedisStorage
    from app.config import Settings
    from app.telegram.bot import build_isolation, build_storage

    settings = Settings(redis_url="redis://localhost:6379/0")
    storage = build_storage(settings)
    isolation = build_isolation(settings, storage)
    assert isinstance(storage, RedisStorage) and isinstance(isolation, RedisEventIsolation)
    assert isolation.redis is storage.redis
    asyncio.new_event_loop().run_until_complete(storage.close())
