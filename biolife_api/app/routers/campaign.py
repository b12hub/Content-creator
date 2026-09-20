from __future__ import annotations

import datetime as dt
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.config import Settings
from app.dependencies import get_llm_client, settings_dep
from app.handlers.factory import get_handler
from app.llm.base import LLMClient, LLMError, LLMRefusal
from app.models.campaign import CampaignPackage, CampaignScript, ContextSummary
from app.models.context import AdContext
from app.models.script_package import ResolvedCallToAction
from app.services.media_planner import MediaPlannerService
from app.services.quality import QualityFailed

log = logging.getLogger("biolife.campaign")
router = APIRouter(tags=["campaign"])


@router.post(
    "/generate-campaign-package",
    response_model=CampaignPackage,
    summary="Script (Phase 3) + Meta Ads media plan (Phase 4) from one resolve_ad_context() payload",
    responses={422: {"description": "Invalid context payload"},
               502: {"description": "LLM failed, refused, or failed QA after retries"}},
)
async def generate_campaign_package(
    ctx: AdContext,
    llm: Annotated[LLMClient, Depends(get_llm_client)],
    settings: Annotated[Settings, Depends(settings_dep)],
    product_launch: Annotated[bool, Query(description="Force PREMIUM tier for a product launch")] = False,
) -> CampaignPackage:
    # 1. media plan first: deterministic, instant, and fails fast before paying for an LLM call
    plan = MediaPlannerService(settings).calculate_plan(ctx, product_launch=product_launch)

    # 2. script via the Phase 2 handler (same routing + QA)
    handler = get_handler(ctx.framework.name, llm, settings)
    try:
        reel = await handler.generate(ctx)
    except LLMRefusal as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {"error": "llm_refusal", "detail": str(e)}) from e
    except QualityFailed as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            {"error": "quality_check_failed", "attempts": e.attempts, "issues": e.issues}) from e
    except LLMError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {"error": "llm_error", "detail": str(e)}) from e

    # 3. resolve CTA handle/link from config (never from the LLM)
    channel = next(c for c in settings.cta_channels if c.key == reel.call_to_action.channel_key)
    cta = ResolvedCallToAction(**reel.call_to_action.model_dump(), channel_label_ru=channel.label_ru,
                               channel_label_uz=channel.label_uz, handle=channel.handle, url=channel.url)

    ev = ctx.primary_event
    day = ctx.input.date or dt.date.today()
    package_id = f"biolife_{day:%Y%m%d}_{ev.key if ev else 'none'}_{ctx.framework.name.value.lower()}_{plan.decision.tier.value.lower()}"

    blockers = [f"Review: {r}" for r in reel.quality.review_reasons]
    blockers += [f"Media: {w}" for w in plan.warnings if w.startswith("PREMIUM wants")]
    if not settings.meta_pixel_id and plan.tier.optimization_goal == "OFFSITE_CONVERSIONS":
        blockers.append("Media: conversion optimization selected but BIOLIFE_META_PIXEL_ID is empty")

    log.info("package=%s tier=%s handler=%s", package_id, plan.decision.tier.value, handler.name)
    return CampaignPackage(
        package_id=package_id,
        generated_at=dt.datetime.now(dt.timezone.utc),
        context=ContextSummary(date=ctx.input.date, event_key=ev.key if ev else None,
                               event_name_ru=ev.name_ru if ev else None, framework=ctx.framework.name,
                               temperature_c=ctx.resolved_temperature_c,
                               temperature_source=ctx.temperature_source, season=ctx.season,
                               sku=ctx.recommended_sku.value),
        creative=CampaignScript(strategic_selection=reel.strategic_selection, visual_hook=reel.visual_hook,
                                script=reel.script, call_to_action=cta),
        quality=reel.quality,
        generation=reel.meta,
        media_plan=plan,
        ready_to_launch=not blockers,
        blockers=blockers,
    )
