"""Phase 4 — Meta Ads media plan models."""
from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class BudgetTier(str, Enum):
    STANDARD = "STANDARD"
    DRIVE = "DRIVE"
    PREMIUM = "PREMIUM"

    @property
    def rank(self) -> int:
        return {"STANDARD": 0, "DRIVE": 1, "PREMIUM": 2}[self.value]


class TierSpec(BaseModel):
    tier: BudgetTier
    monthly_budget_uzs: int
    reels_min: int
    reels_max: int | None = Field(description="None = open-ended ('10+')")
    production: str
    goal: str
    campaign_objective: str = Field(description="Meta ODAX objective, e.g. OUTCOME_TRAFFIC")
    optimization_goal: str = Field(description="Meta ad set optimization_goal enum")
    billing_event: str = "IMPRESSIONS"


class TierTrigger(BaseModel):
    rule: str
    tier: BudgetTier
    reason: str


class TierDecision(BaseModel):
    tier: BudgetTier
    fired: list[TierTrigger] = Field(description="Every rule that fired; the highest tier wins")
    forced_by: str | None = Field(None, description="Set when a guardrail capped the tier")


class CityTarget(BaseModel):
    key: str
    name_ru: str
    name_uz: str
    latitude: float
    longitude: float
    radius_km: int = Field(ge=1, le=80, description="Meta custom_locations limit: 1-80 km")


class AdSetPlan(BaseModel):
    name: str
    cities: list[str]
    budget_share: float = Field(ge=0, le=1)
    monthly_budget_uzs: int
    daily_budget_uzs: int
    daily_budget_usd: float
    budget_type: str = Field(description="DAILY, or LIFETIME when ad scheduling is used")
    lifetime_budget_usd: float | None = None
    max_cost_per_result_for_learning_usd: float | None = Field(
        None, description="Weekly budget / 50: the highest cost per optimization event at which this ad set can "
        "still reach ~50 events in 7 days (Meta's learning-phase guidance). None for ThruPlay.")
    creatives: list[str] = Field(description="Ads inside this ad set (one per language)")
    targeting: dict[str, Any] = Field(description="Meta targeting spec, ready for the Marketing API")
    adset_schedule: list[dict[str, Any]] | None = None
    pacing_type: list[str] = Field(default_factory=lambda: ["standard"],
                                   description="['day_parting'] when adset_schedule is used")


class MeasurementPlan(BaseModel):
    primary_kpi: str
    tracking: list[str]
    prerequisites: list[str]


class MediaPlan(BaseModel):
    decision: TierDecision
    tier: TierSpec
    account_currency: str
    uzs_per_usd: float
    fx_rate_date: str
    monthly_budget_uzs: int
    monthly_budget_usd: float
    flight_start: dt.date
    flight_end: dt.date
    flight_days: int
    flight_budget_uzs: int
    placements: dict[str, Any]
    ad_sets: list[AdSetPlan]
    interest_queries: list[str] = Field(
        description="Search terms to resolve into interest IDs via GET /search?type=adinterest&q=... "
        "(Meta recommends validating by ID, not by name)")
    measurement: MeasurementPlan
    warnings: list[str] = Field(default_factory=list)
