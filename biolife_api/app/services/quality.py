"""Deterministic QA of LLM output. Runs after every generation.

hard_issues   → the draft is rejected and the LLM gets one repair attempt.
review_reasons → the draft is returned, but a human must approve before publishing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.config import Settings
from app.handlers.prompt_blocks import voiceover_word_budget
from app.models.context import AdContext
from app.models.script_package import ReelScriptLLM
from app.services.lexicon import TEMPERATURE_RE, context_aliases, dish_aliases, mentions_any

CYRILLIC = re.compile(r"[А-Яа-яЁёЎўҚқҒғҲҳ]")

# lower-cased stems / words; matched with word-start boundaries
COMPETITOR_AND_INTERNAL = [
    "coca-cola", "coca cola", "кока-кол", "кока кол", "pepsi", "пепси",
    "dopamine", "дофамин", "hippocamp", "гиппокамп", "neuro", "нейро",
    "conditional reflex", "условный рефлекс", "framework",
]
ALCOHOL = [r"водк\w*", r"пив[оа]\w*", r"пивн\w*", r"бокал\w*\s+вина", r"вино\b", r"коньяк\w*", r"шампанск\w*", r"алкогол\w*",
           r"aroq\w*", r"pivo\w*", r"vino\b", r"alkogol\w*", r"shampan\w*",
           r"beer\b", r"wine\b", r"vodka\w*", r"alcohol\w*"]
MEDICAL = [r"лечит\w*", r"лечеб\w*", r"исцел\w*", r"детокс\w*", r"токсин\w*", r"пищеварен\w*",
           r"иммунитет\w*", r"сжиг\w*\s+(?:жир|калори)\w*", r"похуд\w*", r"метаболизм\w*",
           r"davola\w*", r"shifo(?!kor)\w*", r"detoks\w*", r"toksin\w*", r"hazm\w*", r"immunitet\w*",
           r"ozdir\w*", r"detox\w*", r"cure\w*", r"digestion\w*", r"immunity\w*"]
SALES_PRESSURE = [r"скидк\w*", r"акци[яиюей]\w*", r"распродаж\w*", r"chegirma\w*", r"aksiya\w*",
                  r"sale\b", r"discount\w*"]
DAYTIME_RISK = [r"днём", r"днем", r"в полдень", r"обед\w*", r"kunduzi", r"tush\s+payt\w*", r"peshin\w*"]


WORD = re.compile(r"[\w°%+'‘’ʻ]+")


def word_count(s: str) -> int:
    """Counts real words; stand-alone dashes/punctuation are not words."""
    return sum(1 for t in WORD.findall(s) if re.search(r"\w", t))


def _find(patterns: list[str], text: str) -> list[str]:
    hits = []
    for p in patterns:
        m = re.search(r"(?<![\w-])" + (re.escape(p) if not any(c in p for c in "\\[(") else p), text)
        if m:
            hits.append(m.group(0))
    return hits


@dataclass
class QAReport:
    hard_issues: list[str] = field(default_factory=list)
    review_reasons: list[str] = field(default_factory=list)


class QualityFailed(Exception):
    def __init__(self, issues: list[str], attempts: int):
        super().__init__("; ".join(issues))
        self.issues = issues
        self.attempts = attempts


def _viewer_text(d: ReelScriptLLM) -> dict[str, str]:
    """Everything the viewer sees or hears, per language."""
    ru = " ".join([d.visual_hook.on_screen_text.ru, d.script.ru.voiceover,
                   *d.script.ru.on_screen_text, d.call_to_action.ru])
    uz = " ".join([d.visual_hook.on_screen_text.uz, d.script.uz.voiceover,
                   *d.script.uz.on_screen_text, d.call_to_action.uz])
    return {"ru": ru, "uz": uz}


def evaluate(ctx: AdContext, d: ReelScriptLLM, settings: Settings,
             route_forbidden: list[str] | None = None) -> QAReport:
    r = QAReport()
    text = _viewer_text(d)
    both = (text["ru"] + " " + text["uz"]).lower()

    # --- language integrity --------------------------------------------
    if CYRILLIC.search(text["uz"]):
        sample = CYRILLIC.findall(text["uz"])[:5]
        r.hard_issues.append(f"Uzbek text must be Latin script only; Cyrillic letters found: {''.join(sample)}")
    if not CYRILLIC.search(text["ru"]):
        r.hard_issues.append("Russian script contains no Cyrillic text — ru block must be in Russian")
    for lang in ("ru", "uz"):
        if "biolife" not in text[lang].lower():
            r.hard_issues.append(f"Brand name 'BioLife' missing from the {lang} viewer-facing text")

    # --- brand safety ----------------------------------------------------
    if hits := _find(COMPETITOR_AND_INTERNAL, both):
        r.hard_issues.append(f"Remove competitor/internal terms from viewer-facing text: {hits}")
    if hits := _find(ALCOHOL, both):
        r.hard_issues.append(f"Alcohol references are forbidden: {hits}")
    if hits := _find(MEDICAL, both):
        r.hard_issues.append(f"Medical/health claims are forbidden, describe sensations only: {hits}")
    if (ctx.is_religious_period or ctx.is_low_key) and (hits := _find(SALES_PRESSURE, both)):
        r.hard_issues.append(f"No discount/sales language on religious or remembrance days: {hits}")

    if route_forbidden:
        hits = [t for t in route_forbidden if re.search(r"(?<![\w-])" + re.escape(t), both)]
        if hits:
            r.hard_issues.append(f"These words break this route's psychological boundaries: {hits}")

    # --- structure & format ----------------------------------------------
    allowed = {c.key for c in settings.cta_channels}
    if d.call_to_action.channel_key not in allowed:
        r.hard_issues.append(f"call_to_action.channel_key '{d.call_to_action.channel_key}' is not one of {sorted(allowed)}")
    if not d.visual_hook.time_range.strip().startswith("0"):
        r.hard_issues.append("visual_hook.time_range must start at 0 (e.g. '0-3s')")
    kinds = {c.kind for c in d.script.audio_cues}
    if not d.script.audio_cues:
        r.hard_issues.append("script.audio_cues is empty")
    else:
        if "MUSIC" not in kinds:
            r.hard_issues.append("audio_cues need at least one MUSIC cue (a 'no music' decision counts)")
        if not kinds & {"ASMR", "FOLEY"}:
            r.hard_issues.append("audio_cues need at least one ASMR or FOLEY cue")
    for fld in ("shot_type", "lighting", "camera_movement", "editor_instructions"):
        if not getattr(d.visual_hook, fld).strip():
            r.hard_issues.append(f"visual_hook.{fld} is empty")

    budget = voiceover_word_budget(ctx)
    for lang in ("ru", "uz"):
        words = word_count(getattr(d.script, lang).voiceover)
        if words > budget:
            r.hard_issues.append(f"{lang} voiceover has {words} words; max {budget} for this reel length/pace")
        for line in getattr(d.script, lang).on_screen_text:
            if word_count(line) > 8:
                r.hard_issues.append(f"{lang} on-screen text too long (>8 words): '{line}'")
        hook = getattr(d.visual_hook.on_screen_text, lang)
        if word_count(hook) > 6:
            r.hard_issues.append(f"{lang} hook text too long (>6 words): '{hook}'")

    # --- Part 1 must reference the inputs (dish + temperature or season/event) ---
    sel = d.strategic_selection
    dish = ctx.dishes[0]
    if not mentions_any(sel, dish_aliases(dish.key, dish.name_ru, dish.name_uz)):
        r.hard_issues.append(f"strategic_selection must name the featured dish ({dish.name_ru} / {dish.name_uz})")
    ev = ctx.primary_event
    if not (TEMPERATURE_RE.search(sel)
            or mentions_any(sel, context_aliases(ctx.season, ev.key if ev else None, ev.name_ru if ev else None))):
        r.hard_issues.append("strategic_selection must reference the temperature (e.g. +43°C) or the season/event")

    # --- soft checks -> human review ---------------------------------------
    if ctx.is_religious_period:
        r.review_reasons.append("Religious period: a person must approve tone before publishing")
        if hits := _find(DAYTIME_RISK, both):
            r.review_reasons.append(f"Check that nobody drinks during fasting hours (daytime words: {hits})")
    if ctx.is_low_key:
        r.review_reasons.append("Remembrance day: a person must approve before publishing")
    r.review_reasons.extend(f"DB warning: {w}" for w in ctx.warnings)
    num = re.escape(ctx.recommended_sku.value.split("L")[0]).replace(r"\.", r"[.,]")   # 0.5 -> 0[.,]5
    sku_re = r"(?<![\d.,])" + num + (r"(?:\s*/\s*10)?" if num == "5" else "") + r"\s*(?:l|л|litr|литр)"
    if not re.search(sku_re, both):
        r.review_reasons.append(f"SKU {ctx.recommended_sku.value} is not mentioned in viewer text")
    return r
