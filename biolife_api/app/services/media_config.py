"""Static media-planning knowledge. Edit here, not in the planner logic."""
from __future__ import annotations

from app.models.media_plan import BudgetTier, CityTarget, TierSpec

TIERS: dict[BudgetTier, TierSpec] = {
    BudgetTier.STANDARD: TierSpec(
        tier=BudgetTier.STANDARD, monthly_budget_uzs=1_500_000, reels_min=4, reels_max=4,
        production="In-house smartphone shoot + product macro; no field crew",
        goal="Primary organic reach with lightweight paid amplification",
        campaign_objective="OUTCOME_AWARENESS", optimization_goal="THRUPLAY"),
    BudgetTier.DRIVE: TierSpec(
        tier=BudgetTier.DRIVE, monthly_budget_uzs=3_000_000, reels_min=8, reels_max=10,
        production="1 field shoot per month (street food / choyxona / iftar table) cut into 8-10 Reels",
        goal="Stable click flow to the Telegram bot and delivery apps; feed Meta's learning phase",
        campaign_objective="OUTCOME_TRAFFIC", optimization_goal="LINK_CLICKS"),
    BudgetTier.PREMIUM: TierSpec(
        tier=BudgetTier.PREMIUM, monthly_budget_uzs=8_000_000, reels_min=10, reels_max=None,
        production="Studio production with actors, dynamic editing, separate RU/UZ voiceover sessions",
        goal="Market capture and dominant share of voice across the 6 cities",
        campaign_objective="OUTCOME_SALES", optimization_goal="OFFSITE_CONVERSIONS"),
}

# City-centre coordinates (approximate) + radius. custom_locations need no Meta city keys.
CITIES: dict[str, CityTarget] = {c.key: c for c in [
    CityTarget(key="tashkent",  name_ru="Ташкент",   name_uz="Toshkent",  latitude=41.2995, longitude=69.2401, radius_km=25),
    CityTarget(key="samarkand", name_ru="Самарканд", name_uz="Samarqand", latitude=39.6542, longitude=66.9597, radius_km=15),
    CityTarget(key="namangan",  name_ru="Наманган",  name_uz="Namangan",  latitude=40.9983, longitude=71.6726, radius_km=15),
    CityTarget(key="andijan",   name_ru="Андижан",   name_uz="Andijon",   latitude=40.7821, longitude=72.3442, radius_km=15),
    CityTarget(key="fergana",   name_ru="Фергана",   name_uz="Farg‘ona",  latitude=40.3864, longitude=71.7864, radius_km=15),
    CityTarget(key="bukhara",   name_ru="Бухара",    name_uz="Buxoro",    latitude=39.7747, longitude=64.4286, radius_km=15),
]}

# Ad-set structure per tier: few, well-funded ad sets learn faster than many thin ones.
AD_SET_GROUPS: dict[BudgetTier, list[tuple[str, list[str], float]]] = {
    BudgetTier.STANDARD: [("UZ-6cities", list(CITIES), 1.0)],
    BudgetTier.DRIVE: [("Tashkent", ["tashkent"], 0.5),
                       ("Regions", ["samarkand", "namangan", "andijan", "fergana", "bukhara"], 0.5)],
    BudgetTier.PREMIUM: [("Tashkent", ["tashkent"], 0.45),
                         ("FerganaValley", ["namangan", "andijan", "fergana"], 0.30),
                         ("Samarkand-Bukhara", ["samarkand", "bukhara"], 0.25)],
}

AGE_MIN, AGE_MAX = 18, 45
GENDERS = [1, 2]          # Meta: 1 = male, 2 = female

BASE_INTERESTS = ["Uzbek cuisine", "Pilaf", "Food delivery", "Online food ordering", "Grocery delivery",
                  "Yandex Go", "Express24", "Physical fitness", "Outdoor recreation"]
FRAMEWORK_INTERESTS = {
    "PEPSI_BEHAVIORAL_DOPAMINE": ["Street food", "Barbecue", "Fast food", "Music festivals"],
    "COCA_COLA_EMOTIONAL": ["Family", "Home cooking", "Weddings", "Parenting"],
    "HYBRID_SPRING_RENEWAL": ["Travel", "Picnics", "Gardening", "Healthy lifestyle"],
}
DISH_INTEREST = {"osh": "Pilaf", "shashlik": "Shashlik", "somsa": "Samsa", "lagmon": "Lagman",
                 "manti": "Manti", "kazan_kabob": "Barbecue", "norin": "Central Asian cuisine",
                 "sumalak": "Nowruz", "shurva": "Soup"}
