"""Phase 4 — deterministic Meta Ads media planner.

Same input → same plan. No LLM involved. Rules (highest tier wins, guardrails cap):
  PREMIUM  product launch flag | event in PREMIUM_EVENTS (Navruz) | last N days of Ramadan ("iftar peak")
  DRIVE    temperature ≥ heatwave threshold (default 38°C) | Ramadan | event priority ≥ threshold
  STANDARD otherwise
  CAP      remembrance day (LOW_KEY guardrail) → STANDARD + awareness only, no conversion push

Meta facts this module relies on (checked against developers.facebook.com, Sep 2026):
  • custom_locations radius 1-80 km; age_min ≥ 13 (18 default), age_max ≤ 65; genders 1 = male, 2 = female
  • instagram_positions "reels", facebook_positions "facebook_reels", audience_network_positions "classic"
  • Audience Network cannot run alone and requires Facebook in publisher_platforms
  • UZS is not a supported ad-account currency → budgets are converted to USD
  • optimization goals THRUPLAY / LINK_CLICKS / OFFSITE_CONVERSIONS exist
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from app.config import Settings
from app.models.context import AdContext
from app.models.media_plan import (
    AdSetPlan, BudgetTier, MeasurementPlan, MediaPlan, TierDecision, TierSpec, TierTrigger,
)
from app.services import media_config as cfg

EVENT_RULES = {"premium_event", "ramadan_month", "ramadan_iftar_peak", "high_priority_event"}
PREMIUM_EVENTS = {"navruz"}
DRIVE_EVENTS = {"ramadan"}
DAYS_PER_MONTH = 30.4
LEARNING_EVENTS_PER_WEEK = 50


class MediaPlannerService:
    def __init__(self, settings: Settings):
        self.s = settings

    # ------------------------------------------------------------------ tiers
    def decide_tier(self, ctx: AdContext, product_launch: bool = False) -> TierDecision:
        fired: list[TierTrigger] = []
        ev = ctx.primary_event
        temp = ctx.resolved_temperature_c

        if product_launch:
            fired.append(TierTrigger(rule="product_launch", tier=BudgetTier.PREMIUM,
                                     reason="Product launch flag set"))
        if ev and ev.key in PREMIUM_EVENTS:
            fired.append(TierTrigger(rule="premium_event", tier=BudgetTier.PREMIUM,
                                     reason=f"Top-tier national event: {ev.name_ru}"))
        if ev and ev.key == "ramadan":
            fired.append(TierTrigger(rule="ramadan_month", tier=BudgetTier.DRIVE,
                                     reason="Ramadan month: high-volume iftar period"))
            if ev.day_index and ev.days_total:
                peak_from = ev.days_total - self.s.ramadan_peak_last_days + 1
                if ev.day_index >= peak_from:
                    fired.append(TierTrigger(
                        rule="ramadan_iftar_peak", tier=BudgetTier.PREMIUM,
                        reason=f"Iftar peak: day {ev.day_index} of {ev.days_total} "
                               f"(last {self.s.ramadan_peak_last_days} days)"))
        if temp is not None and temp >= self.s.heatwave_temp_c:
            fired.append(TierTrigger(rule="heatwave", tier=BudgetTier.DRIVE,
                                     reason=f"Heatwave {temp:+.0f}°C ≥ {self.s.heatwave_temp_c:+.0f}°C "
                                            f"({ctx.temperature_source})"))
        if ev and ev.priority is not None and ev.priority >= self.s.drive_priority_threshold \
                and ev.key not in PREMIUM_EVENTS | DRIVE_EVENTS:
            fired.append(TierTrigger(rule="high_priority_event", tier=BudgetTier.DRIVE,
                                     reason=f"{ev.name_ru}: priority {ev.priority} ≥ {self.s.drive_priority_threshold}"))

        tier = max((t.tier for t in fired), key=lambda t: t.rank, default=BudgetTier.STANDARD)
        forced_by = None
        if ctx.is_low_key and tier != BudgetTier.STANDARD:
            tier, forced_by = BudgetTier.STANDARD, "LOW_KEY guardrail (remembrance day): awareness only"
        return TierDecision(tier=tier, fired=fired, forced_by=forced_by)

    # -------------------------------------------------------------- targeting
    def placements(self) -> dict[str, Any]:
        if self.s.include_audience_network:
            return {"publisher_platforms": ["instagram", "facebook", "audience_network"],
                    "instagram_positions": ["reels"],
                    "facebook_positions": ["facebook_reels"],
                    "audience_network_positions": ["classic"],
                    "device_platforms": ["mobile"]}
        return {"publisher_platforms": ["instagram"], "instagram_positions": ["reels"],
                "device_platforms": ["mobile"]}

    def targeting_spec(self, city_keys: list[str]) -> dict[str, Any]:
        return {
            "geo_locations": {
                "custom_locations": [
                    {"latitude": cfg.CITIES[k].latitude, "longitude": cfg.CITIES[k].longitude,
                     "radius": cfg.CITIES[k].radius_km, "distance_unit": "kilometer",
                     "name": cfg.CITIES[k].name_ru}
                    for k in city_keys],
                "location_types": ["home", "recent"],
            },
            "age_min": cfg.AGE_MIN,
            "age_max": cfg.AGE_MAX,
            "genders": cfg.GENDERS,
            **self.placements(),
            # interests are added after resolving IDs: "flexible_spec": [{"interests": [{"id": ...}]}]
        }

    @staticmethod
    def interest_queries(ctx: AdContext) -> list[str]:
        q = list(cfg.BASE_INTERESTS) + cfg.FRAMEWORK_INTERESTS.get(ctx.framework.name.value, [])
        q += [cfg.DISH_INTEREST[d.key] for d in ctx.dishes if d.key in cfg.DISH_INTEREST]
        return list(dict.fromkeys(q))       # de-duplicate, keep order

    # ------------------------------------------------------------ measurement
    def measurement(self, spec: TierSpec) -> tuple[MeasurementPlan, TierSpec, list[str]]:
        warnings: list[str] = []
        prereq = ["Ad account timezone = Asia/Tashkent (ad scheduling uses ADVERTISER time)",
                  f"Ad account currency = {self.s.meta_account_currency} (UZS is not supported by Meta)"]
        tracking = ["UTM tags on every link: utm_source=meta&utm_medium=paid&utm_campaign=<package_id>",
                    "Telegram deep link per ad set: t.me/<bot>?start=<adset_code> to count bot starts"]
        if spec.optimization_goal == "OFFSITE_CONVERSIONS" and not self.s.meta_conversion_tracking_ready:
            warnings.append("PREMIUM wants conversion optimization, but no Pixel/Conversions API dataset is "
                            "configured (BIOLIFE_META_CONVERSION_TRACKING_READY=false). Falling back to "
                            "OUTCOME_TRAFFIC / LINK_CLICKS until tracking is live.")
            spec = spec.model_copy(update={"campaign_objective": "OUTCOME_TRAFFIC", "optimization_goal": "LINK_CLICKS"})
            prereq.append("To unlock conversion optimization: send bot orders to Meta via Conversions API "
                          "(action_source='chat') from the Telegram bot backend, then set the flag to true")
        kpi = {"THRUPLAY": "Cost per ThruPlay", "LINK_CLICKS": "Cost per link click → bot start",
               "OFFSITE_CONVERSIONS": "Cost per order (CAPI)"}[spec.optimization_goal]
        return MeasurementPlan(primary_kpi=kpi, tracking=tracking, prerequisites=prereq), spec, warnings

    def ramadan_schedule(self) -> list[dict[str, Any]]:
        """After the LATEST iftar of the month until before the EARLIEST suhoor end (whole hours)."""
        start_h, end_h = self.s.ramadan_ads_start_hour, self.s.ramadan_ads_end_hour
        days = [0, 1, 2, 3, 4, 5, 6]
        return [{"start_minute": start_h * 60, "end_minute": 24 * 60, "days": days, "timezone_type": "ADVERTISER"},
                {"start_minute": 0, "end_minute": end_h * 60, "days": days, "timezone_type": "ADVERTISER"}]

    # ------------------------------------------------------------------ plan
    def calculate_plan(self, ctx: AdContext, product_launch: bool = False) -> MediaPlan:
        decision = self.decide_tier(ctx, product_launch)
        spec = cfg.TIERS[decision.tier]
        measurement, spec, warnings = self.measurement(spec)
        if decision.forced_by:
            spec = spec.model_copy(update={"campaign_objective": "OUTCOME_AWARENESS", "optimization_goal": "THRUPLAY"})
            warnings.append("Remembrance day: no sales/conversion campaigns; keep brand presence respectful.")

        rate = self.s.uzs_per_usd
        monthly_uzs = spec.monthly_budget_uzs
        daily_uzs_total = monthly_uzs / DAYS_PER_MONTH

        start = ctx.input.date or dt.date.today()
        end = start + dt.timedelta(days=29)
        ev = ctx.primary_event
        winning = [t for t in decision.fired if t.tier == decision.tier]
        event_driven = bool(winning) and all(t.rule in EVENT_RULES for t in winning) and not decision.forced_by
        if event_driven and ev and ev.window_end and ev.window_end >= start:
            end = min(end, ev.window_end)       # event-boosted tiers only run while the event lasts
        flight_days = (end - start).days + 1

        schedule = self.ramadan_schedule() if ev and ev.key == "ramadan" else None
        ad_sets: list[AdSetPlan] = []
        for name, cities, share in cfg.AD_SET_GROUPS[decision.tier]:
            daily_uzs = daily_uzs_total * share
            daily_usd = round(daily_uzs / rate, 2)
            weekly_usd = daily_usd * 7
            ad_sets.append(AdSetPlan(
                name=f"BioLife_{decision.tier.value}_{name}_{start:%Y%m%d}",
                cities=[cfg.CITIES[c].name_ru for c in cities],
                budget_share=share,
                monthly_budget_uzs=round(monthly_uzs * share),
                daily_budget_uzs=round(daily_uzs),
                daily_budget_usd=daily_usd,
                budget_type="LIFETIME" if schedule else "DAILY",
                lifetime_budget_usd=round(daily_usd * flight_days, 2) if schedule else None,
                max_cost_per_result_for_learning_usd=(None if spec.optimization_goal == "THRUPLAY"
                                                      else round(weekly_usd / LEARNING_EVENTS_PER_WEEK, 2)),
                creatives=["RU", "UZ"],
                targeting=self.targeting_spec(cities),
                adset_schedule=schedule,
                pacing_type=["day_parting"] if schedule else ["standard"],
            ))

        if self.s.include_audience_network:
            warnings.append("Audience Network requires Facebook placements (Meta rule), so Facebook Reels "
                            "is included. Set BIOLIFE_INCLUDE_AUDIENCE_NETWORK=false for Instagram Reels only.")
        if schedule:
            warnings.append(f"Ramadan: ads run {self.s.ramadan_ads_start_hour:02d}:00-{self.s.ramadan_ads_end_hour:02d}:00 "
                            "only (after iftar, before suhoor) with a lifetime budget. Verify against the month's "
                            "latest iftar and earliest suhoor in Tashkent before launch.")
        if spec.optimization_goal != "THRUPLAY":
            worst = min(a.max_cost_per_result_for_learning_usd or 0 for a in ad_sets)
            warnings.append(f"Learning phase check: at this budget an ad set needs a cost per {spec.optimization_goal} "
                            f"≤ ${worst:.2f} to reach ~{LEARNING_EVENTS_PER_WEEK} events/week. Compare with your "
                            "real CPC after 3-4 days; if higher, merge ad sets.")
        if ctx.temperature_source != "live_input":
            warnings.append("Temperature is a monthly average, not live data - pass a live reading to "
                            "resolve_ad_context() so heatwave triggers work.")

        return MediaPlan(
            decision=decision, tier=spec,
            account_currency=self.s.meta_account_currency, uzs_per_usd=rate, fx_rate_date=self.s.fx_rate_date,
            monthly_budget_uzs=monthly_uzs, monthly_budget_usd=round(monthly_uzs / rate, 2),
            flight_start=start, flight_end=end, flight_days=flight_days,
            flight_budget_uzs=round(daily_uzs_total * flight_days),
            placements=self.placements(), ad_sets=ad_sets,
            interest_queries=self.interest_queries(ctx),
            measurement=measurement, warnings=warnings,
        )
