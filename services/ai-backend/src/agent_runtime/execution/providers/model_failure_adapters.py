"""SDK-boundary exception adapters for sanitized F10 failure observations.

Only reviewed SDK exception classes and numeric status fields are inspected.
Provider messages and ``str(exception)`` are deliberately never consulted.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from agent_runtime.execution.model_invocation.contracts import (
    ModelFailureSignal,
    ProviderFailureObservation,
)
from agent_runtime.execution.model_invocation.lifecycle import ProviderAttemptLifecycle


@dataclass(frozen=True, slots=True)
class ProviderExceptionClass:
    """Stable exact SDK class identity without importing provider packages."""

    module: str
    qualname: str

    @classmethod
    def from_type(cls, value: type[BaseException]) -> "ProviderExceptionClass":
        return cls(module=value.__module__, qualname=value.__qualname__)

    def matches(self, error: BaseException) -> bool:
        return any(
            candidate.__module__ == self.module
            and candidate.__qualname__ == self.qualname
            for candidate in type(error).__mro__
        )


@dataclass(frozen=True, slots=True)
class ProviderExceptionTypes:
    connection: tuple[ProviderExceptionClass, ...] = ()
    timeout: tuple[ProviderExceptionClass, ...] = ()
    rate_limited: tuple[ProviderExceptionClass, ...] = ()
    authentication: tuple[ProviderExceptionClass, ...] = ()
    permission: tuple[ProviderExceptionClass, ...] = ()
    bad_request: tuple[ProviderExceptionClass, ...] = ()
    status_error: tuple[ProviderExceptionClass, ...] = ()
    cancelled: tuple[ProviderExceptionClass, ...] = ()


class ProviderFailureAdapter(Protocol):
    def observe(
        self,
        error: BaseException,
        lifecycle: ProviderAttemptLifecycle,
    ) -> ProviderFailureObservation: ...


class TypedProviderFailureAdapter:
    """Translate a closed set of exception classes/statuses into domain signals."""

    def __init__(self, exception_types: ProviderExceptionTypes) -> None:
        self._types = exception_types

    def observe(
        self,
        error: BaseException,
        lifecycle: ProviderAttemptLifecycle,
    ) -> ProviderFailureObservation:
        signal = self._signal(error)
        return ProviderFailureObservation(
            signal=signal,
            dispatch_state=lifecycle.dispatch_state,
            stream_state=lifecycle.stream_state,
        )

    def _signal(self, error: BaseException) -> ModelFailureSignal:
        # Specific subclasses precede their broader SDK base classes.
        if isinstance(error, asyncio.CancelledError) or self._matches(
            error, self._types.cancelled
        ):
            return ModelFailureSignal.CANCELLED
        if isinstance(error, TimeoutError) or self._matches(error, self._types.timeout):
            return ModelFailureSignal.DEADLINE_EXCEEDED
        if self._matches(error, self._types.rate_limited):
            return ModelFailureSignal.RATE_LIMITED
        if self._matches(error, self._types.authentication):
            return ModelFailureSignal.AUTH_INVALID
        if self._matches(error, self._types.permission):
            return ModelFailureSignal.POLICY_INCOMPATIBLE
        if self._matches(error, self._types.bad_request):
            return ModelFailureSignal.REQUEST_INVALID
        if self._matches(error, self._types.connection):
            return ModelFailureSignal.CONNECTIVITY
        if self._matches(error, self._types.status_error):
            return self._status_signal(getattr(error, "status_code", None))
        return ModelFailureSignal.UNKNOWN

    @staticmethod
    def _matches(
        error: BaseException, classes: tuple[ProviderExceptionClass, ...]
    ) -> bool:
        return any(candidate.matches(error) for candidate in classes)

    @staticmethod
    def _status_signal(raw_status: object) -> ModelFailureSignal:
        if not isinstance(raw_status, int) or isinstance(raw_status, bool):
            return ModelFailureSignal.UNKNOWN
        if raw_status == 400 or raw_status == 413 or raw_status == 422:
            return ModelFailureSignal.REQUEST_INVALID
        if raw_status == 401:
            return ModelFailureSignal.AUTH_INVALID
        if raw_status == 403:
            return ModelFailureSignal.POLICY_INCOMPATIBLE
        if raw_status in {408, 504}:
            return ModelFailureSignal.DEADLINE_EXCEEDED
        if raw_status == 429:
            return ModelFailureSignal.RATE_LIMITED
        if raw_status in {500, 502, 503, 529}:
            return ModelFailureSignal.OVERLOADED
        return ModelFailureSignal.UNKNOWN


def openai_failure_adapter() -> TypedProviderFailureAdapter:
    """Build against OpenAI's reviewed public exception class identities."""

    return TypedProviderFailureAdapter(
        ProviderExceptionTypes(
            connection=(ProviderExceptionClass("openai", "APIConnectionError"),),
            timeout=(ProviderExceptionClass("openai", "APITimeoutError"),),
            rate_limited=(ProviderExceptionClass("openai", "RateLimitError"),),
            authentication=(ProviderExceptionClass("openai", "AuthenticationError"),),
            permission=(ProviderExceptionClass("openai", "PermissionDeniedError"),),
            bad_request=(ProviderExceptionClass("openai", "BadRequestError"),),
            status_error=(ProviderExceptionClass("openai", "APIStatusError"),),
        )
    )


def anthropic_failure_adapter() -> TypedProviderFailureAdapter:
    """Build against Anthropic's reviewed public exception class identities."""

    return TypedProviderFailureAdapter(
        ProviderExceptionTypes(
            connection=(ProviderExceptionClass("anthropic", "APIConnectionError"),),
            timeout=(ProviderExceptionClass("anthropic", "APITimeoutError"),),
            rate_limited=(ProviderExceptionClass("anthropic", "RateLimitError"),),
            authentication=(
                ProviderExceptionClass("anthropic", "AuthenticationError"),
            ),
            permission=(ProviderExceptionClass("anthropic", "PermissionDeniedError"),),
            bad_request=(ProviderExceptionClass("anthropic", "BadRequestError"),),
            status_error=(ProviderExceptionClass("anthropic", "APIStatusError"),),
        )
    )


def google_genai_failure_adapter() -> TypedProviderFailureAdapter:
    """Build against google-genai's typed errors and numeric ``code`` field."""

    return _GoogleGenaiFailureAdapter(
        ProviderExceptionTypes(
            bad_request=(ProviderExceptionClass("google.genai.errors", "ClientError"),),
            status_error=(ProviderExceptionClass("google.genai.errors", "APIError"),),
        )
    )


class _GoogleGenaiFailureAdapter(TypedProviderFailureAdapter):
    """google-genai names its numeric HTTP field ``code``."""

    def _signal(self, error: BaseException) -> ModelFailureSignal:
        if self._matches(error, self._types.status_error):
            return self._status_signal(getattr(error, "code", None))
        return super()._signal(error)


class ProviderFailureAdapterRegistry:
    """Explicit provider lookup; unknown providers remain ambiguous."""

    def __init__(
        self, adapters: dict[str, ProviderFailureAdapter] | None = None
    ) -> None:
        self._adapters = dict(adapters or {})

    @classmethod
    def defaults(cls) -> "ProviderFailureAdapterRegistry":
        return cls(
            {
                "openai": openai_failure_adapter(),
                "anthropic": anthropic_failure_adapter(),
                "google": google_genai_failure_adapter(),
                "google_genai": google_genai_failure_adapter(),
            }
        )

    def observe(
        self,
        provider: str,
        error: BaseException,
        lifecycle: ProviderAttemptLifecycle,
    ) -> ProviderFailureObservation:
        adapter = self._adapters.get(provider.strip().lower())
        if adapter is None:
            return ProviderFailureObservation(
                signal=ModelFailureSignal.UNKNOWN,
                dispatch_state=lifecycle.dispatch_state,
                stream_state=lifecycle.stream_state,
            )
        return adapter.observe(error, lifecycle)


__all__ = (
    "ProviderExceptionClass",
    "ProviderExceptionTypes",
    "ProviderFailureAdapter",
    "ProviderFailureAdapterRegistry",
    "TypedProviderFailureAdapter",
    "anthropic_failure_adapter",
    "google_genai_failure_adapter",
    "openai_failure_adapter",
)
