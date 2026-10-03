import pytest
from pydantic import ValidationError

from payments_assistant.core.config import Settings


def test_rejects_live_stripe_key():
    with pytest.raises(ValidationError, match="test-mode"):
        Settings(stripe_secret_key="sk_live_nope")


def test_rejects_restricted_or_garbage_key():
    with pytest.raises(ValidationError):
        Settings(stripe_secret_key="rk_test_123")


def test_defaults(settings):
    assert settings.business_timezone == "America/New_York"
    assert settings.handoff_threshold_cents == 200_000
    assert settings.llm_model == "openrouter:moonshotai/kimi-k2.6"
    assert settings.summary_model == settings.llm_model


def test_summary_model_override():
    s = Settings(stripe_secret_key="sk_test_x", llm_summary_model="openrouter:z-ai/glm-5.3")
    assert s.summary_model == "openrouter:z-ai/glm-5.3"


def test_webhook_secret_env_wins_over_file(settings):
    settings.stripe_webhook_secret_file.write_text("whsec_from_file\n")
    settings.stripe_webhook_secret = "whsec_from_env"
    assert settings.webhook_secret() == "whsec_from_env"


def test_webhook_secret_falls_back_to_file(settings):
    settings.stripe_webhook_secret_file.write_text("whsec_from_file\n")
    assert settings.webhook_secret() == "whsec_from_file"


def test_webhook_secret_missing_file_is_empty(settings):
    assert settings.webhook_secret() == ""
