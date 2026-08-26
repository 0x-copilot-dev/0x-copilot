"""The per-model-call retry policy, and its wiring into the live model seam.

Every test here drives a **fake clock**. The policy's whole job is to wait, so a
test that actually waited would either be slow enough that nobody runs it or
short enough that it proves nothing about the real schedule. ``_Clock`` records
what was asked of it and returns immediately, which lets the assertions be about
the *number* rather than about elapsed wall time.

The seam under test is ``ModelInvocationMiddleware.awrap_model_call`` with no F10
binding installed. That is not a corner case: ``FeatureModeSet.f10`` ships
``OFF`` and ``RUNTIME_HARNESS_RELEASE_CONFIG_PATH`` is set nowhere in the repo,
so this is the path every shipped deployment takes on every model call.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, cast

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
import pytest

from agent_runtime.execution.model_invocation.contracts import (
    ModelDispatchState,
    ModelFailureClass,
)
from agent_runtime.execution.model_invocation.lifecycle import ProviderAttemptLifecycle
from agent_runtime.execution.model_invocation.retry_schedule import (
    ModelCallRetryPolicy,
    ProviderRetryHint,
    provider_retry_hint,
)
from agent_runtime.execution.model_invocation.runtime import ModelInvocationMiddleware
from agent_runtime.hyperparameters.contracts import ModelRetryHyperparameters


_NOW = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)


class _Clock:
    """Fake sleep: records the requested waits, returns immediately."""

    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


def _headers_error(
    status: int,
    headers: dict[str, str] | None,
    *,
    qualname: str = "RateLimitError",
) -> Exception:
    """An exception shaped like the reviewed OpenAI SDK classes.

    Built by identity (``__module__`` / ``__qualname__``) rather than by
    importing the SDK, exactly as ``TypedProviderFailureAdapter`` matches it.
    That is the point of the adapter design: the runtime never needs the vendor
    package present to classify a vendor failure.
    """

    error_type = type(qualname, (Exception,), {})
    error_type.__module__ = "openai"
    error_type.__qualname__ = qualname
    error = error_type("provider prose that must never be parsed")
    error.status_code = status
    if headers is not None:
        error.response = SimpleNamespace(headers=headers)
    return error


def _request() -> ModelRequest[Any]:
    return ModelRequest(
        model=FakeListChatModel(responses=["done"]),
        messages=[HumanMessage(content="user body")],
        system_message=SystemMessage(content="harness"),
        tools=[],
        state={"runtime_control_model_turn": 1},
        runtime=cast(Any, SimpleNamespace(config={"metadata": {}})),
        model_settings={},
    )


def _middleware(clock: _Clock, *, jitter: float = 1.0) -> ModelInvocationMiddleware:
    return ModelInvocationMiddleware(
        occupancy_recorder=None,
        retry_policy=ModelCallRetryPolicy(),
        sleep=clock,
        random_source=lambda: jitter,
    )


class TestTheLiveSeamRetriesWithoutRerunningTheTurn:
    """Wiring tests: the policy must reach the path production actually takes."""

    async def test_rate_limit_with_retry_after_waits_that_long_and_succeeds(
        self,
    ) -> None:
        # The headline case from the problem statement: a 429 arriving deep in a
        # turn is absorbed by one bounded wait, and the turn is NOT re-run.
        clock = _Clock()
        calls: list[ModelRequest[Any]] = []

        async def handler(inner: ModelRequest[Any]) -> ModelResponse[Any]:
            calls.append(inner)
            if len(calls) == 1:
                raise _headers_error(429, {"retry-after": "2"})
            return ModelResponse(result=[AIMessage(content="done")])

        response = await _middleware(clock).awrap_model_call(_request(), handler)

        assert response.result[0].content == "done"
        assert len(calls) == 2
        # ~2s, from the header — not from the backoff curve, whose first wait
        # would be 2.0 * 1.25 = 2.5 with this jitter source.
        assert clock.waits == [2.0]

    async def test_a_rate_limit_without_headers_backs_off_inside_documented_bounds(
        self,
    ) -> None:
        clock = _Clock()
        attempts = 0

        async def handler(_inner: ModelRequest[Any]) -> ModelResponse[Any]:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise _headers_error(429, None)
            return ModelResponse(result=[AIMessage(content="done")])

        await _middleware(clock, jitter=0.5).awrap_model_call(_request(), handler)

        tunables = ModelRetryHyperparameters()
        assert len(clock.waits) == 2
        for index, wait in enumerate(clock.waits):
            base = tunables.initial_backoff_seconds * (tunables.backoff_factor**index)
            # Upper jitter: the wait lands in [base, base * (1 + jitter_factor)]
            # and never above the ceiling.
            assert base <= wait <= base * (1 + tunables.jitter_factor)
            assert wait <= tunables.max_backoff_seconds
        # Growth is the property, not the exact number.
        assert clock.waits[1] > clock.waits[0]

    async def test_a_non_retryable_400_is_raised_on_the_first_attempt(self) -> None:
        clock = _Clock()
        attempts = 0

        async def handler(_inner: ModelRequest[Any]) -> ModelResponse[Any]:
            nonlocal attempts
            attempts += 1
            raise _headers_error(400, None, qualname="BadRequestError")

        with pytest.raises(Exception) as caught:
            await _middleware(clock).awrap_model_call(_request(), handler)

        assert attempts == 1
        assert clock.waits == []
        # The provider's own exception, unwrapped: the runtime's existing typed
        # error taxonomy decides what the user sees, not this policy.
        assert type(caught.value).__qualname__ == "BadRequestError"

    async def test_retries_are_capped_and_the_typed_error_surfaces(self) -> None:
        clock = _Clock()
        attempts = 0

        async def handler(_inner: ModelRequest[Any]) -> ModelResponse[Any]:
            nonlocal attempts
            attempts += 1
            raise _headers_error(503, None, qualname="APIStatusError")

        with pytest.raises(Exception) as caught:
            await _middleware(clock).awrap_model_call(_request(), handler)

        assert attempts == ModelRetryHyperparameters().max_attempts
        assert len(clock.waits) == attempts - 1
        assert type(caught.value).__qualname__ == "APIStatusError"

    async def test_a_call_that_already_streamed_visible_text_is_never_retried(
        self,
    ) -> None:
        # Re-dispatching here would replay the first half of the answer into the
        # user's transcript. The failure class alone says "retryable"; the
        # lifecycle is what makes it terminal.
        clock = _Clock()
        attempts = 0

        async def handler(inner: ModelRequest[Any]) -> ModelResponse[Any]:
            nonlocal attempts
            attempts += 1
            inner.model.callbacks[-1].on_llm_new_token("half an answer")
            raise _headers_error(503, None, qualname="APIStatusError")

        with pytest.raises(Exception):
            await _middleware(clock).awrap_model_call(_request(), handler)

        assert attempts == 1
        assert clock.waits == []

    async def test_a_cancelled_run_is_never_retried(self) -> None:
        # Cancellation classifies as CANCELLED, which is outside the retryable
        # set, so a user pressing stop is honoured immediately rather than
        # being answered with two more provider calls.
        clock = _Clock()
        attempts = 0

        async def handler(_inner: ModelRequest[Any]) -> ModelResponse[Any]:
            nonlocal attempts
            attempts += 1
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await _middleware(clock).awrap_model_call(_request(), handler)

        assert attempts == 1
        assert clock.waits == []

    async def test_each_attempt_gets_its_own_lifecycle_observer(self) -> None:
        # One observer per attempt, never a shared one: a reused observer would
        # carry attempt 1's terminal state into attempt 2, where
        # ``observe_error`` returns early and every later failure classifies as
        # whatever the first one was.
        clock = _Clock()
        observers: list[object] = []

        async def handler(inner: ModelRequest[Any]) -> ModelResponse[Any]:
            observers.append(inner.model.callbacks[-1])
            if len(observers) == 1:
                raise _headers_error(429, None)
            return ModelResponse(result=[AIMessage(content="done")])

        await _middleware(clock).awrap_model_call(_request(), handler)

        assert len(observers) == 2
        assert observers[0] is not observers[1]


class TestTheJournaledPathIsPacedToo:
    """The F10 branch had the same instant-re-dispatch bug, one loop away."""

    async def test_the_f10_retry_loop_waits_between_admitted_attempts(self) -> None:
        # Imported here rather than at module scope: this module is about the
        # default path, and pulling the F10 harness in eagerly would make an
        # unrelated fixture failure look like a retry-policy failure.
        from tests.unit.agent_runtime.execution.model_invocation.test_model_invocation_runtime import (  # noqa: E501
            _AuthorityAdapter,
            _binding,
            _invoke,
            _Journal,
            _request as _f10_request,
        )
        from agent_runtime.execution.model_invocation.contracts import (
            ModelInvocationBudget,
        )

        clock = _Clock()
        journal = _Journal()
        authority = _AuthorityAdapter(
            budget=ModelInvocationBudget(max_attempts=2, max_same_deployment_attempts=2)
        )
        calls = 0

        async def handler(_inner: ModelRequest[Any]) -> ModelResponse[Any]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise _headers_error(429, {"retry-after": "3"})
            return ModelResponse(result=[AIMessage(content="done")])

        result = await _invoke(
            _binding(journal=journal, authority=authority, retry=True),
            _f10_request(),
            handler,
            middleware=ModelInvocationMiddleware(
                retry_policy=ModelCallRetryPolicy(),
                sleep=clock,
                random_source=lambda: 0.0,
            ),
        )

        assert result.result[0].content == "done"
        assert calls == 2
        # The provider's own number, honoured on the journaled path as well.
        assert clock.waits == [3.0]


class TestTheScheduleItself:
    """Pure policy: no middleware, no request, no clock beyond the value passed."""

    def test_retry_after_ms_is_preferred_over_retry_after(self) -> None:
        hint = provider_retry_hint(
            _headers_error(429, {"retry-after-ms": "1500", "retry-after": "9"}),
            now=_NOW,
        )

        assert hint == ProviderRetryHint(delay_seconds=1.5, headers_observed=True)

    def test_the_http_date_form_of_retry_after_resolves_against_the_clock(
        self,
    ) -> None:
        # RFC 9110 permits a date rather than a delta, and a provider that sends
        # one is otherwise read as "no hint" and silently given a backoff curve.
        later = _NOW + timedelta(seconds=45)
        hint = provider_retry_hint(
            _headers_error(
                429,
                {"retry-after": later.strftime("%a, %d %b %Y %H:%M:%S GMT")},
            ),
            now=_NOW,
        )

        assert hint.headers_observed
        assert hint.delay_seconds == pytest.approx(45.0, abs=1.0)

    def test_a_past_http_date_never_produces_a_negative_wait(self) -> None:
        earlier = _NOW - timedelta(seconds=30)
        hint = provider_retry_hint(
            _headers_error(
                429,
                {"retry-after": earlier.strftime("%a, %d %b %Y %H:%M:%S GMT")},
            ),
            now=_NOW,
        )

        assert hint.delay_seconds == 0.0

    def test_a_malformed_retry_after_degrades_to_the_backoff_curve(self) -> None:
        hint = provider_retry_hint(
            _headers_error(429, {"retry-after": "soon-ish"}), now=_NOW
        )

        assert hint == ProviderRetryHint(delay_seconds=None, headers_observed=True)

    def test_an_absent_response_reports_no_headers(self) -> None:
        assert provider_retry_hint(_headers_error(429, None), now=_NOW) == (
            ProviderRetryHint(delay_seconds=None, headers_observed=False)
        )

    def test_an_outsized_provider_hint_is_clamped_below_the_worker_lock(self) -> None:
        # ``execution.worker_lock_seconds`` is 60. Honouring a literal
        # ``retry-after: 3600`` would park the call long enough for a second
        # worker to claim a run this one is still inside.
        policy = ModelCallRetryPolicy()

        wait = policy.delay_seconds(
            attempt=1,
            hint=ProviderRetryHint(delay_seconds=3600.0, headers_observed=True),
            random_value=0.0,
        )

        assert wait == ModelRetryHyperparameters().provider_hint_max_seconds
        assert wait < 60

    def test_backoff_is_capped_however_many_attempts_are_configured(self) -> None:
        policy = ModelCallRetryPolicy()

        wait = policy.delay_seconds(
            attempt=25, hint=ProviderRetryHint(), random_value=1.0
        )

        assert wait == ModelRetryHyperparameters().max_backoff_seconds

    def test_a_hostile_jitter_source_cannot_widen_the_window(self) -> None:
        # The jitter source is injected, so a broken one must be clamped rather
        # than trusted to stay inside [0, 1].
        policy = ModelCallRetryPolicy(
            ModelRetryHyperparameters(max_backoff_seconds=600.0)
        )

        widened = policy.delay_seconds(
            attempt=1, hint=ProviderRetryHint(), random_value=1_000.0
        )
        narrowed = policy.delay_seconds(
            attempt=1, hint=ProviderRetryHint(), random_value=-1_000.0
        )
        tunables = ModelRetryHyperparameters()

        assert widened == tunables.initial_backoff_seconds * (
            1 + tunables.jitter_factor
        )
        assert narrowed == tunables.initial_backoff_seconds

    @pytest.mark.parametrize(
        "failure",
        sorted(ModelCallRetryPolicy.RETRYABLE_CLASSES),
    )
    def test_the_retryable_set_is_the_one_the_f10_path_already_admits(
        self, failure: ModelFailureClass
    ) -> None:
        # Pinned against ``ModelAttemptAdmissionPolicy._SAFE_RETRY_CLASSES``:
        # two different notions of "transient" is how a non-idempotent call
        # eventually gets replayed.
        from agent_runtime.execution.model_invocation.policy import (
            ModelAttemptAdmissionPolicy,
        )

        assert failure in ModelAttemptAdmissionPolicy._SAFE_RETRY_CLASSES  # noqa: SLF001
        assert ModelCallRetryPolicy.RETRYABLE_CLASSES == (
            ModelAttemptAdmissionPolicy._SAFE_RETRY_CLASSES  # noqa: SLF001
        )

    def test_an_exhausted_budget_stops_before_consulting_the_failure_class(
        self,
    ) -> None:
        policy = ModelCallRetryPolicy(ModelRetryHyperparameters(max_attempts=1))

        decision = policy.decide(
            failure=ModelFailureClass.PROVIDER_OVERLOADED,
            lifecycle=ProviderAttemptLifecycle(),
            attempt=1,
            error=_headers_error(429, None),
            now=_NOW,
            random_value=0.0,
        )

        assert decision.should_retry is False

    def test_a_visible_stream_is_terminal_even_for_a_retryable_class(self) -> None:
        policy = ModelCallRetryPolicy()

        decision = policy.decide(
            failure=ModelFailureClass.PROVIDER_OVERLOADED,
            lifecycle=ProviderAttemptLifecycle(
                dispatch_started=True,
                dispatch_state=ModelDispatchState.ACCEPTED,
                stream_started=True,
                visible_text_observed=True,
            ),
            attempt=1,
            error=_headers_error(429, None),
            now=_NOW,
            random_value=0.0,
        )

        assert decision.should_retry is False

    def test_a_model_the_provider_will_not_serve_is_never_re_dispatched(self) -> None:
        # ``RETRYABLE_CLASSES`` is an allow-set, so omission is the whole
        # mechanism — asserted rather than assumed, because a later edit that
        # "completes" the set would spend the full backoff ladder collecting
        # identical 404s.
        policy = ModelCallRetryPolicy()

        decision = policy.decide(
            failure=ModelFailureClass.MODEL_NOT_FOUND,
            lifecycle=ProviderAttemptLifecycle(),
            attempt=1,
            error=_headers_error(404, None, qualname="NotFoundError"),
            now=_NOW,
            random_value=0.0,
        )

        assert decision.should_retry is False
        assert ModelFailureClass.MODEL_NOT_FOUND not in (
            ModelCallRetryPolicy.RETRYABLE_CLASSES
        )
