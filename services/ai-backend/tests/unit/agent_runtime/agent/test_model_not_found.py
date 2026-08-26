"""A provider 404 for a model id must end the run permanently, and say so.

The defect this file pins, reproduced from a real benchmark arm before it was
fixed. The catalog's LiteLLM fallback offered ``claude-3-haiku-20240307``, which
Anthropic has retired. Selecting it produced a real ``anthropic.NotFoundError``
(``404 … 'type': 'not_found_error', 'message': 'model: claude-3-haiku-20240307'``).
``_TracedRuntimeCall.guard`` classified it with ``not isinstance(exc,
(TypeError, ValueError, AttributeError))`` — a guess about Python builtins, not
a verdict about the provider — so the run failed as ``external_service_error``
with ``retryable: true``. The stored ``run_failed`` event therefore carried
``presentation.title "Service unavailable"``, ``summary "We couldn't complete
this run. Please try again."`` and ``retryable: true``, and ``RunTerminalBeatCard``
rendered a "start a new run with this goal" button wired to re-send the same
goal to the same model the provider had already refused. Five runs died that way
in seven seconds.

Why this test drives the REAL runtime helpers rather than the classifier:

* ``ProviderFailureClassifier`` owns the model-call journal and the per-attempt
  retry admission. It does NOT decide the run envelope. A green classifier test
  is compatible with the user still seeing "Please try again", which is exactly
  the dead-branch shape this repo has paid for before. The assertions below run
  through ``ainvoke_runtime`` and ``astream_runtime``, which is where
  ``retryable`` is actually stamped.
* ``astream_runtime`` is the worker's default path. Translating only ``ainvoke``
  would leave the shipped path emitting the old envelope.

Scope limit, stated so it is not discovered later: this covers a model call in
the main graph. A 404 raised inside a subagent's ``task`` tool is caught by
``DefaultToolErrorPolicy.classify`` and surfaced to the LLM, so the model
paraphrases it and ``guard()`` never sees it. That path is unchanged here.
"""

from __future__ import annotations

from typing import Any, TypedDict

import anthropic
import httpx
import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from langgraph.graph import END, StateGraph

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
    RuntimeErrorCode,
)
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.factory import RuntimeHarness
from agent_runtime.execution.runtime import (
    MODEL_NOT_FOUND_MESSAGE,
    ainvoke_runtime,
    astream_runtime,
)


class _State(TypedDict, total=False):
    messages: list


def _retired_model_404() -> anthropic.NotFoundError:
    """The exact exception shape Anthropic raises for a retired model id.

    Constructed from the real SDK class over a real ``httpx`` 404 rather than a
    stub, because the whole classification contract is the exception's own class
    identity and numeric status. A stub with the right attributes would pass a
    test that the SDK could then break silently.
    """

    return anthropic.NotFoundError(
        "Error code: 404 - {'type': 'error', 'error': "
        "{'type': 'not_found_error', 'message': 'model: claude-3-haiku-20240307'}}",
        response=httpx.Response(
            404, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        ),
        body=None,
    )


class _RetiredModel(BaseChatModel):
    """A chat model whose provider refuses the id it was configured with."""

    @property
    def _llm_type(self) -> str:
        return "anthropic-chat"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise _retired_model_404()


class RetiredModelGraphMixin:
    """A real compiled graph whose single node makes one model call."""

    @staticmethod
    def build_graph():
        model = _RetiredModel()
        graph = StateGraph(_State)
        graph.add_node("call", lambda state: {"messages": [model.invoke("hello")]})
        graph.set_entry_point("call")
        graph.add_edge("call", END)
        return graph.compile()

    @classmethod
    def harness(
        cls,
        context: AgentRuntimeContext,
        dependencies: RuntimeDependencies,
    ) -> RuntimeHarness:
        return RuntimeHarness(
            agent=cls.build_graph(),
            context=context.model_copy(
                update={
                    "run_id": "run_model_not_found",
                    "request_id": "request_model_not_found",
                }
            ),
            dependencies=dependencies,
            tools=(),
            mcp_servers=(),
            subagents=(),
            memory_backend=None,
            skill_directories=(),
        )


class TestAProvider404EndsTheRunWithoutInvitingARetry(RetiredModelGraphMixin):
    @staticmethod
    def _assert_envelope(error: AgentRuntimeError) -> None:
        assert error.code is RuntimeErrorCode.MODEL_NOT_FOUND
        # The half that reaches the user. ``retryable`` gates the run card's
        # button; ``true`` here offered a remedy that provably cannot work.
        assert error.retryable is False
        # The provider's own exception is preserved as the cause, so the
        # traceback still names what actually happened.
        assert isinstance(error.__cause__, anthropic.NotFoundError)
        assert error.safe_message == MODEL_NOT_FOUND_MESSAGE
        # Copy assertions, not wording assertions: the message must not invite
        # the retry, and must not leak a library symbol.
        assert "try again" not in error.safe_message.lower()
        assert "NotFoundError" not in error.safe_message
        assert "404" not in error.safe_message

    async def test_invoke_raises_the_typed_permanent_error(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        harness = self.harness(runtime_context_admin, fake_dependencies)

        with pytest.raises(AgentRuntimeError) as caught:
            await ainvoke_runtime(harness, [])

        self._assert_envelope(caught.value)

    async def test_stream_raises_the_typed_permanent_error_too(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """``astream`` is the worker's default path — the one that shipped."""

        harness = self.harness(runtime_context_admin, fake_dependencies)

        with pytest.raises(AgentRuntimeError) as caught:
            async for _ in astream_runtime(harness, []):
                pass

        self._assert_envelope(caught.value)

    async def test_an_unrelated_provider_failure_keeps_its_retryable_envelope(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """The new branch must not swallow the cases the old one got right.

        A connection failure is genuinely worth another attempt, and
        ``classify_without_lifecycle`` returns ``None`` for it precisely because
        its class depends on progress nobody recorded here. It must fall through
        to the existing ``EXTERNAL_SERVICE_ERROR`` handling untouched.
        """

        class _Unreachable(_RetiredModel):
            def _generate(
                self,
                messages: list[BaseMessage],
                stop: list[str] | None = None,
                run_manager: CallbackManagerForLLMRun | None = None,
                **kwargs: Any,
            ) -> ChatResult:
                raise anthropic.APIConnectionError(
                    request=httpx.Request("POST", "https://api.anthropic.com")
                )

        model = _Unreachable()
        graph = StateGraph(_State)
        graph.add_node("call", lambda state: {"messages": [model.invoke("hello")]})
        graph.set_entry_point("call")
        graph.add_edge("call", END)
        harness = RuntimeHarness(
            agent=graph.compile(),
            context=runtime_context_admin.model_copy(
                update={"run_id": "run_unreachable", "request_id": "req_unreachable"}
            ),
            dependencies=fake_dependencies,
            tools=(),
            mcp_servers=(),
            subagents=(),
            memory_backend=None,
            skill_directories=(),
        )

        with pytest.raises(AgentRuntimeError) as caught:
            await ainvoke_runtime(harness, [])

        assert caught.value.code is RuntimeErrorCode.EXTERNAL_SERVICE_ERROR
        assert caught.value.retryable is True


class TestTheFailureIsLoggedAsPermanent(RetiredModelGraphMixin):
    async def test_the_runtime_failure_event_carries_the_new_code(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The log row is what a support engineer reads after the fact.

        The pre-fix arm's ``runtime.stream.failed`` row said
        ``error_code: external_service_error, retryable: true`` next to
        ``exception_type: NotFoundError`` — the contradiction that had to be
        read by hand to understand why an arm burned five runs.
        """

        harness = self.harness(runtime_context_admin, fake_dependencies)

        with caplog.at_level("ERROR"), pytest.raises(AgentRuntimeError):
            async for _ in astream_runtime(harness, []):
                pass

        failures = [
            record.runtime
            for record in caplog.records
            if getattr(record, "runtime", {}).get("event") == "runtime.stream.failed"
        ]
        assert failures, "the runtime logged no stream failure"
        payload = failures[-1]
        assert payload["error_code"] == RuntimeErrorCode.MODEL_NOT_FOUND
        assert payload["retryable"] is False
        # Asserted alongside the code: the pre-fix row carried this same
        # exception type next to ``external_service_error``/``retryable: true``.
        assert payload["metadata"]["exception_type"] == "NotFoundError"
        assert "try again" not in payload["safe_message"].lower()


class TestTheHelperOnlyTranslatesWhatItCanProve:
    """``_model_not_found_code`` is deliberately narrow; pin that."""

    def test_only_the_404_class_is_translated(self) -> None:
        from agent_runtime.execution.runtime import _model_not_found_code

        assert (
            _model_not_found_code(_retired_model_404())
            is RuntimeErrorCode.MODEL_NOT_FOUND
        )

    @pytest.mark.parametrize(
        "error",
        [
            anthropic.APIConnectionError(
                request=httpx.Request("POST", "https://api.anthropic.com")
            ),
            ValueError("Error code: 404 - not_found_error"),
            RuntimeError("model not found"),
        ],
    )
    def test_everything_else_falls_through(self, error: BaseException) -> None:
        from agent_runtime.execution.runtime import _model_not_found_code

        assert _model_not_found_code(error) is None
