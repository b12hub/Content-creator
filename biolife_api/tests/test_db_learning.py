"""Phase 12: PostgreSQL wiring, the few-shot 'learning' loop and the Redis-backed FSM.

The database layer is tested against a fake asyncpg pool, so the suite needs no live PostgreSQL
while still proving the exact SQL, the argument order and - most importantly - that every failure
path degrades instead of taking the bot down.
"""
from __future__ import annotations

import json

import asyncpg
import pytest

from app.config import Settings
from app.db import BASELINE_TEMPLATES, Database, SavedScript, format_templates
from app.telegram.services import (FEW_SHOT_HEADER, build_system_prompt, fetch_templates_from_db)

# --------------------------------------------------------------------- fakes
class FakePool:
    """Records every query; each method can be told to raise instead."""

    def __init__(self, *, fetchval=None, fetch=None, raises: Exception | None = None) -> None:
        self._fetchval = fetchval
        self._fetch = fetch if fetch is not None else []
        self._raises = raises
        self.queries: list[tuple[str, tuple]] = []
        self.closed = False

    async def fetchval(self, query, *args):
        self.queries.append((query, args))
        if self._raises:
            raise self._raises
        return self._fetchval

    async def fetch(self, query, *args):
        self.queries.append((query, args))
        if self._raises:
            raise self._raises
        return self._fetch

    async def execute(self, query, *args):
        self.queries.append((query, args))
        if self._raises:
            raise self._raises
        return "INSERT 0 1"

    def acquire(self):
        pool = self

        class _Acquire:
            async def __aenter__(self):
                return pool

            async def __aexit__(self, *exc):
                return False
        return _Acquire()

    async def close(self):
        self.closed = True


def make_db(pool=None, **overrides) -> Database:
    settings = Settings(**({"database_url": "postgresql://u:p@localhost/biolife"} | overrides))
    db = Database(settings)
    db._pool = pool                                  # noqa: SLF001 - the point of the fake
    return db


CONTEXT = {
    "season": "autumn",
    "resolved_temperature_c": 24.4,
    "recommended_sku": "BioLife 1.5L PET",
    "primary_event": {"name_uz": "To'y mavsumi", "name_ru": "Сезон свадеб"},
    "framework": {"narrative_tone": "warm, family-first"},
    "dishes": [{"name_uz": "Osh"}],
}


# ------------------------------------------------------------- disabled database
@pytest.mark.asyncio
async def test_every_call_is_a_safe_noop_without_a_database():
    """No DATABASE_URL must never raise: the bot keeps working on the static baseline."""
    db = make_db(pool=None, database_url="")
    assert db.enabled is False
    assert await db.ad_context() is None
    assert await db.templates() == BASELINE_TEMPLATES
    assert await db.save_script(user_id=1, brief="b", script="🎬 Kuzgi ssenariy — to'y mavsumi uchun BioLife") is False
    assert await db.recent_scripts() == []
    await db.close()                                  # must not raise either


@pytest.mark.asyncio
async def test_connect_without_a_url_does_not_touch_asyncpg(monkeypatch):
    called = False

    async def boom(*a, **kw):                         # pragma: no cover - must not run
        nonlocal called
        called = True
    monkeypatch.setattr(asyncpg, "create_pool", boom)
    db = make_db(pool=None, database_url="")
    await db.connect()
    assert called is False and db.enabled is False


@pytest.mark.asyncio
async def test_connect_survives_a_dead_server(monkeypatch, caplog):
    """A database outage at startup must not stop the bot from booting."""
    async def refuse(*a, **kw):
        raise OSError("connection refused")
    monkeypatch.setattr(asyncpg, "create_pool", refuse)
    db = make_db(pool=None)
    await db.connect()
    assert db.enabled is False


def test_the_password_is_never_logged():
    safe = Database._safe_dsn("postgresql://bobur:SuperSecret@10.0.0.1:5432/biolife")
    assert "SuperSecret" not in safe and "10.0.0.1:5432/biolife" in safe


# ----------------------------------------------------------------- ad_context
@pytest.mark.asyncio
async def test_ad_context_parses_the_json_returned_by_the_function():
    pool = FakePool(fetchval=json.dumps(CONTEXT))
    db = make_db(pool)
    context = await db.ad_context(temperature_c=24.4, dish_key="osh")
    assert context["recommended_sku"] == "BioLife 1.5L PET"
    query, args = pool.queries[0]
    assert "biolife.resolve_ad_context(CURRENT_DATE" in query
    assert args == (24.4, "osh")                      # temperature first, dish second


@pytest.mark.asyncio
async def test_templates_fall_back_to_the_baseline_when_the_query_fails():
    db = make_db(FakePool(raises=asyncpg.PostgresError("boom")))
    assert await db.templates() == BASELINE_TEMPLATES


@pytest.mark.asyncio
async def test_templates_fall_back_when_the_function_returns_null():
    assert await make_db(FakePool(fetchval=None)).templates() == BASELINE_TEMPLATES


def test_format_templates_is_a_single_compact_line():
    line = format_templates(CONTEXT)
    assert "\n" not in line
    assert len(line) < 250                            # ~40 tokens, not the ~1400-token payload
    for fragment in ("Season: autumn", "Temperature: +24C", "To'y mavsumi",
                     "warm, family-first", "BioLife 1.5L PET", "Osh"):
        assert fragment in line


def test_format_templates_tolerates_a_half_empty_context():
    line = format_templates({"season": "winter"})
    assert "Season: winter" in line and "Event: -" in line and "Temperature" not in line


# ---------------------------------------------------------------- save_script
@pytest.mark.asyncio
async def test_save_script_writes_the_expected_row():
    pool = FakePool()
    script = "🎬 Kuzgi ssenariy — to'y mavsumi uchun BioLife"
    assert await make_db(pool).save_script(user_id=777, brief="Kuz", script=script) is True
    query, args = pool.queries[0]
    assert "INSERT INTO biolife.saved_scripts" in query
    assert "ON CONFLICT DO NOTHING" in query          # a second 💾 tap must not duplicate the row
    assert args == (777, "Kuz", script)


@pytest.mark.asyncio
async def test_save_script_returns_false_instead_of_raising():
    """The marketer gets 'could not save', the bot stays alive."""
    db = make_db(FakePool(raises=asyncpg.PostgresError("relation does not exist")))
    assert await db.save_script(user_id=1, brief=None, script="🎬 Kuzgi ssenariy — to'y mavsumi uchun BioLife") is False


@pytest.mark.asyncio
async def test_recent_scripts_reads_the_newest_active_rows():
    pool = FakePool(fetch=[{"brief": "b1", "script": "s1"}, {"brief": None, "script": "s2"}])
    db = make_db(pool)
    scripts = await db.recent_scripts()
    assert scripts == [SavedScript("b1", "s1"), SavedScript("", "s2")]
    query, args = pool.queries[0]
    # id, not created_at: now() is transaction-stable, so created_at can tie
    assert "WHERE is_active ORDER BY id DESC LIMIT" in query
    assert args == (3,)                               # few_shot_limit default


@pytest.mark.asyncio
async def test_recent_scripts_returns_nothing_on_a_database_error():
    db = make_db(FakePool(raises=asyncpg.PostgresError("timeout")))
    assert await db.recent_scripts() == []


# ------------------------------------------------------------ few-shot prompt
def test_no_examples_leaves_the_system_prompt_untouched():
    assert build_system_prompt("BASE", []) == "BASE"


def test_examples_are_appended_under_the_uzbek_header_newest_first():
    prompt = build_system_prompt("BASE", [SavedScript("Kuz", "AAA"), SavedScript("", "BBB")])
    assert prompt.startswith("BASE")
    assert FEW_SHOT_HEADER in prompt
    assert prompt.index("--- MISOL 1 ---") < prompt.index("--- MISOL 2 ---")
    assert prompt.index("AAA") < prompt.index("BBB")   # the newest row comes first
    assert "Brief: Kuz" in prompt
    assert prompt.count("Brief:") == 1                 # the empty brief adds no line


def test_a_long_example_is_trimmed_so_the_prompt_cannot_grow_without_limit():
    prompt = build_system_prompt("BASE", [SavedScript("", "x" * 5000)], max_chars=100)
    assert "[...]" in prompt and len(prompt) < 400


@pytest.mark.asyncio
async def test_fetch_templates_without_a_database_returns_the_baseline():
    assert await fetch_templates_from_db(None) == BASELINE_TEMPLATES


@pytest.mark.asyncio
async def test_fetch_templates_uses_the_database_when_it_answers():
    db = make_db(FakePool(fetchval=json.dumps(CONTEXT)))
    assert "BioLife 1.5L PET" in await fetch_templates_from_db(db)


# ------------------------------------------- failure classes asyncpg really raises
# These are the ones the first version of db.py did NOT catch. asyncpg puts them in three
# unrelated hierarchies, so a single `except asyncpg.PostgresError` let them escape:
#   ClientConfigurationError -> InterfaceError -> ValueError   (a typo'd DSN)
#   asyncio.TimeoutError                                       (command_timeout fired)
#   ConnectionResetError -> OSError                            (the server went away)
REAL_FAILURES = [
    asyncpg.exceptions.ClientConfigurationError("invalid DSN"),
    asyncpg.InterfaceError("pool is closing"),
    __import__("asyncio").TimeoutError(),
    ConnectionResetError("server closed the connection"),
    asyncpg.PostgresError("relation does not exist"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", REAL_FAILURES, ids=lambda e: type(e).__name__)
async def test_no_failure_class_escapes_a_query(failure):
    """The contract is 'a database outage degrades the copy, it never takes the bot down'."""
    db = make_db(FakePool(raises=failure))
    assert await db.ad_context() is None
    assert await db.templates() == BASELINE_TEMPLATES
    assert await db.recent_scripts() == []
    assert await db.save_script(user_id=1, brief=None, script="x" * 50) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", REAL_FAILURES, ids=lambda e: type(e).__name__)
async def test_no_failure_class_stops_startup(monkeypatch, failure):
    """connect() runs first in the FastAPI lifespan: a raise here kills /health and the whole API,
    not just the bot. A typo in DATABASE_URL must never do that."""
    async def refuse(*a, **kw):
        raise failure
    monkeypatch.setattr(asyncpg, "create_pool", refuse)
    db = make_db(pool=None)
    await db.connect()                                # must not raise
    assert db.enabled is False


@pytest.mark.asyncio
async def test_a_malformed_table_name_is_refused_before_any_sql(monkeypatch):
    """saved_scripts_table is interpolated into SQL, so it is validated as an identifier."""
    called = False

    async def create_pool(*a, **kw):                  # pragma: no cover - must not run
        nonlocal called
        called = True
    monkeypatch.setattr(asyncpg, "create_pool", create_pool)
    db = make_db(pool=None, saved_scripts_table="saved_scripts; DROP TABLE users")
    await db.connect()
    assert called is False and db.enabled is False


# ------------------------------------------------------- guards before the INSERT
@pytest.mark.asyncio
@pytest.mark.parametrize("script", ["too short", "x" * 20_001])
async def test_a_script_the_check_constraint_would_reject_never_reaches_the_database(script):
    """Otherwise a truncated answer is reported to the marketer as 'the database is down'."""
    pool = FakePool()
    assert await make_db(pool).save_script(user_id=1, brief=None, script=script) is False
    assert pool.queries == []


@pytest.mark.asyncio
async def test_a_broken_ad_context_payload_falls_back_instead_of_crashing():
    """format_templates() assumes a shape; a changed resolve_ad_context() must not kill a
    generation."""
    db = make_db(FakePool(fetchval=json.dumps({"dishes": "not-a-list",
                                               "resolved_temperature_c": "warm"})))
    assert await db.templates() == BASELINE_TEMPLATES


@pytest.mark.asyncio
async def test_a_non_json_answer_falls_back():
    assert await make_db(FakePool(fetchval=12345)).ad_context() is None


# ------------------------------------------------------------- prompt size limits
def test_the_brief_is_trimmed_too_not_only_the_script():
    """Briefs are accepted up to 2000 chars and the block is paid for on EVERY call."""
    prompt = build_system_prompt("BASE", [SavedScript("b" * 2000, "s" * 100)], max_chars=1500)
    assert prompt.count("b") < 400


def test_the_few_shot_block_is_bounded_by_the_configured_limit():
    examples = [SavedScript("", "x" * 9000) for _ in range(3)]
    small = build_system_prompt("BASE", examples, max_chars=200)
    large = build_system_prompt("BASE", examples, max_chars=1500)
    assert len(small) < len(large) < 3 * 1600 + 200


# --------------------------------------------------------------- lifespan wiring
@pytest.mark.asyncio
async def test_the_pool_is_closed_when_the_app_shuts_down():
    """A leaked pool holds PostgreSQL connections open after every reload."""
    from fastapi import FastAPI

    from app.main import lifespan
    pool = FakePool()
    app = FastAPI(lifespan=lifespan)
    async with lifespan(app):
        app.state.db._pool = pool                     # noqa: SLF001 - stand in for a real pool
        assert app.state.db.enabled is True
    assert pool.closed is True
    assert app.state.db.enabled is False


@pytest.mark.asyncio
async def test_health_reports_the_parts_that_fail_silently():
    from fastapi import FastAPI

    from app.main import health, lifespan
    app = FastAPI(lifespan=lifespan)
    async with lifespan(app):
        body = await health()
    assert set(body) >= {"status", "database", "saved_scripts_table", "ad_context_function",
                         "telegram_webhook"}
    assert body["database"] is False                  # no DATABASE_URL in the test environment
    assert body["status"] == "ok"                     # ...which is a supported mode, not a fault
