"""The per-model-call retry policy this runtime owns, rather than inherits.

Before this module the only *model*-path retry we had was the run-claim retry in
``runtime_worker/loop.py`` — which re-runs the **entire turn**, paying again for
every tool call that already completed — plus whatever the provider SDK happened
to do underneath ``build_chat_model``. The SDK's schedule is invisible to us: it
is not configured here, not observable, and not tunable per deployment. This
module makes the pacing an explicit, bounded, testable policy.

Two rules shape everything below.

**We never classify from message text.** ``providers/model_failure_adapters.py``
states the invariant plainly — only reviewed SDK exception classes and numeric
status fields are inspected, never ``str(exception)``. So this module contributes
*no* second classifier: retryability is read off the
:class:`~agent_runtime.execution.model_invocation.contracts.ModelFailureClass`
that :class:`ProviderFailureClassifier` already produced from adapter-attested
facts. Reference implementations that regex the provider's prose (opencode's
``session/retry.ts`` has six such families) are deliberately *not* ported: a
message-shaped rule would retry a 400 whose body happens to contain "timeout".

**Headers are structured data, so they are fair game.** ``retry-after-ms`` and
``retry-after`` (both the numeric-seconds and the HTTP-date form) are read off
the exception's response when the SDK attached one, exactly as the tool path
already does in ``tool_error_sanitizer._httpx_status``. A provider that tells us
when to come back is more informative than any backoff curve we could invent, so
its number wins — bounded, because an unbounded honour turns one hint into a
stalled run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Final, Mapping

from agent_runtime.execution.model_invocation.contracts import ModelFailureClass
from agent_runtime.execution.model_invocation.lifecycle import ProviderAttemptLifecycle
from agent_runtime.hyperparameters.contracts import ModelRetryHyperparameters


#: Header names carrying a provider-stated wait, most precise first.
_RETRY_AFTER_MS: Final[str] = "retry-after-ms"
_RETRY_AFTER: Final[str] = "retry-after"


@dataclass(frozen=True, slots=True)
class ProviderRetryHint:
    """What the provider said about when to come back, if it said anything.

    ``headers_observed`` is tracked separately from ``delay_seconds`` because the
    two answer different questions. A response that carried headers but no
    ``retry-after`` still proves we reached a real HTTP responder, which is a
    different situation from a socket that died before any response existed.
    """

    delay_seconds: float | None = None
    headers_observed: bool = False


@dataclass(frozen=True, slots=True)
class ModelRetryDecision:
    """Whether to re-dispatch this model call, and how long to wait first."""

    should_retry: bool
    delay_seconds: float = 0.0
    #: ``True`` only when the wait came from a provider header rather than the
    #: backoff curve. Carried into the log line so a stalled run can be traced
    #: to the provider that asked for the stall.
    provider_directed: bool = False


def _headers(error: BaseException) -> Mapping[str, str] | None:
    """Return response headers from a reviewed SDK exception shape, else ``None``.

    Both ``openai`` and ``anthropic`` attach the ``httpx.Response`` as
    ``.response``; ``httpx.HTTPStatusError`` does the same. Some wrappers hoist
    the mapping onto the exception as ``.headers``. Nothing else is consulted,
    and every read is defensive: a provider SDK that changes shape must degrade
    into "no hint" rather than raise on the failure path.
    """

    for candidate in (getattr(error, "response", None), error):
        headers = getattr(candidate, "headers", None)
        if headers is None:
            continue
        try:
            # ``httpx.Headers`` is a case-insensitive Mapping; a plain dict is
            # not, so normalise rather than trusting the caller's casing.
            return {str(key).lower(): str(value) for key, value in headers.items()}
        except Exception:  # noqa: BLE001 — a malformed header bag is "no hint"
            continue
    return None


def _positive_float(value: str) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return parsed if parsed > 0 else 0.0


def provider_retry_hint(
    error: BaseException,
    *,
    now: datetime,
) -> ProviderRetryHint:
    """Extract a bounded, provider-stated wait from ``error``'s response headers.

    ``retry-after-ms`` is preferred over ``retry-after`` because it is the more
    precise of the two when a provider sends both. ``retry-after`` accepts the
    numeric-seconds form *and* the HTTP-date form from RFC 9110; the date form is
    resolved against ``now`` (the binding's clock, never ``datetime.now``) so the
    computation stays deterministic under a fake clock.
    """

    headers = _headers(error)
    if headers is None:
        return ProviderRetryHint()

    raw_ms = headers.get(_RETRY_AFTER_MS)
    if raw_ms is not None:
        milliseconds = _positive_float(raw_ms)
        if milliseconds is not None:
            return ProviderRetryHint(
                delay_seconds=milliseconds / 1000, headers_observed=True
            )

    raw = headers.get(_RETRY_AFTER)
    if raw is not None:
        seconds = _positive_float(raw)
        if seconds is not None:
            return ProviderRetryHint(delay_seconds=seconds, headers_observed=True)
        deadline = _http_date(raw)
        if deadline is not None:
            return ProviderRetryHint(
                delay_seconds=max(0.0, (deadline - now).total_seconds()),
                headers_observed=True,
            )

    return ProviderRetryHint(headers_observed=True)


def _http_date(value: str) -> datetime | None:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    # RFC 9110 dates are GMT; a naive result is therefore UTC, and comparing it
    # against an aware ``now`` would raise on the failure path.
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


class ModelCallRetryPolicy:
    """Decide whether one model call may be re-dispatched, and how long to wait.

    Scope is deliberately *one model call*. It recovers a provider hiccup without
    discarding the turn's completed tool calls — the thing the run-claim retry at
    ``runtime_worker/loop.py`` cannot do, because by the time a claim is retried
    the turn restarts from its first message.

    The retryable set is not this class's invention: it is the same three classes
    ``ModelInvocationMiddleware._can_retry`` admits on the F10 path, and the same
    three ``ModelAttemptAdmissionPolicy._SAFE_RETRY_CLASSES`` admits. Keeping one
    set is the point — a second, subtly different notion of "transient" is how a
    non-idempotent call gets replayed.
    """

    #: Failure classes that may be re-dispatched. Everything else — an invalid
    #: request, a bad key, an exceeded context, a cancellation, a stream that
    #: already emitted content — is terminal for this call.
    RETRYABLE_CLASSES: Final[frozenset[ModelFailureClass]] = frozenset(
        {
            ModelFailureClass.PRE_DISPATCH_TRANSIENT,
            ModelFailureClass.PROVIDER_OVERLOADED,
            ModelFailureClass.STREAM_INTERRUPTED_BEFORE_CONTENT,
        }
    )

    def __init__(self, tunables: ModelRetryHyperparameters | None = None) -> None:
        self._tunables = tunables or ModelRetryHyperparameters()

    @property
    def tunables(self) -> ModelRetryHyperparameters:
        return self._tunables

    @property
    def max_attempts(self) -> int:
        return self._tunables.max_attempts

    def decide(
        self,
        *,
        failure: ModelFailureClass,
        lifecycle: ProviderAttemptLifecycle,
        attempt: int,
        error: BaseException,
        now: datetime,
        random_value: float,
    ) -> ModelRetryDecision:
        """Return the decision for an attempt that has just failed.

        ``attempt`` is 1-based and counts the attempt that *just failed*, so the
        ceiling reads as "3 attempts" rather than "2 retries". A call that has
        already put visible text on the user's screen is never re-dispatched
        whatever its failure class says: the second response would duplicate the
        first half of the answer.
        """

        if attempt >= self._tunables.max_attempts:
            return ModelRetryDecision(should_retry=False)
        if failure not in self.RETRYABLE_CLASSES:
            return ModelRetryDecision(should_retry=False)
        if lifecycle.visible_output_observed:
            return ModelRetryDecision(should_retry=False)
        hint = provider_retry_hint(error, now=now)
        return ModelRetryDecision(
            should_retry=True,
            delay_seconds=self.delay_seconds(
                attempt=attempt, hint=hint, random_value=random_value
            ),
            provider_directed=hint.delay_seconds is not None,
        )

    def delay_seconds(
        self,
        *,
        attempt: int,
        hint: ProviderRetryHint,
        random_value: float,
    ) -> float:
        """Seconds to wait before attempt ``attempt + 1``.

        A provider-stated wait wins, clamped to ``provider_hint_max_seconds``:
        honouring a literal ``retry-after: 3600`` would hold the worker's claim
        far past ``execution.worker_lock_seconds`` (60s), at which point another
        worker steals a run this one is still inside. Clamping keeps the hint
        advisory rather than load-bearing.

        With no stated wait we fall back to exponential backoff with *upper*
        jitter — ``base + base * jitter * random`` — so concurrent callers spread
        across the window instead of re-hitting an overloaded provider in lockstep.
        """

        tunables = self._tunables
        if hint.delay_seconds is not None:
            return min(hint.delay_seconds, tunables.provider_hint_max_seconds)
        base = tunables.initial_backoff_seconds * (
            tunables.backoff_factor ** max(attempt - 1, 0)
        )
        jittered = base + base * tunables.jitter_factor * _bounded_unit(random_value)
        return min(jittered, tunables.max_backoff_seconds)


def _bounded_unit(value: float) -> float:
    """Clamp a jitter source into ``[0, 1]`` so a bad source cannot widen the cap."""

    if value != value:  # NaN
        return 0.0
    return min(max(value, 0.0), 1.0)


__all__ = (
    "ModelCallRetryPolicy",
    "ModelRetryDecision",
    "ProviderRetryHint",
    "provider_retry_hint",
)
