from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.config import Settings
from app.dependencies import get_llm_client, settings_dep
from app.handlers.factory import get_handler
from app.llm.base import LLMClient, LLMError, LLMRefusal
from app.models.context import AdContext
from app.models.script_package import ReelScriptResponse
from app.services.quality import QualityFailed

log = logging.getLogger("biolife.api")
router = APIRouter(tags=["ad-generator"])


@router.post(
    "/generate-reel-script",
    response_model=ReelScriptResponse,
    summary="Generate a bilingual Instagram Reel script from a resolve_ad_context() payload",
    responses={
        422: {"description": "Payload is not a valid resolve_ad_context() JSON"},
        502: {"description": "LLM failed, refused, or its output failed QA after retries"},
    },
)
async def generate_reel_script(
    ctx: AdContext,
    llm: Annotated[LLMClient, Depends(get_llm_client)],
    settings: Annotated[Settings, Depends(settings_dep)],
) -> ReelScriptResponse:
    handler = get_handler(ctx.framework.name, llm, settings)   # deterministic route
    log.info("route framework=%s handler=%s event=%s", ctx.framework.name.value, handler.name,
             ctx.primary_event.key if ctx.primary_event else None)
    try:
        return await handler.generate(ctx)
    except LLMRefusal as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {"error": "llm_refusal", "detail": str(e)}) from e
    except QualityFailed as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            {"error": "quality_check_failed", "attempts": e.attempts, "issues": e.issues}) from e
    except LLMError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {"error": "llm_error", "detail": str(e)}) from e
