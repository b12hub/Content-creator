"""Prompt building blocks shared by all three routes.

System prompt  = route persona (unique per handler) + SHARED_CONTRACT + hard guardrails.
User prompt    = the creative brief rendered from the DB context (render_brief).
"""
from __future__ import annotations

import json
from textwrap import dedent

from app.config import CtaChannel
from app.models.context import AdContext


def voiceover_word_budget(ctx: AdContext) -> int:
    """Max words per language that fit the longest reel at the framework's pace (+10%)."""
    fw = ctx.framework
    return int(fw.voiceover.pace_wpm * fw.instagram_format.max_seconds / 60 * 1.1)


SHARED_CONTRACT = dedent("""
    ══════════ UNIVERSAL CONTRACT (applies to every route) ══════════

    OUTPUT — return ONLY the JSON object defined by the schema, with exactly these four parts:
    1. strategic_selection — 2-4 sentences IN RUSSIAN for the marketing team: which psychological
       angle you chose and WHY it fits this exact date, event, temperature, dish and SKU. It MUST name
       the featured dish and the temperature (e.g. "+43°C") or the season/event from the brief.
    2. visual_hook — the first 0-3 seconds only, all IN RUSSIAN, executable by an editor without
       questions: shot_type (frame/lens), lighting (light + colour grade), camera_movement (move +
       fps), editor_instructions (what happens at 0s, 1s, 2s, 3s; focus; transitions).
       on_screen_text: max 6 words per language.
    3. script — the full reel after the hook:
       • ru.voiceover / uz.voiceover — complete voiceover lines, in order.
       • ru.on_screen_text / uz.on_screen_text — ordered text overlays, max 8 words each.
       • audio_cues — IN RUSSIAN, with timecodes covering the whole reel. Each cue has a kind:
         MUSIC, ASMR, FOLEY, VOICE or SILENCE. Include at least one MUSIC cue (a deliberate
         "без музыки" decision counts) and at least one ASMR or FOLEY cue.
    4. call_to_action — channel_key MUST be one of the allowed keys in the brief; ru line uses the channel's
       ru name, uz line its uz name, exactly as given. Never invent handles, URLs, phone numbers or prices.

    LANGUAGE RULES
    • ru: natural spoken Russian as heard in Tashkent, not bureaucratic, not translated-from-English.
    • uz: modern Uzbek in the LATIN alphabet only (o‘, g‘, sh, ch, ng). Zero Cyrillic letters.
      Culturally adapt, do not translate word-for-word; meaning and timing must match the ru version.
      Address the viewer respectfully ("Siz") unless the route explicitly targets peers.
    • The brand is always written exactly "BioLife". The SKU is written exactly as given in the brief.

    BRAND SAFETY — breaking any of these makes the output unusable
    • Never mention Coca-Cola, Pepsi or any other product brand (the CTA channel names from the
      brief — Telegram, Yandex, Uzum, etc. — are the only allowed exception); never use internal terms from the brief
      (framework names, "dopamine", "hippocampus", "neuro", "conditional reflex") in viewer-facing text.
    • Never invent product facts: water source, mineral content, pH, awards, certifications, prices,
      discounts, delivery times. If it is not in the brief, it does not exist.
    • No medical or health claims (no "cures", "detox", "improves digestion", "burns fat", "boosts
      immunity"). Describe sensations only: cool, fresh, clean taste, relief, lightness.
    • No alcohol, no smoking, no gambling. Halal, family-safe context. Modest clothing and respectful
      behaviour consistent with Uzbek norms. No romance between unmarried couples.
    • No quotations from the Qur'an or hadith, no imitation of the adhan, no religious rulings.
    • Respect elders, teachers and parents on screen at all times. Never mock the green-tea tradition
      after a meal: BioLife stands NEXT to the choynak, it does not replace or ridicule it.
    • People in heat: show shade and responsible drinking; never glorify dangerous heat exposure.

    PRECEDENCE: HARD GUARDRAILS in this prompt > route boundaries > creative laws > brief suggestions.
    The sample hooks in the brief are style references — never copy them verbatim.
""").strip()


def guardrail_block(ctx: AdContext) -> str:
    lines = ctx.guardrail_lines()
    if ctx.is_religious_period:
        lines.append("RELIGIOUS PERIOD: calm, respectful, gratitude-led. No party energy, no dancing, "
                     "no discounts, no humour about fasting. Water appears only at iftar or suhoor.")
    if ctx.is_low_key:
        lines.append("LOW-KEY DAY: remembrance only. No sales language, no urgency, no humour.")
    if not lines:
        return "══════════ HARD GUARDRAILS ══════════\n(none beyond the universal contract)"
    return "══════════ HARD GUARDRAILS (non-negotiable) ══════════\n" + "\n".join(f"• {l}" for l in lines)


def render_brief(ctx: AdContext, cta_channels: list[CtaChannel]) -> str:
    fw, ev, vo, ig = ctx.framework, ctx.primary_event, ctx.framework.voiceover, ctx.framework.instagram_format
    temp = ctx.resolved_temperature_c
    temp_line = (f"{temp:+.0f}°C ({'live reading' if ctx.temperature_source == 'live_input' else 'monthly average max'})"
                 if temp is not None else "unknown")
    L: list[str] = [f"CREATIVE BRIEF — BioLife Water, Instagram Reel ({ig.aspect_ratio})", ""]

    L += ["▸ DATE & CLIMATE",
          f"  date: {ctx.input.date or 'today'} | season: {ctx.season} | temperature: {temp_line}",
          f"  month context: {ctx.month_context}", ""]

    L.append("▸ EVENT (primary)")
    if ev:
        L += [f"  {ev.name_uz} / {ev.name_ru} (key: {ev.key})",
              f"  body state: {ev.physiological_state}",
              f"  emotional state: {ev.emotional_state}",
              f"  rituals to draw from: {'; '.join(ev.cultural_rituals)}",
              f"  symbols to draw from: {'; '.join(ev.semiotic_symbols)}"]
    else:
        L.append("  No specific event — use the season and month context.")
    L += [f"  other active events: {', '.join(ctx.all_active_events) or 'none'}", ""]

    L.append("▸ DISHES (feature the FIRST; others optional)")
    for d in ctx.dishes:
        L += [f"  - {d.name_uz} / {d.name_ru} [{d.category}, calories: {d.caloric_density}]",
              f"    profile: {d.fat_and_spice_profile}",
              f"    body effect (context only, never a claim): {d.physiological_impact}",
              f"    why BioLife pairs: {d.pairing_mechanics}"]
    L += ["", "▸ PRODUCT", f"  SKU to feature: {ctx.recommended_sku.value}", ""]

    L += ["▸ DIRECTION FROM THE STRATEGY DATABASE",
          f"  why this framework today: {fw.selection_rationale or 'n/a'}",
          f"  primary trigger: {fw.primary_trigger}",
          f"  narrative tone: {fw.narrative_tone}",
          f"  visual style: {fw.visual_style}",
          f"  voice: {vo.voice} | pace: {vo.pace_wpm} wpm | music: {vo.music}",
          f"  do: {'; '.join(vo.do)}",
          f"  don't: {'; '.join(vo.dont)}",
          f"  style-reference hooks (do not copy): uz {json.dumps(vo.sample_hooks.uz, ensure_ascii=False)}"
          f" | ru {json.dumps(vo.sample_hooks.ru, ensure_ascii=False)}",
          f"  runtime tone modifiers: {'; '.join(ctx.tone_modifiers) or 'none'}",
          f"  runtime visual modifiers: {'; '.join(ctx.visual_modifiers) or 'none'}", ""]

    L.append("▸ TREND HOOK")
    L.append(f'  Open with this trend in the first 2 seconds, then return to the route story: "{ctx.trend_hook}". '
             "Drop it if it conflicts with any guardrail." if ctx.trend_hook else "  none")
    L.append("")

    L += ["▸ FORMAT",
          f"  reel length: {ig.reel_length_s[0]}-{ig.max_seconds}s | hook window: {ig.hook_window_s}s"
          f" | avg shot: {ig.avg_shot_length_s}s",
          f"  voiceover budget: MAX {voiceover_word_budget(ctx)} words per language",
          f"  CTA style: {ig.cta_style}", ""]

    L.append("▸ ALLOWED CTA CHANNELS (choose exactly one key)")
    L += [f"  - key: {c.key} | ru name: {c.label_ru} | uz name: {c.label_uz} | action: {c.action_hint}"
          for c in cta_channels]
    return "\n".join(L)


def repair_instructions(issues: list[str], rejected_draft_json: str) -> str:
    bullets = "\n".join(f"• {i}" for i in issues)
    return ("\n\n══════════ YOUR PREVIOUS DRAFT WAS REJECTED BY QA ══════════\n"
            f"Rejected draft:\n{rejected_draft_json}\n\n"
            "Fix every issue below, keep everything else that was good, and return the complete "
            f"corrected JSON.\n{bullets}")
