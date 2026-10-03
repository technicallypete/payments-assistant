"""Chat model factory. The model is chosen by env (`LLM_MODEL="provider:model"`), never in code.

`openrouter:<model>` is served through LangChain's OpenAI-compatible client pointed at OpenRouter.
We tried `langchain-openrouter` first, but its SDK retried timed-out streaming requests with
backoff regardless of our settings, which hung chat turns for minutes. The OpenAI client honours
`timeout` / `max_retries`, and OpenRouter's API is OpenAI-compatible.
"""

from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from payments_assistant.core.config import Settings

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def get_chat_model(settings: Settings, *, purpose: str = "agent") -> BaseChatModel:
    spec = settings.summary_model if purpose == "summary" else settings.llm_model
    kwargs: dict[str, Any] = {
        "temperature": settings.llm_temperature,
        "timeout": settings.llm_timeout_seconds,
        "max_retries": settings.llm_max_retries,
    }
    provider, _, model = spec.partition(":")
    if provider == "openrouter":
        extra_body: dict[str, Any] = {}
        effort = (
            settings.llm_summary_reasoning_effort
            if purpose == "summary"
            else settings.llm_reasoning_effort
        )
        if effort:
            # OpenRouter's unified reasoning control; "none" disables thinking for faster turns.
            extra_body["reasoning"] = {"enabled": False} if effort == "none" else {"effort": effort}
        return init_chat_model(
            model,
            model_provider="openai",
            base_url=OPENROUTER_BASE_URL,
            api_key=settings.openrouter_api_key,
            stream_usage=True,
            extra_body=extra_body or None,
            **kwargs,
        )
    return init_chat_model(spec, **kwargs)
