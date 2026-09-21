"""Integration points for the content generator.

Both placeholders are async and must never raise on their own: handlers turn any failure into a
readable Uzbek message. Replace the bodies with real calls — the signatures stay the same.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from app.telegram.texts import MONTHS_UZ, SEASONS_UZ

log = logging.getLogger("biolife.telegram.services")

TASHKENT = timezone(timedelta(hours=5), "Asia/Tashkent")


def tashkent_now() -> datetime:
    """Local time in Tashkent. Uses the tz database when present, else a fixed +05:00 offset
    (Uzbekistan has no daylight saving, so the offset is correct year-round)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Tashkent"))
    except Exception:                                   # slim images ship without tzdata
        return datetime.now(TASHKENT)


def season_key(month: int) -> str:
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "autumn"


@dataclass(frozen=True, slots=True)
class DateContext:
    """Today, in the words the copywriter needs."""

    today: date
    month_number: int
    month_uz: str
    season_key: str
    season_uz: str

    @classmethod
    def now(cls) -> "DateContext":
        now = tashkent_now()
        key = season_key(now.month)
        return cls(today=now.date(), month_number=now.month, month_uz=MONTHS_UZ[now.month],
                   season_key=key, season_uz=SEASONS_UZ[key])

    def as_text(self) -> str:
        return f"{self.today:%d.%m.%Y} · {self.month_uz} · {self.season_uz}"


@dataclass(frozen=True, slots=True)
class ScriptLine:
    """One scene of the reel: the same line in two languages."""

    scene: str
    ru: str
    uz: str


@dataclass(frozen=True, slots=True)
class GeneratedScript:
    date_context: DateContext
    templates: str
    lines: list[ScriptLine] = field(default_factory=list)


# --------------------------------------------------------------------------- hooks
async def fetch_templates_from_db() -> str:
    """TODO: read the brand guidelines for today from PostgreSQL
    (biolife.resolve_ad_context() already returns tone, visual style and the SKU)."""
    await asyncio.sleep(0)                              # placeholder for the real query
    return "Tone: Energetic, Focus: Hydration, SKU: BioLife 0.5L PET"


async def generate_llm_script(season: str, templates: str) -> GeneratedScript:
    """TODO: call the LLM (see app/handlers/* for the real prompts) and parse its JSON answer.

    For now it returns a fixed two-language mock so the bot flow can be tested end to end.
    """
    ctx = DateContext.now()
    log.info("generate_llm_script season=%s templates=%r (mock)", season, templates)
    await asyncio.sleep(0)
    lines = [
        ScriptLine(scene="0-3 s · Hook",
                   ru="Жара не спрашивает. BioLife отвечает.",
                   uz="Issiq so‘ramaydi. BioLife javob beradi."),
        ScriptLine(scene="3-8 s · Tana",
                   ru="Один глоток — и день снова твой.",
                   uz="Bir qultum — va kun yana sizniki."),
        ScriptLine(scene="8-12 s · Mahsulot",
                   ru="BioLife 0.5L — чистая вода рядом.",
                   uz="BioLife 0.5L — toza suv yoningizda."),
        ScriptLine(scene="12-15 s · CTA",
                   ru="Возьми свою бутылку BioLife.",
                   uz="O‘z BioLife shishangizni oling."),
    ]
    return GeneratedScript(date_context=ctx, templates=templates, lines=lines)
