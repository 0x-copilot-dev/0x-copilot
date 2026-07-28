from __future__ import annotations

import httpx
import pytest

from agent_runtime.execution.model_invocation.contracts import (
    ModelFailureClass,
    ModelFailureSignal,
)
from agent_runtime.execution.model_invocation.lifecycle import (
    ProviderAttemptLifecycle,
    ProviderLifecycleEvent,
    ProviderLifecycleReducer,
)
from agent_runtime.execution.model_invocation.policy import ProviderFailureClassifier
from agent_runtime.execution.providers.model_failure_adapters import (
    ProviderExceptionClass,
    ProviderExceptionTypes,
    ProviderFailureAdapterRegistry,
    TypedProviderFailureAdapter,
    anthropic_failure_adapter,
    google_genai_failure_adapter,
    openai_failure_adapter,
)


class _TypedStatusError(Exception):
    def __init__(self, status_code: object, message: str = "ignored") -> None:
        super().__init__(message)
        self.status_code = status_code


class _UnknownError(Exception):
    pass


def _pre_dispatch() -> ProviderAttemptLifecycle:
    return ProviderAttemptLifecycle()


def test_only_reviewed_exception_types_and_numeric_status_are_used() -> None:
    adapter = TypedProviderFailureAdapter(
        ProviderExceptionTypes(
            status_error=(ProviderExceptionClass.from_type(_TypedStatusError),)
        )
    )
    misleading = _UnknownError("HTTP 429 overloaded authentication failed")
    unknown = adapter.observe(misleading, _pre_dispatch())
    assert unknown.signal is ModelFailureSignal.UNKNOWN
    assert (
        ProviderFailureClassifier().classify(unknown)
        is ModelFailureClass.AMBIGUOUS_PROVIDER_STATE
    )
    assert (
        adapter.observe(_TypedStatusError("429", "rate limit"), _pre_dispatch()).signal
        is ModelFailureSignal.UNKNOWN
    )
    assert (
        adapter.observe(_TypedStatusError(429, "auth invalid"), _pre_dispatch()).signal
        is ModelFailureSignal.RATE_LIMITED
    )


@pytest.mark.parametrize(
    ("factory", "provider_module", "error_name", "status", "expected"),
    [
        (
            openai_failure_adapter,
            "openai",
            "BadRequestError",
            400,
            ModelFailureSignal.REQUEST_INVALID,
        ),
        (
            anthropic_failure_adapter,
            "anthropic",
            "AuthenticationError",
            401,
            ModelFailureSignal.AUTH_INVALID,
        ),
    ],
)
def test_openai_compatible_sdk_typed_exception_mappings(
    factory,
    provider_module: str,
    error_name: str,
    status: int,
    expected: ModelFailureSignal,
) -> None:
    module = __import__(provider_module)
    response = httpx.Response(
        status, request=httpx.Request("POST", "https://provider.test")
    )
    error = getattr(module, error_name)(
        "misleading overloaded rate limit text", response=response, body=None
    )
    assert factory().observe(error, _pre_dispatch()).signal is expected


def test_google_genai_uses_typed_numeric_code() -> None:
    from google.genai.errors import ServerError

    error = ServerError(503, {"error": {"message": "authentication failed"}})
    assert (
        google_genai_failure_adapter().observe(error, _pre_dispatch()).signal
        is ModelFailureSignal.OVERLOADED
    )


def test_unknown_provider_is_conservative_and_preserves_lifecycle_progress() -> None:
    reducer = ProviderLifecycleReducer()
    lifecycle = ProviderAttemptLifecycle()
    for event in (
        ProviderLifecycleEvent.DISPATCH_STARTED,
        ProviderLifecycleEvent.DISPATCH_ACKNOWLEDGED,
        ProviderLifecycleEvent.STREAM_STARTED,
    ):
        lifecycle = reducer.reduce(lifecycle, event)
    observation = ProviderFailureAdapterRegistry().observe(
        "unreviewed-provider", RuntimeError("429"), lifecycle
    )
    assert observation.signal is ModelFailureSignal.UNKNOWN
    assert (
        ProviderFailureClassifier().classify(observation)
        is ModelFailureClass.AMBIGUOUS_PROVIDER_STATE
    )
