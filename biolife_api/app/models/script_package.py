"""Phase 3 — the enforced 4-part Reel script schema (single source of truth).

`ReelScriptLLM` is sent to the LLM as structured output. It has NO length/regex constraints on purpose:
Anthropic and OpenAI structured outputs do not support minLength/maxLength/pattern (verified in their
docs), so those rules live in app/services/quality.py and are enforced after every generation.

Part 1  strategic_selection  why this psychological angle (must cite dish + temperature/season/event)
Part 2  visual_hook          0-3s editor instructions: shot type, lighting, movement, text overlay
Part 3  script               ru / uz (Latin) voiceover + on-screen text, typed audio cues
Part 4  call_to_action       one allowed channel; handle/link are filled from config, never by the LLM
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.context import FrameworkName, Sku


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BilingualText(_Strict):
    ru: str = Field(description="Russian (Cyrillic)")
    uz: str = Field(description="Uzbek in LATIN script (o‘, g‘, sh, ch)")


class VisualHook(_Strict):
    time_range: str = Field(description="Always starts at 0: '0-3s', or shorter like '0-1.5s' for fast formats")
    shot_type: str = Field(description="Frame type, in Russian: e.g. 'макро', 'крупный план рук', 'общий план двора'")
    lighting: str = Field(description="Lighting and colour grade, in Russian")
    camera_movement: str = Field(description="Camera movement and speed/fps, in Russian: e.g. 'медленный dolly-in, 24fps'")
    editor_instructions: str = Field(
        description="Second-by-second shot list for the video editor, in Russian: what happens at 0s, 1s, 2s, 3s, "
        "what is in focus, transitions")
    on_screen_text: BilingualText = Field(description="Text overlay during the hook, max 6 words per language")


class LanguageScript(_Strict):
    voiceover: str = Field(description="Full voiceover for the whole reel in this language")
    on_screen_text: list[str] = Field(description="Ordered text overlays for this language, max 8 words each")


AudioKind = Literal["MUSIC", "ASMR", "FOLEY", "VOICE", "SILENCE"]


class AudioCue(_Strict):
    timecode: str = Field(description="e.g. '0.0-1.2s'")
    kind: AudioKind = Field(description="MUSIC (incl. 'no music' decisions), ASMR, FOLEY, VOICE or SILENCE")
    cue: str = Field(description="Exact sound design / ASMR / music instruction, in Russian")


class Script(_Strict):
    ru: LanguageScript
    uz: LanguageScript
    audio_cues: list[AudioCue]


class CallToAction(_Strict):
    channel_key: str = Field(description="One of the allowed CTA channel keys given in the brief")
    ru: str
    uz: str


class ReelScriptLLM(_Strict):
    """The 4-part structure every handler's LLM call must return."""

    strategic_selection: str = Field(
        description="2-4 sentences in Russian: why this psychological angle; must name the featured dish and "
        "the temperature or season/event from the brief")
    visual_hook: VisualHook
    script: Script
    call_to_action: CallToAction


# ---------------------------- API response models ----------------------------
class QualityReport(BaseModel):
    passed: bool
    attempts: int
    issues_fixed_on_retry: list[str] = Field(default_factory=list)
    review_required: bool = Field(description="True when a human must approve before publishing")
    review_reasons: list[str] = Field(default_factory=list)


class GenerationMeta(BaseModel):
    framework: FrameworkName
    handler: str
    llm_provider: str
    llm_model: str
    sku: Sku
    event_key: str | None
    dish_keys: list[str]


class ReelScriptResponse(ReelScriptLLM):
    """Response of POST /generate-reel-script (Phase 2 contract)."""

    model_config = ConfigDict(extra="forbid")
    meta: GenerationMeta
    quality: QualityReport


class ResolvedCallToAction(CallToAction):
    """CTA enriched from config: handle and link are never written by the LLM."""

    channel_label_ru: str
    channel_label_uz: str
    handle: str | None = None
    url: str | None = None
