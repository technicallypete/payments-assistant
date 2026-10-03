"""Mark everything under tests/integration as `integration`. DB fixtures: tests/db_fixtures.py."""

import pytest


def pytest_collection_modifyitems(items):
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
