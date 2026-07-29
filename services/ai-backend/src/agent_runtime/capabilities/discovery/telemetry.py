"""Body-free F3 decisions and metrics for the capability discovery bridge.

Step 8 work item 10 asks for two things: *decisions* for search, describe, and
invoke, and *token / model-turn / latency metrics* for the discovery path. This
module supplies both, and deliberately supplies them from **one** value.

**One observation, two projections.**  Every bridge call produces exactly one
:class:`CapabilityDiscoveryObservation`.  The canonical run-journal decision and
the OTel metrics are both projections of that single object, so the two can
never disagree and there is no parallel counter store to reconcile: the counts a
dashboard shows and the decision the journal holds were derived from the same
measurement of the same call.

**Body-free by construction, not by discipline.**  The observation carries no
free-text field at all.  Every field is a member of a closed enum, a
non-negative count, or one lower-case SHA-256 digest — so a query, a capability
description, a tool argument, a result, or a connector URL is not something a
call site could pass in even by mistake.  The error *code* that becomes a metric
label is re-parsed through :class:`CapabilityDiscoveryErrorCode` rather than
copied, which is what keeps label cardinality bounded by a reviewed vocabulary
instead of by whatever a downstream component happened to return.

**Observation never changes behaviour.**  :class:`ObservedCapabilityBridgeTool`
returns the wrapped adapter's own answer object unchanged, forwards its name and
description verbatim, and re-raises anything the adapter raises.  Every
observer failure — a broken meter, an unreachable journal, an observation that
will not even validate — is swallowed at the seam.  A run that cannot be
measured still runs.

**The journal is reused, not extended.**  A discovery decision is an ordinary
``quality.decision.v1`` row: ``feature`` is ``f3``, ``phase`` is the closed
:class:`CapabilityDiscoveryPhase` that plays the PRD's ``decision_kind``, and
``outcome_code`` is the closed :class:`CapabilityDiscoveryOutcome`.  No new
event family, no new table, no new queue.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
import logging
from time import perf_counter
from typing import Any, ClassVar, Literal, Protocol, Self, runtime_checkable

from pydantic import Field, NonNegativeInt, PositiveInt

from agent_runtime.capabilities.discovery.contracts import (
    CapabilityBridgeToolName,
    CapabilityDiscoveryErrorCode,
    CapabilityExpansionResult,
    CapabilityExpansionState,
)
from agent_runtime.capabilities.discovery.expansion import (
    BoundedCapabilityExpander,
    TwoTierCapabilitySearch,
)
from agent_runtime.capabilities.discovery.ranker import DeterministicLexicalRanker
from agent_runtime.context.memory.token_budget import TokenBudgetEvaluator
from agent_runtime.control_plane.contracts import RunControlDecision
from agent_runtime.control_plane.feature_modes import AgentQualityFeature
from agent_runtime.control_plane.ports import (
    RunControlDecisionStorePort,
    RunControlDecisionWrite,
)
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.canonical_json import (
    canonical_json_bytes,
    canonical_json_sha256,
    sha256_hex,
)

_LOGGER = logging.getLogger(__name__)

_SHA256_HEX_PATTERN = r"^[0-9a-f]{64}$"

_METER_NAME = "agent_runtime.capability_discovery"
_DECISIONS_TOTAL = "capability_discovery_decisions_total"
_LATENCY_SECONDS = "capability_discovery_latency_seconds"
_RESULT_TOKENS = "capability_discovery_result_tokens"
_MODEL_TURNS_TOTAL = "capability_discovery_model_turns_total"
_CANDIDATES = "capability_discovery_candidates"
_EXPANSION_SERVERS_TOTAL = "capability_discovery_expansion_servers_total"
_EXPANSION_SECONDS = "capability_discovery_expansion_seconds"

_LATENCY_BUCKETS = (0.01, 0.05, 0.1, 0.5, 1, 5, 15, 60)
_TOKEN_BUCKETS = (16, 64, 256, 1024, 4096)
_COUNT_BUCKETS = (1, 2, 5, 10)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class CapabilityDiscoveryPhase(StrEnum):
    """The closed ``decision_kind`` an F3 discovery decision may carry.

    ``quality.decision.v1`` already reserves ``phase`` for exactly this, so the
    PRD's "closed ``decision_kind``" is spelled here rather than as a new event
    field.  One member per model-facing bridge tool: a discovery decision is
    always a decision about one bridge call.
    """

    SEARCH = "capability_search"
    DESCRIBE = "capability_describe"
    INVOKE = "capability_invoke"

    @classmethod
    def for_tool(cls, tool: CapabilityBridgeToolName) -> "CapabilityDiscoveryPhase":
        """Return the decision kind one bridge tool's calls are recorded under."""

        return _PHASE_FOR_TOOL[tool]


_PHASE_FOR_TOOL: dict[CapabilityBridgeToolName, CapabilityDiscoveryPhase] = {
    CapabilityBridgeToolName.SEARCH_CAPABILITIES: CapabilityDiscoveryPhase.SEARCH,
    CapabilityBridgeToolName.DESCRIBE_CAPABILITY: CapabilityDiscoveryPhase.DESCRIBE,
    CapabilityBridgeToolName.INVOKE_CAPABILITY: CapabilityDiscoveryPhase.INVOKE,
}

# The key each exactly-one-outcome envelope carries on success. Pinned per tool
# rather than probed by "any known key", so a contract rename surfaces as a
# failing test instead of silently degrading every success to ``unrecognized``.
_SUCCESS_KEY: dict[CapabilityBridgeToolName, str] = {
    CapabilityBridgeToolName.SEARCH_CAPABILITIES: "search",
    CapabilityBridgeToolName.DESCRIBE_CAPABILITY: "description",
    CapabilityBridgeToolName.INVOKE_CAPABILITY: "invocation",
}


class CapabilityDiscoveryOutcome(StrEnum):
    """The closed outcome vocabulary a decision and a metric label may use.

    Exactly the bridge's own closed failure vocabulary, plus ``ok`` for an
    answered call and two terminal cases that must never widen into free text:
    ``tool_raised`` for an adapter that raised instead of returning an envelope,
    and ``unrecognized`` for an answer shape this module does not understand.
    Without those two, an unexpected result would have no bounded label left and
    the obvious repair — echoing whatever came back — is precisely the
    unbounded-cardinality outage this vocabulary exists to prevent.
    """

    OK = "ok"
    INVALID_REQUEST = "invalid_request"
    CATALOG_INACTIVE = "catalog_inactive"
    CAPABILITY_NOT_FOUND = "capability_not_found"
    CAPABILITY_STALE = "capability_stale"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    EXECUTION_FAILED = "execution_failed"
    TOOL_RAISED = "tool_raised"
    UNRECOGNIZED = "unrecognized"

    @classmethod
    def for_answer(
        cls,
        *,
        tool: CapabilityBridgeToolName,
        answer: object,
    ) -> "CapabilityDiscoveryOutcome":
        """Classify one bridge answer into the closed outcome vocabulary.

        The error code is re-parsed through :class:`CapabilityDiscoveryErrorCode`
        instead of being trusted, so only a member of the reviewed bridge
        vocabulary can ever reach a metric label or a journal row.
        """

        if not isinstance(answer, Mapping):
            return cls.UNRECOGNIZED
        error = answer.get("error")
        if error is None:
            return cls.OK if _SUCCESS_KEY[tool] in answer else cls.UNRECOGNIZED
        if not isinstance(error, Mapping):
            return cls.UNRECOGNIZED
        try:
            code = CapabilityDiscoveryErrorCode(error.get("code"))
        except ValueError:
            return cls.UNRECOGNIZED
        return cls(code.value)


class CapabilityDiscoveryObservation(RuntimeContract):
    """One measured bridge call, carrying identities and counts and nothing else.

    There is no free-text field on this record, and that is the whole design.
    ``input_digest`` is a digest *of* the request — the query, the opaque ref,
    and the arguments are hashed at the seam and never held — while everything
    else is a closed vocabulary member or a count.  A body therefore cannot
    enter the journal or a metric label through this type at all, which is a
    stronger guarantee than reviewing each call site for leaks.
    """

    schema_version: Literal[1] = 1
    phase: CapabilityDiscoveryPhase
    tool: CapabilityBridgeToolName
    outcome: CapabilityDiscoveryOutcome
    input_digest: str = Field(pattern=_SHA256_HEX_PATTERN)
    latency_ms: NonNegativeInt
    # The PRD's budget accounting: one bridge call is one model-visible F4 call,
    # whatever the real inner operation goes on to consume under its own budget.
    model_turns: PositiveInt = 1
    result_tokens: NonNegativeInt = 0
    candidate_count: NonNegativeInt = 0
    scanned_count: NonNegativeInt = 0


class CapabilityExpansionObservation(RuntimeContract):
    """What one bounded tier-two expansion cost, in counts and milliseconds.

    Recorded separately from the search decision because it is measured at a
    different seam and a different time.  ``servers_by_state`` is keyed by the
    closed :class:`CapabilityExpansionState`, which is what keeps the derived
    metric's label set bounded by three reviewed values.
    """

    schema_version: Literal[1] = 1
    latency_ms: NonNegativeInt
    considered_count: NonNegativeInt = 0
    admitted_count: NonNegativeInt = 0
    capability_count: NonNegativeInt = 0
    deadline_exceeded: bool = False
    servers_by_state: dict[CapabilityExpansionState, NonNegativeInt] = Field(
        default_factory=dict,
    )

    @classmethod
    def from_result(
        cls,
        result: CapabilityExpansionResult,
        *,
        latency_ms: int,
    ) -> Self:
        """Project the bounded facts of one expansion, without its records."""

        by_state: dict[CapabilityExpansionState, int] = {}
        for outcome in result.outcomes:
            by_state[outcome.state] = by_state.get(outcome.state, 0) + 1
        return cls(
            latency_ms=max(latency_ms, 0),
            considered_count=result.considered_count,
            admitted_count=result.admitted_count,
            capability_count=len(result.capabilities),
            deadline_exceeded=result.deadline_exceeded,
            servers_by_state=by_state,
        )


@runtime_checkable
class CapabilityDiscoveryObserver(Protocol):
    """Receive one measured bridge call. Implementations must not raise."""

    async def observe(self, observation: CapabilityDiscoveryObservation) -> None: ...


@runtime_checkable
class CapabilityExpansionObserver(Protocol):
    """Receive one measured tier-two expansion. Implementations must not raise."""

    async def observe_expansion(
        self,
        observation: CapabilityExpansionObservation,
    ) -> None: ...


def digest_request(raw_input: object) -> str:
    """Return the lower-case SHA-256 identity of one bridge request.

    The request is the only place user content reaches this module, and it
    reaches it exactly once: the value is hashed here and the digest is all that
    survives the call.  A request that is not canonical-JSON-representable is
    still hashed, through its ``repr``, rather than left undigested — an
    unhashable request would otherwise be the one input with no bounded
    identity.
    """

    try:
        return canonical_json_sha256(raw_input)
    except Exception:
        return sha256_hex(repr(raw_input).encode("utf-8"))


def estimate_answer_tokens(answer: object) -> int:
    """Estimate the model-visible token cost of one bridge answer.

    Deterministic and tokenizer-free, using the same characters-per-token
    estimate the context budget already applies, so discovery cost is comparable
    with the rest of the runtime's accounting.  Only the *size* is kept.
    """

    try:
        rendered = canonical_json_bytes(answer).decode("utf-8")
    except Exception:
        return 0
    return TokenBudgetEvaluator.estimate_tokens(rendered)


@dataclass(frozen=True)
class ObservedCapabilityBridgeTool:
    """Measure one bridge adapter without being able to change what it answers.

    The adapter's own answer object is returned by identity, its name and
    description are forwarded verbatim, and anything it raises propagates
    untouched.  Observation is therefore transparent by construction rather than
    by a convention each observer has to keep: the only thing this wrapper can
    do to a run is take slightly longer.

    Every observer failure is swallowed here as well as inside the observer,
    because "a failure to emit must never fail the run" has to hold even for an
    observer that ignores its own contract.
    """

    inner: Any
    observer: CapabilityDiscoveryObserver
    tool: CapabilityBridgeToolName
    clock: Callable[[], float] = perf_counter

    @property
    def name(self) -> str:
        """Forward the wrapped adapter's model-facing name unchanged."""

        return str(self.inner.name)

    @property
    def description(self) -> str:
        """Forward the wrapped adapter's model-facing description unchanged."""

        return str(self.inner.description)

    async def ainvoke(self, raw_input: Any) -> dict[str, Any]:
        """Call the wrapped adapter, record what happened, and answer as it did."""

        started = self.clock()
        try:
            answer = await self.inner.ainvoke(raw_input)
        except BaseException:
            await self._record(
                raw_input=raw_input,
                answer=None,
                outcome=CapabilityDiscoveryOutcome.TOOL_RAISED,
                latency_ms=self._elapsed_ms(started),
            )
            raise
        await self._record(
            raw_input=raw_input,
            answer=answer,
            outcome=CapabilityDiscoveryOutcome.for_answer(
                tool=self.tool,
                answer=answer,
            ),
            latency_ms=self._elapsed_ms(started),
        )
        return answer

    async def __call__(self, raw_input: Any) -> dict[str, Any]:
        """Delegate to :meth:`ainvoke`."""

        return await self.ainvoke(raw_input)

    def _elapsed_ms(self, started: float) -> int:
        try:
            return max(int((self.clock() - started) * 1000), 0)
        except Exception:
            return 0

    async def _record(
        self,
        *,
        raw_input: object,
        answer: object,
        outcome: CapabilityDiscoveryOutcome,
        latency_ms: int,
    ) -> None:
        try:
            observation = CapabilityDiscoveryObservation(
                phase=CapabilityDiscoveryPhase.for_tool(self.tool),
                tool=self.tool,
                outcome=outcome,
                input_digest=digest_request(raw_input),
                latency_ms=latency_ms,
                result_tokens=(0 if answer is None else estimate_answer_tokens(answer)),
                candidate_count=_candidate_count(self.tool, answer),
                scanned_count=_scanned_count(self.tool, answer),
            )
            await self.observer.observe(observation)
        except Exception:
            _LOGGER.debug("capability_discovery.observe_failed", exc_info=True)


class ObservedTwoTierCapabilitySearch(TwoTierCapabilitySearch):
    """The real second tier, measured.

    A subclass rather than a wrapper so it *is* a
    :class:`TwoTierCapabilitySearch` everywhere one is expected, and so the
    ranking and expansion work is inherited rather than re-stated.  The override
    delegates to ``super().search`` and adds only a stopwatch and a projection,
    which keeps this the only place tier-two cost is measured and keeps
    ``expansion.py`` free of observation concerns entirely.
    """

    def __init__(
        self,
        *,
        expander: BoundedCapabilityExpander,
        observer: CapabilityExpansionObserver,
        ranker: DeterministicLexicalRanker | None = None,
        clock: Callable[[], float] = perf_counter,
    ) -> None:
        super().__init__(expander=expander, ranker=ranker)
        self._observer = observer
        self._clock = clock

    async def search(self, **kwargs: Any) -> Any:
        """Search exactly as the base class does, and record what it cost."""

        started = self._clock()
        result = await super().search(**kwargs)
        try:
            await self._observer.observe_expansion(
                CapabilityExpansionObservation.from_result(
                    result.expansion,
                    latency_ms=max(int((self._clock() - started) * 1000), 0),
                )
            )
        except Exception:
            _LOGGER.debug(
                "capability_discovery.observe_expansion_failed", exc_info=True
            )
        return result


@dataclass
class RunJournalDiscoveryDecisionRecorder:
    """Append F3 discovery decisions to the canonical run event journal.

    The run binding is supplied once at construction because none of it is
    knowable inside the discovery package: a catalog knows its own revision, not
    which control snapshot the worker bound or which verified subject the run
    belongs to.  Taking the store as the narrow
    :class:`RunControlDecisionStorePort` keeps this module ignorant of whether
    the journal is in-memory, file-native, or Postgres.

    ``decision_id`` is an ordinal within the run rather than a digest of the
    body, because two identical searches in one run are two decisions.  A
    digest-derived id would silently collapse them into one journal row and
    under-count the exact repetition the F4 controller most wants to see.
    """

    store: RunControlDecisionStorePort
    org_id: str
    run_id: str
    trace_id: str
    subject_fingerprint: str
    snapshot_id: str
    policy_revision: str
    clock: Callable[[], datetime] = _utc_now
    _ordinal: int = field(default=0, init=False, repr=False)

    async def observe(self, observation: CapabilityDiscoveryObservation) -> None:
        """Record one decision, or record nothing at all. Never raise."""

        try:
            self._ordinal += 1
            decision = RunControlDecision.create(
                decision_id=f"f3.{observation.phase.value}.{self._ordinal}",
                run_id=self.run_id,
                snapshot_id=self.snapshot_id,
                phase=observation.phase.value,
                feature=AgentQualityFeature.F3_CAPABILITY_DISCOVERY,
                policy_revision=self.policy_revision,
                input_digest=observation.input_digest,
                outcome_code=observation.outcome.value,
                created_at=self.clock(),
            )
            await self.store.append(
                RunControlDecisionWrite(
                    org_id=self.org_id,
                    trace_id=self.trace_id,
                    subject_fingerprint=self.subject_fingerprint,
                    decision=decision,
                )
            )
        except Exception:
            _LOGGER.debug("capability_discovery.decision_append_failed", exc_info=True)


class CapabilityDiscoveryMetrics:
    """Per-process OTel meters for the discovery path.

    Every label value on every signal comes from a closed enum defined in this
    module or in the discovery contracts, so the series count is bounded by
    review rather than by traffic.  Nothing here is labelled by capability ref,
    catalog id, connector, query, or run — the labels that would look most
    useful on a dashboard and would take the metrics pipeline down first.

    Instruments are created lazily and every publish is best-effort, following
    the same pattern as the approval and model-invocation meters, so a missing
    or misconfigured OTel install degrades to silence instead of to an error.
    """

    #: Every label key this class may ever attach, for the cardinality test.
    LABEL_KEYS: ClassVar[frozenset[str]] = frozenset({"tool", "outcome", "state"})

    def __init__(self) -> None:
        self._meter = self._build_meter()
        self._instruments: dict[str, Any] = {}

    @staticmethod
    def _build_meter() -> Any:
        try:
            from opentelemetry import metrics as otel_metrics  # noqa: PLC0415
        except ImportError:  # pragma: no cover - optional dep
            return None
        try:
            return otel_metrics.get_meter(_METER_NAME)
        except Exception:  # pragma: no cover - defensive
            return None

    def _counter(self, name: str) -> Any:
        if name in self._instruments:
            return self._instruments[name]
        instrument = None
        if self._meter is not None:
            try:
                instrument = self._meter.create_counter(name)
            except Exception:  # pragma: no cover - defensive
                instrument = None
        self._instruments[name] = instrument
        return instrument

    def _histogram(self, name: str, *, buckets: tuple[float, ...]) -> Any:
        if name in self._instruments:
            return self._instruments[name]
        instrument = None
        if self._meter is not None:
            try:
                instrument = self._meter.create_histogram(
                    name,
                    explicit_bucket_boundaries_advisory=list(buckets),
                )
            except TypeError:
                try:
                    instrument = self._meter.create_histogram(name)
                except Exception:  # pragma: no cover - defensive
                    instrument = None
            except Exception:  # pragma: no cover - defensive
                instrument = None
        self._instruments[name] = instrument
        return instrument

    def _add(self, name: str, amount: int, labels: Mapping[str, str]) -> None:
        instrument = self._counter(name)
        if instrument is None:
            return
        try:
            instrument.add(amount, dict(labels))
        except Exception:
            _LOGGER.debug("capability_discovery.metric_failed name=%s", name)

    def _record(
        self,
        name: str,
        value: float,
        labels: Mapping[str, str],
        *,
        buckets: tuple[float, ...],
    ) -> None:
        instrument = self._histogram(name, buckets=buckets)
        if instrument is None:
            return
        try:
            instrument.record(value, dict(labels))
        except Exception:
            _LOGGER.debug("capability_discovery.metric_failed name=%s", name)

    async def observe(self, observation: CapabilityDiscoveryObservation) -> None:
        """Publish the token, model-turn, and latency signals for one call."""

        tool = {"tool": observation.tool.value}
        self._add(
            _DECISIONS_TOTAL,
            1,
            {**tool, "outcome": observation.outcome.value},
        )
        self._add(_MODEL_TURNS_TOTAL, observation.model_turns, tool)
        self._record(
            _LATENCY_SECONDS,
            observation.latency_ms / 1000,
            tool,
            buckets=_LATENCY_BUCKETS,
        )
        self._record(
            _RESULT_TOKENS,
            observation.result_tokens,
            tool,
            buckets=_TOKEN_BUCKETS,
        )
        if observation.phase is CapabilityDiscoveryPhase.SEARCH:
            self._record(
                _CANDIDATES,
                observation.candidate_count,
                tool,
                buckets=_COUNT_BUCKETS,
            )

    async def observe_expansion(
        self,
        observation: CapabilityExpansionObservation,
    ) -> None:
        """Publish how many servers tier two opened, and how long it took."""

        for state, count in observation.servers_by_state.items():
            self._add(_EXPANSION_SERVERS_TOTAL, count, {"state": state.value})
        self._record(
            _EXPANSION_SECONDS,
            observation.latency_ms / 1000,
            {},
            buckets=_LATENCY_BUCKETS,
        )


@dataclass(frozen=True)
class CapabilityDiscoveryObserverGroup:
    """Fan one observation out to several observers, isolating each.

    Isolation is per-observer rather than per-group: an unreachable journal must
    not cost the run its metrics, and a broken meter must not cost it its
    decision lineage.  The two are independently useful, so they fail
    independently.
    """

    observers: tuple[CapabilityDiscoveryObserver, ...] = ()

    async def observe(self, observation: CapabilityDiscoveryObservation) -> None:
        """Offer the observation to every member, surviving any of them."""

        for observer in self.observers:
            try:
                await observer.observe(observation)
            except Exception:
                _LOGGER.debug("capability_discovery.observer_failed", exc_info=True)


def _answer_search_block(
    tool: CapabilityBridgeToolName,
    answer: object,
) -> Mapping[str, Any] | None:
    if tool is not CapabilityBridgeToolName.SEARCH_CAPABILITIES:
        return None
    if not isinstance(answer, Mapping):
        return None
    block = answer.get("search")
    return block if isinstance(block, Mapping) else None


def _candidate_count(tool: CapabilityBridgeToolName, answer: object) -> int:
    block = _answer_search_block(tool, answer)
    if block is None:
        return 0
    candidates = block.get("candidates")
    return len(candidates) if isinstance(candidates, (list, tuple)) else 0


def _scanned_count(tool: CapabilityBridgeToolName, answer: object) -> int:
    block = _answer_search_block(tool, answer)
    if block is None:
        return 0
    scanned = block.get("scanned_count")
    if not isinstance(scanned, int) or isinstance(scanned, bool):
        return 0
    return max(scanned, 0)


__all__ = (
    "CapabilityDiscoveryMetrics",
    "CapabilityDiscoveryObservation",
    "CapabilityDiscoveryObserver",
    "CapabilityDiscoveryObserverGroup",
    "CapabilityDiscoveryOutcome",
    "CapabilityDiscoveryPhase",
    "CapabilityExpansionObservation",
    "CapabilityExpansionObserver",
    "ObservedCapabilityBridgeTool",
    "ObservedTwoTierCapabilitySearch",
    "RunJournalDiscoveryDecisionRecorder",
    "digest_request",
    "estimate_answer_tokens",
)
