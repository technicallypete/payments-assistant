"""LLM evals call the real model through OpenRouter: opt-in (`-m llm_eval`) and budget-limited
(docs/v0/goal.md: at most 3 runs). Skipped entirely without a real OPENROUTER_API_KEY."""

import os

import pytest


def pytest_collection_modifyitems(config, items):
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if key and "replace_me" not in key:
        return
    skip = pytest.mark.skip(reason="needs a real OPENROUTER_API_KEY")
    for item in items:
        if "llm_eval" in item.keywords:
            item.add_marker(skip)
