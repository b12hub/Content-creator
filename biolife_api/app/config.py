from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class CtaChannel(BaseModel):
    key: str
    label_ru: str         # how the channel is named in Russian copy
    label_uz: str         # how the channel is named in Uzbek (Latin) copy
    action_hint: str      # what the user does there
    handle: str | None = None   # filled into the response from config, never generated
    url: str | None = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="BIOLIFE_", extra="ignore")

    llm_provider: Literal["anthropic", "openai", "fake"] = "anthropic"
    anthropic_model: str = "claude-sonnet-5"
    openai_model: str = ""            # set explicitly, e.g. BIOLIFE_OPENAI_MODEL=...
    max_output_tokens: int = 4000
    llm_timeout_s: float = 90.0
    max_attempts: int = 2             # 1 generation + 1 self-repair retry

    # !!! VERIFY before going live: handles/links below must be BioLife's REAL channels.
    # The LLM may only pick a key; labels/handles/urls are injected from here.
    cta_channels: list[CtaChannel] = [
        CtaChannel(key="telegram_bot", label_ru="Telegram-бот BioLife (@BioLifeUz_bot)",
                   label_uz="BioLife Telegram-boti (@BioLifeUz_bot)",
                   action_hint="order delivery to home or office in 2 taps",
                   handle="@BioLifeUz_bot", url="https://t.me/BioLifeUz_bot"),
        CtaChannel(key="yandex_go", label_ru="Yandex Go", label_uz="Yandex Go",
                   action_hint="add BioLife to the food/grocery order in Yandex Go"),
        CtaChannel(key="uzum_tezkor", label_ru="Uzum Tezkor", label_uz="Uzum Tezkor",
                   action_hint="add BioLife to the grocery/food order"),
        CtaChannel(key="nearest_store", label_ru="ближайший магазин", label_uz="eng yaqin do‘kon",
                   action_hint="grab a chilled bottle from the fridge"),
    ]

    # ---- Phase 4: media planner -------------------------------------------------
    # Meta ad accounts cannot be billed in UZS (not in Meta's supported currency list), so API budgets
    # are converted to USD. Update the rate from cbu.uz (1 USD = 11,839.59 UZS on 2026-09-19).
    meta_account_currency: str = "USD"
    uzs_per_usd: float = 11839.59
    fx_rate_date: str = "2026-09-19"
    heatwave_temp_c: float = 38.0
    drive_priority_threshold: int = 85        # events at/above this priority -> at least DRIVE
    ramadan_peak_last_days: int = 10          # "iftar peak" = last N days of Ramadan -> PREMIUM
    # Ramadan ad window (whole hours, Asia/Tashkent). Defaults fit Ramadan 2026-2028 (Jan-Mar):
    # iftar ~17:30-18:30, suhoor ends ~05:30-06:00. Re-check every year as Ramadan moves ~11 days earlier.
    ramadan_ads_start_hour: int = 19
    ramadan_ads_end_hour: int = 5
    include_audience_network: bool = True     # Meta rule: AN requires Facebook placements too
    meta_conversion_tracking_ready: bool = False   # True only after Pixel/Conversions API dataset is live
    meta_pixel_id: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
