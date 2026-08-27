"""SDK-boundary exception adapters for sanitized F10 failure observations.

Only reviewed SDK exception classes and numeric status fields are inspected.
Provider messages and ``str(exception)`` are deliberately never consulted.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from agent_runtime.execution.model_invocation.contracts import (
    ModelFailureClass,
    ModelFailureSignal,
    ProviderFailureObservation,
)
from agent_runtime.execution.model_invocation.lifecycle import ProviderAttemptLifecycle
from agent_runtime.execution.model_invocation.policy import ProviderFailureClassifier


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
        if raw_status == 404:
            # The provider's own numeric status, not its prose: 404 is the one
            # status that proves the *addressed resource* does not exist, which
            # for a model call means the model id (or the endpoint path it was
            # sent to) is not something this deployment can reach. Without this
            # row a vendor 404 fell through to UNKNOWN and then to
            # AMBIGUOUS_PROVIDER_STATE — "we cannot tell" — which is the one
            # verdict a 404 never warrants.
            return ModelFailureSignal.NOT_FOUND
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

    def observe_unattributed(
        self,
        error: BaseException,
        lifecycle: ProviderAttemptLifecycle,
    ) -> ProviderFailureObservation:
        """Classify when the caller cannot name the provider.

        :meth:`observe` needs our canonical slug (``"openai"``), which the F10
        path takes from the verified route. Callers outside that path have only
        the LangChain model object, whose ``_llm_type`` is the *library's* name
        (``"openai-chat"``, ``"chat-google-generative-ai"``) and matches no key
        here. Keying off it would silently classify every provider failure as
        ``UNKNOWN`` — retryable failures included.

        So the exception is asked instead of the caller. That is not a guess:
        every adapter matches on exact SDK class identity (module + qualname
        through the MRO), the modules are disjoint, and an exception that
        belongs to no reviewed SDK still lands on ``UNKNOWN``. At most one
        adapter can recognise any given error, which is why scanning is
        deterministic rather than order-dependent.
        """

        for adapter in self._adapters.values():
            observation = adapter.observe(error, lifecycle)
            if observation.signal is not ModelFailureSignal.UNKNOWN:
                return observation
        return ProviderFailureObservation(
            signal=ModelFailureSignal.UNKNOWN,
            dispatch_state=lifecycle.dispatch_state,
            stream_state=lifecycle.stream_state,
        )


def classify_without_lifecycle(error: BaseException) -> ModelFailureClass | None:
    """Classify a provider exception where no attempt lifecycle was recorded.

    Exists for the *run* boundary. ``_TracedRuntimeCall.guard`` catches whatever
    escaped the graph roughly two hundred frames above the model call, so the
    ``ProviderAttemptLifecycle`` that :class:`ProviderFailureClassifier` normally
    reads is long out of scope. What survives is the exception object, and every
    adapter here reads only its SDK class identity and its numeric status — so
    the subset of the taxonomy that does not depend on how far the attempt got
    is still decidable from it alone.

    The blind spot is the contract, not a caveat: ``None`` means *this class is
    not observable without a lifecycle*, and never "no failure". ``CONNECTIVITY``
    and ``STREAM_INTERRUPTED`` genuinely resolve to different classes depending
    on ``dispatch_state`` / ``stream_state`` (pre-dispatch transient vs ambiguous
    provider state; interrupted before vs after visible content), so guessing one
    here would manufacture a verdict from a fact nobody measured. Only the signals
    in :attr:`ProviderFailureClassifier._DIRECT_CLASSES` — the ones whose class is
    a pure function of the signal — are returned. Callers must treat ``None`` as
    "fall through to your existing handling", which is what the run boundary does.
    """

    observation = ProviderFailureAdapterRegistry.defaults().observe_unattributed(
        error, ProviderAttemptLifecycle()
    )
    return ProviderFailureClassifier().classify_lifecycle_independent(observation)


__all__ = (
    "ProviderExceptionClass",
    "ProviderExceptionTypes",
    "ProviderFailureAdapter",
    "ProviderFailureAdapterRegistry",
    "TypedProviderFailureAdapter",
    "anthropic_failure_adapter",
    "classify_without_lifecycle",
    "google_genai_failure_adapter",
    "openai_failure_adapter",
)
