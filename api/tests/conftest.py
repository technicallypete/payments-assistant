"""Shared fixtures. Unit tests must not need a DB, network, or real secrets."""

import pytest

from payments_assistant.core.config import Settings

# DB fixtures (migrated throwaway database + per-role engines), shared by integration and
# stripe_live tests so there is exactly one test database per run.
pytest_plugins = ["tests.db_fixtures"]


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Settings built explicitly, independent of whatever is in the container env."""
    return Settings(
        stripe_secret_key="sk_test_unit",
        stripe_webhook_secret_file=tmp_path / "whsec",
        _env_file=None,  # type: ignore[call-arg]
    )
