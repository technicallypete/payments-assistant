from payments_assistant.core.config import Settings
from payments_assistant.core.llm import OPENROUTER_BASE_URL, get_chat_model


def _settings(**kw) -> Settings:
    return Settings(stripe_secret_key="sk_test_x", openrouter_api_key="sk-or-test", **kw)


def test_openrouter_spec_uses_openai_compatible_client_with_timeouts():
    m = get_chat_model(_settings(llm_timeout_seconds=12, llm_max_retries=0))
    assert type(m).__name__ == "ChatOpenAI"
    assert m.model_name == "moonshotai/kimi-k2.6"
    assert m.openai_api_base == OPENROUTER_BASE_URL
    assert m.request_timeout == 12
    assert m.max_retries == 0
    assert m.stream_usage is True


def test_summary_model_override():
    m = get_chat_model(
        _settings(llm_summary_model="openrouter:deepseek/deepseek-v4-pro"), purpose="summary"
    )
    assert m.model_name == "deepseek/deepseek-v4-pro"


def test_reasoning_effort_mapping():
    assert get_chat_model(_settings(llm_reasoning_effort="none")).extra_body == {
        "reasoning": {"enabled": False}
    }
    assert get_chat_model(_settings(llm_reasoning_effort="low")).extra_body == {
        "reasoning": {"effort": "low"}
    }
    assert get_chat_model(_settings()).extra_body is None
