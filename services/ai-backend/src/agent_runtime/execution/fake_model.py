"""Deterministic, network-free chat model for hermetic run→stream testing.

The desktop's real topology — supervised process, in-process worker, durable
store, streaming SSE — was untested end-to-end, which is how the AC2b worker
guard silently disabled all runs. Verifying that topology needs a run that
actually executes and *streams*, without a provider key or network. This module
supplies that: an env-gated fake that substitutes the concrete chat model at the
single construction funnel (:func:`agent_runtime.execution.deep_agent_builder.build_chat_model`)
while leaving the real Deep Agents graph, streaming executor, and event emission
untouched — so ``model_delta`` / ``reasoning_summary`` / ``final_response`` /
``run_completed`` are genuinely produced, not synthesized.

Fail-closed by construction: it activates ONLY when ``RUNTIME_FAKE_MODEL`` is an
explicit truthy value. The shipped desktop never sets it (``service-env.ts``
neither sets nor allowlists it), so a real user deployment cannot select it.
Activation is logged at WARNING so it is never silent.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable

logger = logging.getLogger(__name__)

# Reasoning content-block shapes the worker stream layer already parses
# (OpenAI Responses form) — see runtime_worker/stream_messages.py.
_REASONING_DELTA = "reasoning_summary_text_delta"
_REASONING_DONE = "reasoning_summary_text_done"

_TRUTHY = frozenset({"1", "true", "yes", "on", "enabled"})


class DeterministicFakeChatModel(BaseChatModel):
    """A ``BaseChatModel`` that streams a fixed reasoning span + text reply.

    Deterministic and offline: identical output for identical input, no key, no
    network. Streams so the real executor emits multiple ``model_delta`` events
    (and, when ``emit_reasoning`` is set, ``reasoning_summary`` events), then a
    ``final_response`` — exercising the streaming pipeline, not a single blob.
    """

    response_text: str = "Hello world from the deterministic fake model."
    reasoning_text: str = "Considering the request and forming a concise reply."
    emit_reasoning: bool = True

    @property
    def _llm_type(self) -> str:
        return "deterministic-fake"

    def _text_chunks(self) -> list[str]:
        # Split on spaces but keep the trailing space on each token so the
        # concatenated deltas reproduce the response verbatim.
        words = self.response_text.split(" ")
        return [w + (" " if i < len(words) - 1 else "") for i, w in enumerate(words)]

    def _reasoning_chunks(self) -> list[str]:
        words = self.reasoning_text.split(" ")
        return [w + (" " if i < len(words) - 1 else "") for i, w in enumerate(words)]

    def _final_message(self) -> AIMessage:
        return AIMessage(content=self.response_text)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._final_message())])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._final_message())])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        for chunk in self._iter_chunks():
            if run_manager is not None:
                run_manager.on_llm_new_token(chunk.text or "", chunk=chunk)
            yield chunk

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        for chunk in self._iter_chunks():
            if run_manager is not None:
                await run_manager.on_llm_new_token(chunk.text or "", chunk=chunk)
            yield chunk

    def _iter_chunks(self) -> Iterator[ChatGenerationChunk]:
        if self.emit_reasoning:
            for piece in self._reasoning_chunks():
                yield ChatGenerationChunk(
                    message=AIMessageChunk(
                        content=[{"type": _REASONING_DELTA, "text": piece}]
                    )
                )
            yield ChatGenerationChunk(
                message=AIMessageChunk(content=[{"type": _REASONING_DONE}])
            )
        for piece in self._text_chunks():
            yield ChatGenerationChunk(message=AIMessageChunk(content=piece))

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Runnable:
        # The fake never calls tools; accept and ignore the binding so the Deep
        # Agents graph (which binds its tool set to the model) still runs.
        return self


class FakeModelProvider:
    """Env-gated activation + construction of the deterministic fake model."""

    ENV_FLAG = "RUNTIME_FAKE_MODEL"

    @classmethod
    def is_enabled(cls) -> bool:
        """True only for an explicit truthy ``RUNTIME_FAKE_MODEL`` value.

        The shipped desktop neither sets nor allowlists this var, so a real
        deployment can never turn it on — it is a test/CI affordance only.
        """
        return os.environ.get(cls.ENV_FLAG, "").strip().lower() in _TRUTHY

    @classmethod
    def build(cls, model_config: object) -> BaseChatModel:
        """Return the deterministic fake, logging its activation loudly."""
        logger.warning(
            "fake_model_active",
            extra={
                "safe_message": (
                    "DETERMINISTIC FAKE MODEL is active (RUNTIME_FAKE_MODEL); "
                    "no real provider is called. This must never be a production path."
                )
            },
        )
        return DeterministicFakeChatModel()


__all__ = ["DeterministicFakeChatModel", "FakeModelProvider"]
