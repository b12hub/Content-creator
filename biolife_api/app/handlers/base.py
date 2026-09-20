from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import ClassVar

from app.config import Settings
from app.handlers.prompt_blocks import SHARED_CONTRACT, guardrail_block, render_brief, repair_instructions
from app.llm.base import LLMClient
from app.models.context import AdContext, FrameworkName
from app.models.script_package import GenerationMeta, QualityReport, ReelScriptLLM, ReelScriptResponse
from app.services.quality import QualityFailed, evaluate

log = logging.getLogger("biolife.handlers")


class BaseReelHandler(ABC):
    framework: ClassVar[FrameworkName]
    name: ClassVar[str]
    # lower-case stems that must NOT appear in viewer-facing text for this route (QA-enforced)
    forbidden_viewer_terms: ClassVar[list[str]] = []

    def __init__(self, llm: LLMClient, settings: Settings):
        self.llm = llm
        self.settings = settings

    # --- the only thing each route must define ---------------------------
    @abstractmethod
    def persona_prompt(self, ctx: AdContext) -> str: ...

    # --- shared pipeline -----------------------------------------------
    def build_system_prompt(self, ctx: AdContext) -> str:
        return "\n\n".join([self.persona_prompt(ctx).strip(), SHARED_CONTRACT, guardrail_block(ctx)])

    def build_user_prompt(self, ctx: AdContext) -> str:
        return render_brief(ctx, self.settings.cta_channels) + \
            "\n\nWrite the reel now. Return only the JSON object."

    async def generate(self, ctx: AdContext) -> ReelScriptResponse:
        system = self.build_system_prompt(ctx)
        user = self.build_user_prompt(ctx)
        fixed: list[str] = []
        last_issues: list[str] = []

        for attempt in range(1, self.settings.max_attempts + 1):
            draft = await self.llm.generate_structured(system=system, user=user, schema=ReelScriptLLM)
            report = evaluate(ctx, draft, self.settings, route_forbidden=self.forbidden_viewer_terms)
            if not report.hard_issues:
                return ReelScriptResponse(
                    **draft.model_dump(),
                    meta=GenerationMeta(
                        framework=self.framework, handler=self.name,
                        llm_provider=self.llm.provider, llm_model=self.llm.model,
                        sku=ctx.recommended_sku,
                        event_key=ctx.primary_event.key if ctx.primary_event else None,
                        dish_keys=[d.key for d in ctx.dishes]),
                    quality=QualityReport(
                        passed=True, attempts=attempt, issues_fixed_on_retry=fixed,
                        review_required=bool(report.review_reasons),
                        review_reasons=report.review_reasons),
                )
            log.warning("handler=%s attempt=%d QA issues=%s", self.name, attempt, report.hard_issues)
            last_issues = report.hard_issues
            fixed = list(report.hard_issues)
            user = user.split("\n\n══════════ YOUR PREVIOUS DRAFT")[0] + \
                repair_instructions(report.hard_issues, draft.model_dump_json())

        raise QualityFailed(last_issues, attempts=self.settings.max_attempts)
