"""Real Anthropic calls for the BioLife copywriter bot.

Three jobs, three prompts:
    generate_script()       brief (or today's default) -> bilingual RU/UZ Reel script
    generate_video_prompt() script                     -> copy-paste prompt for AI video tools
    revise_script()         script + feedback          -> updated script

All of them use llm.generate_text() (plain text, no JSON schema). That is deliberate: structured
outputs need Sonnet 4.5+/Opus 4.5+/Haiku 4.5, while plain text also runs on older models such as
claude-3-5-sonnet-20240620 (set BIOLIFE_TELEGRAM_LLM_MODEL to pin one just for the bot).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app.llm.base import LLMClient
from app.llm.fallback import GenerationResult
from app.telegram.texts import MONTHS_UZ, SEASONS_UZ

log = logging.getLogger("biolife.telegram.services")

TASHKENT = timezone(timedelta(hours=5), "Asia/Tashkent")


def tashkent_now() -> datetime:
    """Local time in Tashkent (no daylight saving, so a fixed +05:00 fallback is exact)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Tashkent"))
    except Exception:                       # slim images ship without tzdata
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


# ----------------------------------------------------------------- brand context
async def fetch_templates_from_db() -> str:
    """TODO: read today's guidelines from PostgreSQL — biolife.resolve_ad_context() already returns
    tone, visual style, dish pairing and SKU. Until then, a stable brand baseline."""
    await asyncio.sleep(0)
    return ("Tone: Energetic, Focus: Hydration, SKU: BioLife 0.5L PET / 1.5L PET, "
            "Audience: Uzbekistan, 18-45, urban")


SEASON_HINTS: dict[str, str] = {
    "summer": "Extreme heat (+38-45°C), thirst and instant cold relief dominate.",
    "autumn": "Wedding season, harvest, warm days and cool nights; family tables.",
    "winter": "Cold, weak thirst signal; hydration as a daily habit, indoor family meals.",
    "spring": "Navruz, blossoms, renewal and first outdoor gatherings.",
}

BRAND_SYSTEM_PROMPT = """You are the senior copywriter of BioLife drinking water (Imir Trade Group, Uzbekistan).
You write Instagram Reels scripts for the Uzbek market.

HARD RULES
- The brand is written exactly "BioLife". Never mention Coca-Cola, Pepsi or any other brand.
- No medical or health claims (no "cures", "detox", "improves digestion", "boosts immunity").
  Describe sensations only: cool, fresh, clean taste, relief, lightness.
- No alcohol, no smoking. Family-safe, halal context, modest clothing, respect for elders.
- Never invent facts about the product: source, minerals, pH, awards, prices, delivery times.
- Never quote the Qur'an or hadith. During Ramadan: no eating or drinking on screen in daylight.
- Do not mock the green-tea tradition: BioLife stands next to the choynak, never replaces it.

OUTPUT FORMAT (plain text, no markdown tables, no preamble, no closing remarks)
🎬 <short title>
📅 <date · month · season, copied from the brief>
🎯 <the creative angle in one Uzbek sentence>

Then 4-5 scenes, each exactly:
<start>-<end>s · <scene name in Uzbek>
🎥 <shot instruction in Uzbek: framing, light, camera move, fps>
🇷🇺 <voiceover line in Russian>
🇺🇿 <voiceover line in Uzbek, Latin script only>
🔊 <sound/ASMR/music note in Uzbek>

Finish with one line:
📣 CTA: 🇷🇺 <call to action> | 🇺🇿 <call to action>

Total reel length 15-30 seconds. Uzbek text is LATIN script only (o‘, g‘, sh, ch) - never Cyrillic.
Keep every voiceover line under 12 words."""

VIDEO_PROMPT_SYSTEM = """You are a technical director for AI video generators
(Runway Gen-3, Luma Dream Machine, Sora, Midjourney).

Turn the given Reel script into ONE copy-paste ready English prompt per scene.

Each prompt must specify, in this order:
subject and wardrobe, action/motion, camera (shot size, lens in mm, movement, speed/fps),
lighting (source, direction, temperature), colour palette and grade, environment details,
mood, style references (film stock / lens character), and negative cues.

RULES
- English only. No markdown headers, no explanations, no numbering beyond "SCENE n".
- One paragraph per scene, 45-80 words, written as a single comma-separated prompt string.
- Photoreal, 9:16 vertical, no on-screen text, no logos, no watermarks, no distorted hands.
- Respect the setting: Uzbekistan, family-safe, modest clothing.

FORMAT
SCENE 1 (0-3s)
<prompt>

SCENE 2 (3-8s)
<prompt>
..."""

EDIT_SYSTEM_PROMPT = BRAND_SYSTEM_PROMPT + """

You are now REVISING an existing script. Apply the requested changes, keep everything the editor did
not ask to change, and return the complete updated script in exactly the same output format."""


def _default_brief(date_context: DateContext, templates: str) -> str:
    return (f"Today's default BioLife campaign.\n"
            f"Date: {date_context.as_text()}\n"
            f"Season context: {SEASON_HINTS.get(date_context.season_key, '')}\n"
            f"Brand guidelines: {templates}\n"
            f"Goal: one Instagram Reel script for the daily content plan.")


async def generate_with_fallback(llm: LLMClient, system_prompt: str, user_prompt: str, *,
                                 model: str | None = None,
                                 max_tokens: int | None = None) -> GenerationResult:
    """Single entry point for every bot generation.

    With a FallbackLLMClient this tries Anthropic and, on an API failure, OpenRouter; the result
    says which provider answered so the bot can mark a degraded answer. With a plain client it is
    a normal call whose result is reported as non-degraded."""
    generate = getattr(llm, "generate", None)
    if generate is not None:                      # FallbackLLMClient
        return await generate(system=system_prompt, user=user_prompt, model=model,
                              max_tokens=max_tokens)
    text = await llm.generate_text(system=system_prompt, user=user_prompt, model=model,
                                   max_tokens=max_tokens)
    return GenerationResult(text=text, provider=llm.provider, model=model or llm.model, degraded=False)


async def generate_script(llm: LLMClient, *, brief: str | None = None,
                         model: str | None = None, max_tokens: int | None = None) -> GenerationResult:
    """brief=None -> today's default campaign; otherwise the marketer's own brief."""
    date_context = DateContext.now()
    templates = await fetch_templates_from_db()
    if brief:
        user = (f"Marketer's brief (answer in the required format):\n\"\"\"\n{brief.strip()}\n\"\"\"\n\n"
                f"Date: {date_context.as_text()}\n"
                f"Season context: {SEASON_HINTS.get(date_context.season_key, '')}\n"
                f"Brand guidelines: {templates}")
    else:
        user = _default_brief(date_context, templates)
    log.info("script generation: custom_brief=%s season=%s", bool(brief), date_context.season_key)
    return await generate_with_fallback(llm, BRAND_SYSTEM_PROMPT, user, model=model,
                                        max_tokens=max_tokens)


async def generate_video_prompt(llm: LLMClient, script: str, *, model: str | None = None,
                                max_tokens: int | None = None) -> GenerationResult:
    user = f"Reel script:\n\"\"\"\n{script.strip()}\n\"\"\""
    return await generate_with_fallback(llm, VIDEO_PROMPT_SYSTEM, user, model=model,
                                        max_tokens=max_tokens)


async def revise_script(llm: LLMClient, script: str, feedback: str, *, model: str | None = None,
                        max_tokens: int | None = None) -> GenerationResult:
    user = (f"Current script:\n\"\"\"\n{script.strip()}\n\"\"\"\n\n"
            f"Editor's requested changes:\n\"\"\"\n{feedback.strip()}\n\"\"\"\n\n"
            f"Return the full updated script.")
    return await generate_with_fallback(llm, EDIT_SYSTEM_PROMPT, user, model=model,
                                        max_tokens=max_tokens)
