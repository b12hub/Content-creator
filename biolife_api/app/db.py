"""PostgreSQL access for the bot (asyncpg).

Three jobs:
    ad_context()      -> today's brand guidelines, from biolife.resolve_ad_context()
    save_script()     -> stores an approved brief + script (the "learning" corpus)
    recent_scripts()  -> the last N approved scripts, injected as few-shot examples

Design rule: the database is an ENHANCEMENT, never a hard dependency. Every method catches its
own errors and returns a safe default, so a database outage degrades the copy quality instead of
taking the bot down. Configure with BIOLIFE_DATABASE_URL (or DATABASE_URL); leave it empty and the
bot runs on the static baseline exactly as before.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import asyncpg

from app.config import Settings

log = logging.getLogger("biolife.db")

# Everything a live PostgreSQL can throw at us. asyncpg does NOT put these under one base class:
#   PostgresError        - the server said no (missing table, CHECK violation, ...)
#   InterfaceError       - client-side: bad DSN, closed pool, closed connection during a failover
#   OSError              - network down, DNS failure, connect timeout (TimeoutError is an OSError)
#   asyncio.TimeoutError - command_timeout fired; asyncpg raises the asyncio class, not its own
#   ValueError           - nonsensical pool arguments (min_size > max_size)
# Catching only PostgresError - the first version of this file - let a typo'd DSN take down the
# whole FastAPI app at startup, and let a 10s query timeout escape into the Telegram handlers.
DB_ERRORS = (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, asyncio.TimeoutError,
             ValueError)

# schema.table / table - anything else is a configuration mistake, and interpolating it into SQL
# would at best produce a confusing syntax error at save time.
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")

MIN_SCRIPT_CHARS = 20           # must match the CHECK in biolife_db/05_saved_scripts.sql
MAX_SCRIPT_CHARS = 20_000

BASELINE_TEMPLATES = ("Tone: Energetic, Focus: Hydration, SKU: BioLife 0.5L PET / 1.5L PET, "
                      "Audience: Uzbekistan, 18-45, urban")


@dataclass(frozen=True, slots=True)
class SavedScript:
    brief: str
    script: str


class Database:
    """Thin wrapper over an asyncpg pool. Safe to use when `enabled` is False (all calls no-op)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings        # public: services.py reads the few-shot limits from it
        self._pool: asyncpg.Pool | None = None
        self.table_ready = False        # surfaced by /health: the feature is off without it
        self.function_ready = False

    @property
    def enabled(self) -> bool:
        return self._pool is not None

    # ------------------------------------------------------------------ lifecycle
    async def connect(self) -> None:
        table = self.settings.saved_scripts_table
        if not IDENTIFIER.match(table):
            log.error("BIOLIFE_SAVED_SCRIPTS_TABLE=%r is not a plain [schema.]table name - "
                      "refusing to use it in SQL. Saving scripts stays off.", table)
            return
        url = self.settings.database_url
        if not url:
            log.warning("BIOLIFE_DATABASE_URL is empty - the bot uses the static brand baseline "
                        "and cannot save approved scripts")
            return
        try:
            self._pool = await asyncpg.create_pool(
                dsn=url, min_size=self.settings.db_pool_min_size,
                max_size=self.settings.db_pool_max_size,
                command_timeout=self.settings.db_command_timeout_s)
        except DB_ERRORS as exc:
            log.error("PostgreSQL unavailable (%s: %s) - continuing without it",
                      type(exc).__name__, exc)
            self._pool = None
            return
        log.info("PostgreSQL pool ready (%s)", self._safe_dsn(url))
        await self._check_schema()

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @staticmethod
    def _safe_dsn(url: str) -> str:
        """Never log the password."""
        if "@" in url:
            return url.split("@", 1)[0].split("://", 1)[0] + "://***@" + url.split("@", 1)[1]
        return url

    async def _check_schema(self) -> None:
        """Say at startup what is missing, instead of failing on the first save."""
        assert self._pool is not None
        table = self.settings.saved_scripts_table
        try:
            exists = await self._pool.fetchval("SELECT to_regclass($1) IS NOT NULL", table)
            function = await self._pool.fetchval(
                "SELECT to_regprocedure('biolife.resolve_ad_context(date,numeric,text,text)') "
                "IS NOT NULL")
        except DB_ERRORS as exc:
            log.warning("schema check failed (%s) - continuing", exc)
            return
        self.table_ready = bool(exists)
        self.function_ready = bool(function)
        if not exists:
            log.error("table %s is missing - run biolife_db/05_saved_scripts.sql. Saving scripts "
                      "and few-shot examples stay off until then.", table)
        if not function:
            log.warning("biolife.resolve_ad_context() is missing - run biolife_db/02_functions.sql. "
                        "The bot falls back to the static brand baseline.")

    # ------------------------------------------------------------------- queries
    async def ad_context(self, *, temperature_c: float | None = None,
                         dish_key: str | None = None) -> dict[str, Any] | None:
        """One call to the Phase-1 function; None when the database is off or the call fails."""
        if self._pool is None:
            return None
        try:
            row = await self._pool.fetchval(
                "SELECT biolife.resolve_ad_context(CURRENT_DATE, $1::float8::numeric, $2::text, NULL)",
                temperature_c, dish_key)
        except DB_ERRORS as exc:
            log.warning("resolve_ad_context() failed (%s: %s) - using the baseline",
                        type(exc).__name__, exc)
            return None
        if row is None:
            return None
        try:
            return json.loads(row) if isinstance(row, str) else dict(row)
        except (TypeError, ValueError) as exc:
            log.warning("resolve_ad_context() returned something unexpected (%s) - baseline", exc)
            return None

    async def templates(self, *, temperature_c: float | None = None,
                        dish_key: str | None = None) -> str:
        """The brand guidelines line that goes into the prompt."""
        context = await self.ad_context(temperature_c=temperature_c, dish_key=dish_key)
        if not context:
            return BASELINE_TEMPLATES
        try:
            return format_templates(context)
        except (AttributeError, TypeError, ValueError, IndexError) as exc:
            # a changed resolve_ad_context() payload must degrade the copy, not stop a generation
            log.warning("unexpected ad context shape (%s) - using the baseline", exc)
            return BASELINE_TEMPLATES

    async def save_script(self, *, user_id: int, brief: str | None, script: str) -> bool:
        """False means 'not saved' - the caller tells the marketer, the bot keeps running."""
        if self._pool is None:
            return False
        if not MIN_SCRIPT_CHARS <= len(script) <= MAX_SCRIPT_CHARS:
            # mirrors the CHECK in 05_saved_scripts.sql, so a truncated answer is not reported
            # to the marketer as "the database is down"
            log.warning("script rejected before the INSERT: %d chars", len(script))
            return False
        table = self.settings.saved_scripts_table
        try:
            # command_timeout bounds the QUERY, not the wait for a free connection: without this
            # timeout a saturated pool would hang the webhook task forever, and the busy lock
            # (released in a finally) would keep that marketer blocked until a restart.
            async with asyncio.timeout(self.settings.db_command_timeout_s * 2):
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        f"INSERT INTO {table} (telegram_user_id, brief, script) VALUES ($1, $2, $3)"
                        " ON CONFLICT DO NOTHING",
                        user_id, brief, script)
        except DB_ERRORS as exc:
            log.error("could not save the script (%s): %s", type(exc).__name__, exc)
            return False
        log.info("script saved user_id=%s chars=%d", user_id, len(script))
        return True

    async def recent_scripts(self, limit: int | None = None) -> list[SavedScript]:
        """Approved scripts, newest first - the few-shot corpus."""
        if self._pool is None:
            return []
        table = self.settings.saved_scripts_table
        count = limit or self.settings.few_shot_limit
        try:
            async with asyncio.timeout(self.settings.db_command_timeout_s * 2):
                rows = await self._pool.fetch(
                    f"SELECT brief, script FROM {table} WHERE is_active "
                    f"ORDER BY id DESC LIMIT $1", count)
        except DB_ERRORS as exc:
            log.warning("could not read saved scripts (%s: %s) - generating without examples",
                        type(exc).__name__, exc)
            return []
        return [SavedScript(brief=r["brief"] or "", script=r["script"]) for r in rows]


def format_templates(context: dict[str, Any]) -> str:
    """resolve_ad_context() JSON -> one compact line for the system prompt.

    Only the fields the copywriter needs are taken: the raw payload is ~1400 tokens, this is ~40.
    """
    framework = (context.get("framework") or {})
    event = (context.get("primary_event") or {})
    dishes = context.get("dishes") or []
    parts = [
        f"Season: {context.get('season', '-')}",
        f"Event: {event.get('name_uz') or event.get('name_ru') or '-'}",
        f"Tone: {framework.get('narrative_tone', '-')}",
        f"SKU: {context.get('recommended_sku', '-')}",
        f"Dish: {dishes[0].get('name_uz') if dishes else '-'}",
        "Audience: Uzbekistan, 18-45, urban",
    ]
    if (temp := context.get("resolved_temperature_c")) is not None:
        parts.insert(1, f"Temperature: {temp:+.0f}C")
    return " | ".join(parts)
