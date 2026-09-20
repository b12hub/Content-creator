"""Phase 4 media planner + unified endpoint. Fixtures are real resolve_ad_context() outputs (DB v1.1)."""
import copy

import pytest

from app.config import get_settings
from app.models.context import AdContext
from app.models.media_plan import BudgetTier
from app.services.media_planner import MediaPlannerService
from tests.conftest import load

SIX_CITIES = {"Ташкент", "Самарканд", "Наманган", "Андижан", "Фергана", "Бухара"}


def plan(fixture, settings=None, **kw):
    return MediaPlannerService(settings or get_settings()).calculate_plan(
        AdContext.model_validate(load(fixture)), **kw)


# ---------------------------------------------------------------- tier selection
@pytest.mark.parametrize("fixture,tier,rule", [
    ("chilla_43C", BudgetTier.DRIVE, "heatwave"),              # 43°C ≥ 38
    ("chilla_36C", BudgetTier.STANDARD, None),                 # Chilla but below threshold
    ("ramadan_iftar", BudgetTier.DRIVE, "ramadan_month"),      # day 13 of 29
    ("ramadan_peak", BudgetTier.PREMIUM, "ramadan_iftar_peak"),  # day 22 of 29 → last 10 days
    ("navruz", BudgetTier.PREMIUM, "premium_event"),
    ("new_year", BudgetTier.DRIVE, "high_priority_event"),     # priority 90 ≥ 85
    ("wedding_norin", BudgetTier.STANDARD, None),
    ("autumn_plain", BudgetTier.STANDARD, None),
])
def test_tier_selection(fixture, tier, rule):
    p = plan(fixture)
    assert p.decision.tier == tier
    if rule:
        assert rule in {t.rule for t in p.decision.fired}
    assert p.tier.monthly_budget_uzs == {BudgetTier.STANDARD: 1_500_000, BudgetTier.DRIVE: 3_000_000,
                                         BudgetTier.PREMIUM: 8_000_000}[tier]


def test_heat_43_with_launch_goes_premium():
    p = plan("chilla_43C", product_launch=True)
    assert p.decision.tier == BudgetTier.PREMIUM
    assert {"heatwave", "product_launch"} <= {t.rule for t in p.decision.fired}


def test_heatwave_threshold_is_inclusive_and_configurable():
    ctx = load("chilla_36C"); ctx["resolved_temperature_c"] = 38
    p = MediaPlannerService(get_settings()).calculate_plan(AdContext.model_validate(ctx))
    assert p.decision.tier == BudgetTier.DRIVE
    s = get_settings().model_copy(update={"heatwave_temp_c": 40.0})
    assert MediaPlannerService(s).calculate_plan(AdContext.model_validate(ctx)).decision.tier == BudgetTier.STANDARD


def test_memorial_day_is_capped_to_awareness():
    p = plan("memorial_day", product_launch=True)
    assert p.decision.tier == BudgetTier.STANDARD and p.decision.forced_by
    assert p.tier.optimization_goal == "THRUPLAY"


def test_old_payload_without_new_db_fields_still_works():
    ctx = copy.deepcopy(load("ramadan_peak"))
    for k in ("priority", "window_start", "window_end", "day_index", "days_total"):
        ctx["primary_event"].pop(k)
    p = MediaPlannerService(get_settings()).calculate_plan(AdContext.model_validate(ctx))
    assert p.decision.tier == BudgetTier.DRIVE        # peak unknown → month rule only


# ---------------------------------------------------------------- targeting
@pytest.mark.parametrize("fixture", ["chilla_43C", "navruz", "autumn_plain"])
def test_targeting_covers_six_cities_and_demographics(fixture):
    p = plan(fixture)
    cities = {c for a in p.ad_sets for c in a.cities}
    assert cities == SIX_CITIES
    for a in p.ad_sets:
        t = a.targeting
        assert (t["age_min"], t["age_max"]) == (18, 45) and t["genders"] == [1, 2]
        for loc in t["geo_locations"]["custom_locations"]:
            assert 1 <= loc["radius"] <= 80 and loc["distance_unit"] == "kilometer"
        assert t["instagram_positions"] == ["reels"]


def test_audience_network_always_paired_with_facebook():
    p = plan("chilla_43C")
    pp = p.placements["publisher_platforms"]
    assert "audience_network" in pp and "facebook" in pp and "facebook_reels" in p.placements["facebook_positions"]
    s = get_settings().model_copy(update={"include_audience_network": False})
    assert plan("chilla_43C", settings=s).placements["publisher_platforms"] == ["instagram"]


def test_budget_math_and_currency():
    p = plan("chilla_43C")                                   # DRIVE, 2 ad sets
    assert p.account_currency == "USD" and p.uzs_per_usd > 1000
    assert sum(a.budget_share for a in p.ad_sets) == pytest.approx(1.0)
    assert sum(a.monthly_budget_uzs for a in p.ad_sets) == pytest.approx(3_000_000, abs=2)
    daily_usd = sum(a.daily_budget_usd for a in p.ad_sets)
    assert daily_usd == pytest.approx(3_000_000 / 30.4 / p.uzs_per_usd, abs=0.02)
    for a in p.ad_sets:                                      # weekly budget / 50 events
        assert a.max_cost_per_result_for_learning_usd == pytest.approx(a.daily_budget_usd * 7 / 50, abs=0.01)


def test_flight_ends_with_trigger_event():
    p = plan("ramadan_iftar")                                # Ramadan 2027 ends 2027-03-08 (estimate)
    assert str(p.flight_end) == "2027-03-08" and p.flight_days == 17
    assert plan("chilla_43C").flight_days == 30              # heatwave is weather-driven, not event-driven


def test_ramadan_dayparting_uses_lifetime_budget():
    p = plan("ramadan_iftar")
    for a in p.ad_sets:
        assert a.budget_type == "LIFETIME" and a.lifetime_budget_usd
        assert a.adset_schedule and all(r["start_minute"] % 60 == 0 for r in a.adset_schedule)


def test_premium_without_tracking_falls_back_to_traffic():
    p = plan("navruz")
    assert p.tier.optimization_goal == "LINK_CLICKS" and any("PREMIUM wants" in w for w in p.warnings)
    s = get_settings().model_copy(update={"meta_conversion_tracking_ready": True})
    assert plan("navruz", settings=s).tier.optimization_goal == "OFFSITE_CONVERSIONS"


def test_no_religious_interest_targeting():
    q = " ".join(plan("ramadan_peak").interest_queries).lower()
    assert not any(w in q for w in ("islam", "muslim", "ramadan", "religion"))


def test_plan_is_deterministic():
    assert plan("navruz").model_dump() == plan("navruz").model_dump()


# ---------------------------------------------------------------- unified endpoint
@pytest.mark.parametrize("fixture,tier,handler", [
    ("chilla_43C", "DRIVE", "dopamine_behavioral"),
    ("navruz", "PREMIUM", "hybrid_spring_renewal"),
    ("ramadan_peak", "PREMIUM", "hippocampus_emotional"),
    ("autumn_plain", "STANDARD", "hippocampus_emotional"),
])
def test_generate_campaign_package_fake_mode(make_client, fixture, tier, handler):
    client, fake = make_client()
    r = client.post("/generate-campaign-package", json=load(fixture))
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["media_plan"]["decision"]["tier"] == tier
    assert b["generation"]["handler"] == handler
    assert set(b["creative"]) == {"strategic_selection", "visual_hook", "script", "call_to_action"}
    cta = b["creative"]["call_to_action"]
    assert cta["handle"] == "@BioLifeUz_bot" and cta["url"] == "https://t.me/BioLifeUz_bot"
    assert b["package_id"].endswith(tier.lower())
    assert len(fake.calls) == 1


def test_campaign_package_product_launch_query(make_client):
    client, _ = make_client()
    r = client.post("/generate-campaign-package?product_launch=true", json=load("autumn_plain"))
    assert r.json()["media_plan"]["decision"]["tier"] == "PREMIUM"


def test_campaign_package_blocked_on_religious_review(make_client):
    client, _ = make_client()
    b = client.post("/generate-campaign-package", json=load("ramadan_peak")).json()
    assert b["ready_to_launch"] is False and any("Religious" in x for x in b["blockers"])


def test_campaign_package_invalid_payload_422(make_client):
    client, fake = make_client()
    bad = load("navruz"); del bad["framework"]
    assert client.post("/generate-campaign-package", json=bad).status_code == 422
    assert fake.calls == []                                  # no paid LLM call on bad input


def test_reel_endpoint_still_backward_compatible(make_client):
    client, _ = make_client()
    b = client.post("/generate-reel-script", json=load("chilla_43C")).json()
    assert set(b) == {"strategic_selection", "visual_hook", "script", "call_to_action", "meta", "quality"}


# ---- review findings ----
def test_heatwave_on_one_day_event_keeps_full_month_flight():
    ctx = load("memorial_day"); ctx["guardrails"] = []; ctx["resolved_temperature_c"] = 40
    p = MediaPlannerService(get_settings()).calculate_plan(AdContext.model_validate(ctx))
    assert p.decision.tier == BudgetTier.DRIVE and p.flight_days == 30


def test_launch_on_navruz_is_not_cut_to_event_window():
    assert plan("navruz", product_launch=True).flight_days == 30
    assert plan("navruz").flight_days == 5            # Mar 21 → Mar 25 (event-driven)


def test_ramadan_schedule_after_iftar_and_day_parting():
    a = plan("ramadan_iftar").ad_sets[0]
    assert a.adset_schedule[0]["start_minute"] >= 19 * 60 and a.pacing_type == ["day_parting"]


def test_premium_fallback_blocks_launch(make_client):
    client, _ = make_client()
    b = client.post("/generate-campaign-package", json=load("navruz")).json()
    assert b["ready_to_launch"] is False and any("PREMIUM wants" in x for x in b["blockers"])
