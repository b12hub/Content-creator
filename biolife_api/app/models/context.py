"""Incoming payload = the JSON returned by biolife.resolve_ad_context() (Phase 1).

Models are permissive (extra fields ignored) so that adding columns to the DB
never breaks the API, but the fields the prompts depend on are validated.
"""
from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FrameworkName(str, Enum):
    COCA_COLA_EMOTIONAL = "COCA_COLA_EMOTIONAL"
    PEPSI_BEHAVIORAL_DOPAMINE = "PEPSI_BEHAVIORAL_DOPAMINE"
    HYBRID_SPRING_RENEWAL = "HYBRID_SPRING_RENEWAL"


class Sku(str, Enum):
    GLASS_033 = "0.33L Glass"
    PET_05 = "0.5L PET"
    PET_15 = "1.5L PET"
    FAMILY = "5L/10L Family Pack"


class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class ContextInput(_Loose):
    date: dt.date | None = None
    temp_c: float | None = None
    dish_key: str | None = None
    trend: str | None = None


class SampleHooks(_Loose):
    uz: list[str] = Field(default_factory=list)
    ru: list[str] = Field(default_factory=list)


class Voiceover(_Loose):
    language_priority: list[str] = Field(default_factory=lambda: ["uz-Latn", "ru"])
    voice: str = ""
    pace_wpm: int = 130
    music: str = ""
    do: list[str] = Field(default_factory=list)
    dont: list[str] = Field(default_factory=list)
    sample_hooks: SampleHooks = Field(default_factory=SampleHooks)


class InstagramFormat(_Loose):
    reel_length_s: list[int] = Field(default_factory=lambda: [15, 30])
    hook_window_s: float = 3
    avg_shot_length_s: float = 2
    aspect_ratio: str = "9:16"
    cta_style: str = ""

    @property
    def max_seconds(self) -> int:
        return max(self.reel_length_s) if self.reel_length_s else 30


class Framework(_Loose):
    name: FrameworkName
    neural_target: str = ""
    primary_trigger: str = ""
    narrative_tone: str = ""
    visual_style: str = ""
    voiceover: Voiceover = Field(default_factory=Voiceover)
    instagram_format: InstagramFormat = Field(default_factory=InstagramFormat)
    selection_rationale: str | None = None


class PrimaryEvent(_Loose):
    key: str
    name_uz: str = ""
    name_ru: str = ""
    # added in DB v1.1 (optional so older payloads still validate)
    priority: int | None = None
    window_start: dt.date | None = None
    window_end: dt.date | None = None
    day_index: int | None = None       # 1-based day inside the event window
    days_total: int | None = None
    physiological_state: str = ""
    emotional_state: str = ""
    cultural_rituals: list[str] = Field(default_factory=list)
    semiotic_symbols: list[str] = Field(default_factory=list)


class Dish(_Loose):
    key: str
    name_uz: str = ""
    name_ru: str = ""
    category: str = ""
    caloric_density: str = ""
    fat_and_spice_profile: str = ""
    physiological_impact: str = ""
    pairing_mechanics: str = ""
    recommended_sku: Sku | None = None


class AdContext(_Loose):
    """Exact shape of biolife.resolve_ad_context() output."""

    input: ContextInput = Field(default_factory=ContextInput)
    resolved_temperature_c: float | None = None
    temperature_source: str | None = None
    season: str | None = None
    month_context: str | None = None
    primary_event: PrimaryEvent | None = None
    all_active_events: list[str] = Field(default_factory=list)
    framework: Framework
    fallback_level: str | None = None
    tone_modifiers: list[str] = Field(default_factory=list)
    visual_modifiers: list[str] = Field(default_factory=list)
    dishes: list[Dish] = Field(default_factory=list, min_length=1)
    recommended_sku: Sku
    trend_hook: str | None = None
    guardrails: list[dict[str, Any]] = Field(default_factory=list)
    applied_rules: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    # ---- helpers used by prompts and quality checks -------------------
    @property
    def is_religious_period(self) -> bool:
        return any(g.get("mode") == "RELIGIOUS_SENSITIVE" for g in self.guardrails)

    @property
    def is_low_key(self) -> bool:
        return any(g.get("mode") == "LOW_KEY" for g in self.guardrails)

    def guardrail_lines(self) -> list[str]:
        lines: list[str] = []
        for g in self.guardrails:
            if note := g.get("note"):
                lines.append(str(note))
            lines.extend(str(r) for r in g.get("rules", []))
        return lines
