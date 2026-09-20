"""Backward-compatible import path (Phase 2). The schema now lives in app.models.script_package."""
from app.models.script_package import (  # noqa: F401  (re-exports)
    AudioCue, BilingualText, CallToAction, GenerationMeta, LanguageScript, QualityReport,
    ReelScriptLLM, ReelScriptResponse, ResolvedCallToAction, Script, VisualHook,
)

__all__ = ["AudioCue", "BilingualText", "CallToAction", "GenerationMeta", "LanguageScript", "QualityReport",
           "ReelScriptLLM", "ReelScriptResponse", "ResolvedCallToAction", "Script", "VisualHook"]
