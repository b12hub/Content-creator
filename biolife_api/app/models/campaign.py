from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field

from app.models.context import FrameworkName
from app.models.media_plan import MediaPlan
from app.models.script_package import (
    GenerationMeta, QualityReport, ReelScriptLLM, ResolvedCallToAction, Script, VisualHook,
)


class ContextSummary(BaseModel):
    date: dt.date | None
    event_key: str | None
    event_name_ru: str | None
    framework: FrameworkName
    temperature_c: float | None
    temperature_source: str | None
    season: str | None
    sku: str


class CampaignScript(BaseModel):
    """Phase 3 4-part script, with the CTA resolved to real handles/links from config."""
    strategic_selection: str
    visual_hook: VisualHook
    script: Script
    call_to_action: ResolvedCallToAction


class CampaignPackage(BaseModel):
    package_id: str = Field(description="Stable id: date + event + framework + tier; use as utm_campaign")
    generated_at: dt.datetime
    context: ContextSummary
    creative: CampaignScript
    quality: QualityReport
    generation: GenerationMeta
    media_plan: MediaPlan
    ready_to_launch: bool = Field(description="False while any human review or blocking media issue (see blockers) is open")
    blockers: list[str]


__all__ = ["CampaignPackage", "CampaignScript", "ContextSummary", "ReelScriptLLM"]
