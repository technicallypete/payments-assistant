"""A scripted chat model for agent tests: streams text and tool calls like a real provider.

Each script turn is an `AIMessage` (content and/or tool_calls). Streaming splits content into
word-ish tokens and tool-call args into JSON fragments, mimicking OpenRouter's chunking, so the
loop's chunk accumulation is exercised for real.
"""

import json
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import PrivateAttr


def reply(text: str = "", *calls: tuple[str, dict[str, Any]]) -> AIMessage:
    """Script helper: reply("Sure!", ("find_payments", {"customer_query": "Maya"}))."""
    return AIMessage(
        content=text,
        tool_calls=[
            {"name": n, "args": a, "id": f"call_{i}_{n}", "type": "tool_call"}
            for i, (n, a) in enumerate(calls)
        ],
    )


class ScriptedChatModel(BaseChatModel):
    script: list[AIMessage]
    fail_on_turn: int | None = None  # raise on this (0-based) turn to test error handling
    _turn: int = PrivateAttr(default=0)
    _seen: list[list[BaseMessage]] = PrivateAttr(default_factory=list)
    _bound_tools: list[Any] = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    @property
    def seen(self) -> list[list[BaseMessage]]:
        """The message list the model received on each turn."""
        return self._seen

    @property
    def bound_tools(self) -> list[Any]:
        return self._bound_tools

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ScriptedChatModel":
        self._bound_tools = list(tools)
        return self

    def _next(self, messages: list[BaseMessage]) -> AIMessage:
        self._seen.append(list(messages))
        if self.fail_on_turn is not None and self._turn == self.fail_on_turn:
            raise RuntimeError("provider exploded")
        if self._turn >= len(self.script):
            raise AssertionError("model called more times than scripted")
        msg = self.script[self._turn]
        self._turn += 1
        return msg

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        yield from _chunks(self._next(messages))

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        for c in _chunks(self._next(messages)):
            yield c


def _chunks(msg: AIMessage) -> Iterator[ChatGenerationChunk]:
    text = msg.content if isinstance(msg.content, str) else ""
    words = text.split(" ")
    for i, w in enumerate(words):
        piece = w if i == 0 else " " + w
        if piece:
            yield ChatGenerationChunk(message=AIMessageChunk(content=piece))
    for idx, call in enumerate(msg.tool_calls):
        raw = json.dumps(call["args"])
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {"name": call["name"], "args": "", "id": call["id"], "index": idx}
                ],
            )
        )
        for j in range(0, len(raw), 7):
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {"name": None, "args": raw[j : j + 7], "id": None, "index": idx}
                    ],
                )
            )
    yield ChatGenerationChunk(
        message=AIMessageChunk(
            content="",
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )
    )
