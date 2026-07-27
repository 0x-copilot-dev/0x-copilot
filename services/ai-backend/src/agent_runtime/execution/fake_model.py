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

import json
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
from pydantic import Field

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

    tool_calls_before_final: int = 0
    """Tool-call turns to take before answering. ``0`` never calls a tool.

    Set this to drive the real agent loop through repeated tool dispatch —
    the only way to exercise per-run caps, tool-result surfacing, and
    recovery paths end to end. The turn count is derived from the tool
    messages already in the prompt rather than from instance state, so the
    model stays deterministic and replay-safe.
    """

    tool_call_name: str = "ls"
    tool_call_args: dict[str, Any] = Field(default_factory=lambda: {"path": "/"})

    parallel_tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    """Tool calls to emit together in ONE turn, e.g. ``[{"name": ..., "args": {...}}]``.

    Set this to drive genuinely parallel graph branches — the only way to
    reproduce a run holding several *concurrent* LangGraph interrupts (each
    branch parks in its own task namespace), which is a different shape from
    one interrupt carrying several ``action_requests``.
    """

    parallel_trigger: str = ""
    """Only fan out when the latest human message contains this substring.

    Keeps the fan-out scoped to the turn that should branch; empty means always.
    """

    @property
    def _llm_type(self) -> str:
        return "deterministic-fake"

    @staticmethod
    def _completed_tool_turns(messages: Sequence[BaseMessage]) -> int:
        """Count tool results already in the prompt — the loop's turn index."""
        return sum(1 for message in messages if message.type == "tool")

    def _wants_tool_call(self, messages: Sequence[BaseMessage]) -> bool:
        """True while the scripted tool-call turns are not yet used up."""
        return self._completed_tool_turns(messages) < self.tool_calls_before_final

    def _tool_call_id(self, messages: Sequence[BaseMessage]) -> str:
        return f"fake_call_{self._completed_tool_turns(messages)}"

    def _text_chunks(self) -> list[str]:
        # Split on spaces but keep the trailing space on each token so the
        # concatenated deltas reproduce the response verbatim.
        words = self.response_text.split(" ")
        return [w + (" " if i < len(words) - 1 else "") for i, w in enumerate(words)]

    def _reasoning_chunks(self) -> list[str]:
        words = self.reasoning_text.split(" ")
        return [w + (" " if i < len(words) - 1 else "") for i, w in enumerate(words)]

    @staticmethod
    def _latest_human_text(messages: Sequence[BaseMessage]) -> str:
        """Return the text of the most recent human message, or ``""``."""
        for message in reversed(messages):
            if message.type == "human":
                content = message.content
                return content if isinstance(content, str) else str(content)
        return ""

    def _scripted_calls(self, messages: Sequence[BaseMessage]) -> list[dict[str, Any]]:
        """Return this turn's tool calls — the fan-out script, else the single call.

        ``parallel_trigger`` scopes the fan-out to the prompt that carries it, so
        a run whose fan-out targets are subagents does not recurse: the parent's
        user message contains the trigger and emits N calls, while each
        subagent's prompt (its task description) does not.
        """
        fans_out = bool(self.parallel_tool_calls) and (
            not self.parallel_trigger
            or self.parallel_trigger in self._latest_human_text(messages)
        )
        if not fans_out:
            return [
                {
                    "name": self.tool_call_name,
                    "args": dict(self.tool_call_args),
                    "id": self._tool_call_id(messages),
                }
            ]
        return [
            {
                "name": call.get("name", self.tool_call_name),
                "args": dict(call.get("args", {})),
                "id": f"fake_parallel_{index}",
            }
            for index, call in enumerate(self.parallel_tool_calls)
        ]

    def _final_message(self, messages: Sequence[BaseMessage]) -> AIMessage:
        if self._wants_tool_call(messages):
            # One AIMessage carrying N tool calls — LangGraph runs them in a
            # single superstep, so each target lands in its own task namespace.
            return AIMessage(content="", tool_calls=self._scripted_calls(messages))
        return AIMessage(content=self.response_text)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(
            generations=[ChatGeneration(message=self._final_message(messages))]
        )

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(
            generations=[ChatGeneration(message=self._final_message(messages))]
        )

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        for chunk in self._iter_chunks(messages):
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
        for chunk in self._iter_chunks(messages):
            if run_manager is not None:
                await run_manager.on_llm_new_token(chunk.text or "", chunk=chunk)
            yield chunk

    def _iter_chunks(
        self, messages: Sequence[BaseMessage]
    ) -> Iterator[ChatGenerationChunk]:
        if self._wants_tool_call(messages):
            # A tool-call turn carries no assistant text — the graph
            # dispatches the call(s) and loops back with the result(s).
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": call["name"],
                            "args": json.dumps(call["args"]),
                            "id": call["id"],
                            "index": index,
                            "type": "tool_call_chunk",
                        }
                        for index, call in enumerate(self._scripted_calls(messages))
                    ],
                )
            )
            return
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
    # Scripted tool-call turns, for tests that must drive the real agent
    # loop through repeated dispatch (per-run budget caps, tool-result
    # surfacing). Both are read only when ENV_FLAG is already on.
    ENV_TOOL_CALLS = "RUNTIME_FAKE_MODEL_TOOL_CALLS"
    ENV_TOOL_NAME = "RUNTIME_FAKE_MODEL_TOOL_NAME"
    ENV_TOOL_ARGS = "RUNTIME_FAKE_MODEL_TOOL_ARGS"
    # JSON list of ``{"name", "args"}`` emitted in ONE turn, for driving
    # parallel branches (concurrent interrupts). Scoped by ENV_PARALLEL_TRIGGER.
    ENV_PARALLEL_TOOL_CALLS = "RUNTIME_FAKE_MODEL_PARALLEL_TOOL_CALLS"
    ENV_PARALLEL_TRIGGER = "RUNTIME_FAKE_MODEL_PARALLEL_TRIGGER"

    @classmethod
    def is_enabled(cls) -> bool:
        """True only for an explicit truthy ``RUNTIME_FAKE_MODEL`` value.

        The shipped desktop neither sets nor allowlists this var, so a real
        deployment can never turn it on — it is a test/CI affordance only.
        """
        return os.environ.get(cls.ENV_FLAG, "").strip().lower() in _TRUTHY

    @classmethod
    def _tool_calls_before_final(cls) -> int:
        """Parse the scripted tool-call turn count; ``0`` when unset or invalid."""
        raw = os.environ.get(cls.ENV_TOOL_CALLS, "").strip()
        if not raw:
            return 0
        try:
            parsed = int(raw)
        except ValueError:
            return 0
        return max(parsed, 0)

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
        overrides: dict[str, Any] = {
            "tool_calls_before_final": cls._tool_calls_before_final()
        }
        tool_name = os.environ.get(cls.ENV_TOOL_NAME, "").strip()
        if tool_name:
            overrides["tool_call_name"] = tool_name
        tool_args = cls._tool_call_args()
        if tool_args is not None:
            overrides["tool_call_args"] = tool_args
        parallel = cls._parallel_tool_calls()
        if parallel:
            overrides["parallel_tool_calls"] = parallel
            overrides["parallel_trigger"] = os.environ.get(
                cls.ENV_PARALLEL_TRIGGER, ""
            ).strip()
        return DeterministicFakeChatModel(**overrides)

    @classmethod
    def _parallel_tool_calls(cls) -> list[dict[str, Any]]:
        """Parse the scripted fan-out tool calls as JSON; ``[]`` when unset or invalid."""
        raw = os.environ.get(cls.ENV_PARALLEL_TOOL_CALLS, "").strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except ValueError:
            return []
        if not isinstance(parsed, list):
            return []
        return [call for call in parsed if isinstance(call, dict)]

    @classmethod
    def _tool_call_args(cls) -> dict[str, Any] | None:
        """Parse the scripted tool arguments as JSON; ``None`` when unset or invalid."""
        raw = os.environ.get(cls.ENV_TOOL_ARGS, "").strip()
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None


__all__ = ["DeterministicFakeChatModel", "FakeModelProvider"]
