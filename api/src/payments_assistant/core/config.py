"""Typed settings, read from the environment (compose `env_file` + per-service `environment`)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # External services
    stripe_secret_key: str
    stripe_webhook_secret: str = ""
    stripe_webhook_secret_file: Path = Path("/run/stripe/whsec")
    openrouter_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""

    # LLM
    llm_model: str = "openrouter:moonshotai/kimi-k2.6"
    llm_summary_model: str = ""
    llm_temperature: float = 0.0
    llm_max_tool_hops: int = Field(6, ge=1, le=20)

    # Business rules
    business_timezone: str = "America/New_York"
    default_currency: str = "usd"
    handoff_threshold_cents: int = Field(200_000, gt=0)

    # Owner seed
    owner_email: str = ""
    owner_password: str = ""

    # Auth / security tuning (code defaults; override via env)
    session_ttl_days: int = 7
    invite_ttl_days: int = 7
    action_proposal_ttl_minutes: int = 10
    login_max_attempts: int = 5
    login_window_minutes: int = 15

    # Wiring
    database_url: str = ""
    environment: str = "development"
    log_level: str = "info"

    @field_validator("stripe_secret_key")
    @classmethod
    def _test_mode_only(cls, v: str) -> str:
        if not v.startswith("sk_test_"):
            raise ValueError("STRIPE_SECRET_KEY must be a test-mode key (sk_test_...)")
        return v

    @property
    def summary_model(self) -> str:
        return self.llm_summary_model or self.llm_model

    def webhook_secret(self) -> str:
        """Explicit env value wins; otherwise the file written by the stripe-cli service."""
        if self.stripe_webhook_secret:
            return self.stripe_webhook_secret
        try:
            return self.stripe_webhook_secret_file.read_text().strip()
        except FileNotFoundError:
            return ""


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # required fields come from env
