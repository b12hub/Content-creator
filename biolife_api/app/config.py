from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import AliasChoices, BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CtaChannel(BaseModel):
    key: str
    label_ru: str         # how the channel is named in Russian copy
    label_uz: str         # how the channel is named in Uzbek (Latin) copy
    action_hint: str      # what the user does there
    handle: str | None = None   # filled into the response from config, never generated
    url: str | None = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="BIOLIFE_", extra="ignore",
                                      populate_by_name=True)   # lets code/tests set aliased fields

    # API keys live WITHOUT the BIOLIFE_ prefix in .env (that is how the SDKs document them), so they
    # need an explicit validation_alias: env_prefix is not applied when an alias is given.
    # pydantic-settings reads .env itself and does NOT copy values into os.environ, which is why the
    # SDK could not find the key on its own.
    anthropic_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY", "BIOLIFE_ANTHROPIC_API_KEY"))
    openai_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("OPENAI_API_KEY", "BIOLIFE_OPENAI_API_KEY"))

    llm_provider: Literal["anthropic", "openai", "fake"] = "anthropic"
    anthropic_model: str = "claude-sonnet-5"
    openai_model: str = ""            # set explicitly, e.g. BIOLIFE_OPENAI_MODEL=...
    # Optional override for the Telegram copywriter (plain text, no JSON schema). Leave empty to
    # reuse anthropic_model. Structured-output endpoints need Sonnet 4.5+/Opus 4.5+/Haiku 4.5, but
    # free text works on older models too, e.g. claude-3-5-sonnet-20240620.
    telegram_llm_model: str = ""
    telegram_llm_max_tokens: int = 2000
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

    # ---- Telegram bot (aiogram 3.x over FastAPI webhook) ------------------------
    telegram_bot_token: SecretStr | None = None          # BIOLIFE_TELEGRAM_BOT_TOKEN
    telegram_webhook_secret: SecretStr | None = None     # any random 1-256 char string [A-Za-z0-9_-]
    telegram_webhook_base_url: str = ""                  # https://api.biolife.uz  (must be HTTPS)
    telegram_webhook_path: str = "/telegram/webhook"
    telegram_drop_pending_updates: bool = True
    telegram_delete_webhook_on_shutdown: bool = False    # True only for local/dev tunnels
    telegram_max_connections: int = 40
    telegram_min_interval_s: float = 0.4                 # per-user throttle
    # Internal bot: Telegram user ids of the marketing team. Empty = everyone (dev only).
    telegram_allowed_user_ids: list[int] = []

    # ---- validation & derived values -------------------------------------------
    @field_validator("telegram_webhook_path", mode="before")
    @classmethod
    def _normalise_path(cls, value: object) -> str:
        """The PATH must be a local route ('/telegram/webhook'), never a full URL.
        A pasted URL is reduced to its path instead of breaking routing."""
        raw = str(value or "").strip()
        if "://" in raw:
            raw = urlsplit(raw).path or "/"
        if not raw.startswith("/"):
            raw = "/" + raw
        return raw.rstrip("/") or "/"

    @field_validator("telegram_webhook_base_url", mode="before")
    @classmethod
    def _normalise_base(cls, value: object) -> str:
        """The BASE URL must be scheme + host only, e.g. https://xxx.ngrok-free.dev."""
        raw = str(value or "").strip().rstrip("/")
        if not raw:
            return ""
        parts = urlsplit(raw)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("BIOLIFE_TELEGRAM_WEBHOOK_BASE_URL must be an absolute https URL, "
                             f"e.g. https://your-tunnel.ngrok-free.dev (got {raw!r})")
        return raw

    @model_validator(mode="after")
    def _drop_duplicated_path_from_base(self) -> "Settings":
        """Telegram gets base + path. If the base already carries a path (a common copy-paste
        mistake, e.g. base '.../webhook'), the two are concatenated and every update 404s.
        The path segment is dropped here and the app logs what it used."""
        if self.telegram_webhook_base_url:
            parts = urlsplit(self.telegram_webhook_base_url)
            if parts.path:
                self.base_url_had_path = parts.path
                self.telegram_webhook_base_url = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        return self

    base_url_had_path: str = ""          # set when a path was stripped, for a startup warning

    @property
    def telegram_webhook_url(self) -> str:
        """The single source of truth for the public webhook URL."""
        if not self.telegram_webhook_base_url:
            return ""
        return self.telegram_webhook_base_url + self.telegram_webhook_path


@lru_cache
def get_settings() -> Settings:
    return Settings()
