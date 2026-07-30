"""The single worker-owned F6 batch-concurrency assembly.

This module answers one question for one run: *may this run overlap capability
work, and for which capabilities*.  It composes pieces that already exist —
F6.1's :class:`ConcurrencyPolicyResolver`, F6.2's :class:`BatchPlanRecorder` over
the canonical run event journal, F6.3's :class:`BatchExecutionCoordinator`, F6.4's
:class:`RunPermitManager`, F6.6's child transition journal, and F6.7's kill-switch
gate — and decides nothing about authorization itself.  Every child still enters
the Operation Gateway through the same tool call the model made, under the same
middleware, behind the same run-scoped admission.

Four properties are load-bearing and enforced structurally rather than by
convention.

*Fail dark, never fail open.*  Absent configuration, an F6 run mode that is not
``enforce``, an empty capability catalog, or any construction fault leaves
:meth:`BatchConcurrencyComposer.compose` returning ``None``.  The run handler
then installs nothing, the middleware's two context reads return ``None``, and
the run stays on the untouched pre-F6 serial path.  Nothing here can raise into a
healthy run.

*The presence gate is read before the subsystem is imported.*
:func:`build_batch_concurrency_composer` returns ``None`` without importing
``agent_runtime.capabilities.concurrency`` at all, so an unconfigured
deployment's import graph is the pre-F6 one — parity in the work done, not only
in the behaviour observed.  Every F6 import in this module is therefore deferred
into a method body, and every F6 annotation resolves only under
``TYPE_CHECKING``.

*Two independent authorities must agree.*  Overlap requires the control plane's
own F6 run mode to be ``enforce`` **and** an operator-declared capability
catalog.  Neither alone is enough: the mode without a catalog leaves every
capability at the conservative floor, which the planner turns into serial
segments; the catalog without the mode leaves the snapshot allowance at ``off``,
which narrows every batch to serial before the planner runs.

*Undeclared is serial, and only a catalog may establish.*  Policies are resolved
through F6.1's precedence, where ``PRODUCT_CATALOG`` is the one source that may
*establish* a policy and everything else may only narrow it.  A capability the
operator never described therefore resolves to the conservative floor no matter
what any connector claims about itself.

*No batch is planned without a run-scoped approval authority.*  BUG-15 made
approval-gated work structurally unplannable and BUG-19 found that the authority
it named was never passed here, so the rule could never fire.  ``_compose`` now
constructs :class:`ToolUsePolicyApprovalSource` on every path — there is no
branch that yields an admission without one — and a run whose tool-use policy
could not be read gets a source that answers ``UNKNOWN`` rather than no source at
all.  The difference is load-bearing: an absent source leaves the catalog's own
claim untouched, so collapsing the two would make a broken policy lane quieter
than a missing one.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import logging
import os
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime.
    from agent_runtime.capabilities.concurrency.batch_recovery import RunRestartPlan
    from agent_runtime.capabilities.concurrency.descriptor_policy import (
        CapabilityConcurrencyDeclaration,
    )
    from agent_runtime.capabilities.concurrency.graph_admission import RunBatchAdmission
    from agent_runtime.capabilities.concurrency.kill_switches import (
        ConcurrencyKillSwitchSourcePort,
    )
    from agent_runtime.control_plane.context import RunControlBinding
    from agent_runtime.control_plane.ports import RunControlSnapshotStorePort
    from agent_runtime.execution.contracts import AgentRuntimeContext
    from agent_runtime.persistence.ports import EventStorePort
    from runtime_worker.batch_approval_authority import ToolUsePolicyApprovalSource


_LOGGER = logging.getLogger(__name__)


class LiveBatchAdmissionRegistry:
    """Which runs are executing F6 batches *in this process*, right now.

    Cancellation in this runtime is out of band: the API enqueues a command and
    a worker claims it as a claim of its own, so the coroutine that learns a run
    was cancelled is never the coroutine executing it.  The coordinator, by
    contrast, is per run and lives only in memory.  Something has to join those
    two facts, and this is the smallest thing that can: a lookup, not a second
    cancellation mechanism.

    Two properties are deliberate.

    *A miss is not an error.*  In a multi-worker deployment the cancel claim may
    land on a process that is not running the run, and then there is no live
    coordinator to stop — which is exactly the situation before this class
    existed.  The lookup returns ``None`` and the cancel handler does what it has
    always done.  This registry makes the same-process case *better*; it must
    never make the cross-process case *worse* by pretending to have reached
    something.

    *Registration is scoped to the run's own try/finally.*  The entry is removed
    on every exit path, so a process that has stopped executing a run cannot
    later be asked to cancel it and answer from a stale object.
    """

    __slots__ = ("_admissions",)

    #: A ceiling, not a target: one entry per run this process is executing, and
    #: the worker's own claim semaphore already bounds that far below this.
    MAX_TRACKED_RUNS: Final[int] = 1024

    def __init__(self) -> None:
        self._admissions: dict[str, RunBatchAdmission] = {}

    @property
    def tracked_runs(self) -> int:
        """Return how many runs currently have a live admission in this process."""

        return len(self._admissions)

    def register(self, *, run_id: str, admission: RunBatchAdmission) -> object | None:
        """Publish one run's live admission, returning a release token.

        The token is the handle a ``finally`` releases.  Returning one rather
        than having the caller re-derive the key keeps the removal paired with
        this exact registration, so a run that was never registered — because
        F6 is off, or because the table was full — releases nothing instead of
        evicting a namesake.
        """

        key = (run_id or "").strip()
        if not key or len(self._admissions) >= self.MAX_TRACKED_RUNS:
            return None
        self._admissions[key] = admission
        return key

    def release(self, token: object | None) -> None:
        """Withdraw one registration.  Idempotent, and safe on ``None``."""

        if isinstance(token, str):
            self._admissions.pop(token, None)

    def admission_for(self, run_id: str) -> RunBatchAdmission | None:
        """Return the live admission for one run, if this process holds it."""

        return self._admissions.get((run_id or "").strip())


class BatchConcurrencyEnvironment:
    """Bounded, conservative environment parsing for the worker-only F6 lane.

    Nothing here raises on a malformed value.  F6's own rule is that an absent,
    unknown, or unparseable control resolves to the *narrowest* posture, so a
    configuration typo can only remove overlap — never a run.
    """

    #: Presence gate *and* the deployment's parallelism ceiling.  One variable
    #: rather than two because "F6 is configured" and "F6 may reach width N"
    #: are the same operator decision, and splitting them would create a state
    #: where the subsystem is built and can never do anything.
    MAX_PARALLELISM = "F6_BATCH_CONCURRENCY"
    CATALOG = "F6_CONCURRENCY_CATALOG"
    KILL_SWITCH = "F6_CONCURRENCY_KILL_SWITCH"

    SERIAL_PARALLELISM: Final[int] = 1
    MAX_ALLOWED_PARALLELISM: Final[int] = 100
    MAX_CATALOG_ENTRIES: Final[int] = 256

    @classmethod
    def is_configured(cls, environ: Mapping[str, str] | None = None) -> bool:
        """Return whether an operator has configured F6 batch concurrency.

        The one cheap pre-gate read before anything F6-owned is imported.  It
        tests *presence*, never meaning, so it cannot drift into a second
        activation vocabulary: any value that is present — including a
        misspelling — still goes through the conservative parse below.
        """

        source = environ if environ is not None else os.environ
        return bool(source.get(cls.MAX_PARALLELISM, "").strip())

    @classmethod
    def max_parallelism(cls, environ: Mapping[str, str] | None = None) -> int:
        """Return the deployment ceiling, defaulting to serial on anything invalid."""

        source = environ if environ is not None else os.environ
        raw = source.get(cls.MAX_PARALLELISM, "").strip()
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return cls.SERIAL_PARALLELISM
        if value < cls.SERIAL_PARALLELISM:
            return cls.SERIAL_PARALLELISM
        return min(value, cls.MAX_ALLOWED_PARALLELISM)

    @classmethod
    def raw_catalog(cls, environ: Mapping[str, str] | None = None) -> object:
        """Return the operator's declaration document, or ``None``."""

        source = environ if environ is not None else os.environ
        raw = source.get(cls.CATALOG, "").strip()
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            # An unreadable catalog declares nothing, which is the conservative
            # floor for every capability — the same answer as no catalog.
            _LOGGER.warning("F6 concurrency catalog is not valid JSON; ignoring it")
            return None

    @classmethod
    def raw_kill_switch(cls, environ: Mapping[str, str] | None = None) -> str | None:
        """Return the operator's raw kill-switch directive string, or ``None``."""

        source = environ if environ is not None else os.environ
        return source.get(cls.KILL_SWITCH, "").strip() or None


class EnvironmentConcurrencyKillSwitchSource:
    """Read the deployment's kill-switch directives from the process environment.

    Synchronous and re-read on every admission, matching
    ``ConcurrencyKillSwitchSourcePort``'s contract.  A process environment is a
    static deployment control rather than a live one, so this adapter is the
    floor: a deployment that wants a switch it can flip without a restart injects
    a different source into :class:`BatchConcurrencyComposer`, and everything
    downstream — gate, plan record, live allowance supplier — is unchanged.
    """

    __slots__ = ("_environ",)

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._environ = environ

    def current_kill_switch_directives(self) -> object:
        """Return the raw directive value, unparsed, for the resolver to read."""

        return BatchConcurrencyEnvironment.raw_kill_switch(self._environ)


class BatchConcurrencyComposer:
    """Compose one run's F6 admission after the run-control binding exists.

    Constructed once per worker.  ``compose`` is called per run and returns the
    single object the run handler installs into both admission slots, or ``None``
    to leave the run exactly as serial as it has always been.
    """

    def __init__(
        self,
        *,
        events: EventStorePort,
        snapshots: RunControlSnapshotStorePort,
        environ: Mapping[str, str] | None = None,
        kill_switch_source: ConcurrencyKillSwitchSourcePort | None = None,
    ) -> None:
        self._events = events
        self._snapshots = snapshots
        self._environ = environ
        self._kill_switch_source = kill_switch_source or (
            EnvironmentConcurrencyKillSwitchSource(environ)
        )

    def compose(
        self,
        *,
        org_id: str,
        trace_id: str,
        control: RunControlBinding,
        runtime_context: AgentRuntimeContext | None,
    ) -> RunBatchAdmission | None:
        """Return this run's batch admission, or ``None`` to stay serial.

        ``runtime_context`` carries the run's tool-use policy and has no
        default: a composition root that has no context must say so and get the
        conservative authority, rather than silently reproduce the BUG-19 state
        where the seam consulted nothing.
        """

        try:
            return self._compose(
                org_id=org_id,
                trace_id=trace_id,
                control=control,
                runtime_context=runtime_context,
            )
        except Exception:  # noqa: BLE001 - an uncomposable run is a serial run.
            _LOGGER.warning("F6 batch concurrency did not compose", exc_info=True)
            return None

    async def aplan_restart(
        self,
        *,
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
    ) -> RunRestartPlan | None:
        """Return what this run's durable journal permits a restart to repeat.

        Called at the two moments a process begins executing a run it may not
        have started — a worker claim and an approval resume — because those are
        the moments at which "did somebody already do this?" stops being
        answerable from memory.

        On the overwhelmingly common first attempt the journal holds no batch
        plan and this answers ``None`` after one read.  It is deliberately a
        separate call from :meth:`compose` rather than folded into it: composing
        is synchronous and infallible-by-design, and making it await a store
        would put a database read on the path of every run F6 does not even
        apply to.
        """

        try:
            return await self._recovery().aplan(
                org_id=org_id,
                run_id=run_id,
                subject_fingerprint=subject_fingerprint,
            )
        except Exception:  # noqa: BLE001 - an unreadable journal decides nothing.
            _LOGGER.warning("F6 restart recovery did not plan", exc_info=True)
            return None

    def _journal(self) -> Any:
        """Build the one durable store every F6 write and read in a run uses.

        A single seam rather than a construction at each call site, so the plan
        recorder, the child transition recorder, and the restart recovery cannot
        end up reading and writing different journals.
        """

        from agent_runtime.capabilities.concurrency.batch_journal_store import (
            EventJournalBatchPlanStore,
        )

        return EventJournalBatchPlanStore(
            events=self._events,
            snapshots=self._snapshots,
        )

    def _recovery(self) -> Any:
        """Build the recovery service over the same one journal F6 already writes."""

        from agent_runtime.capabilities.concurrency.batch_run_recovery import (
            BatchRunRecovery,
        )

        return BatchRunRecovery(store=self._journal())

    def _approvals(
        self,
        runtime_context: AgentRuntimeContext | None,
    ) -> ToolUsePolicyApprovalSource:
        """Return the run-scoped approval authority this admission consults.

        Never ``None``, on any path. BUG-15 built the seam that refuses a plan
        entry to a capability whose dispatch can park and BUG-19 found that the
        only construction site in ``src/`` passed no source, so the seam read
        ``None`` — *not declared* — and the catalog's claim went unchallenged on
        every call. Returning a value unconditionally is what makes that state
        unreachable rather than merely fixed once.

        A context that is absent, or a policy that will not resolve, yields a
        source that answers ``UNKNOWN`` for everything. That is the conservative
        outcome and it is structural: the run then plans no batch at all, which
        is exactly the pre-F6 serial path.
        """

        from agent_runtime.capabilities.tools.tool_use_enforcement import (
            ToolUsePolicyResolver,
        )
        from runtime_worker.batch_approval_authority import (
            ToolUsePolicyApprovalSource,
        )

        if runtime_context is None:
            return ToolUsePolicyApprovalSource(None)
        try:
            snapshot = ToolUsePolicyResolver.resolve(runtime_context)
        except Exception:  # noqa: BLE001 - an unread policy is an unknown one.
            _LOGGER.warning("F6 could not resolve the run tool-use policy")
            return ToolUsePolicyApprovalSource(None)
        return ToolUsePolicyApprovalSource(snapshot)

    def _compose(
        self,
        *,
        org_id: str,
        trace_id: str,
        control: RunControlBinding,
        runtime_context: AgentRuntimeContext | None,
    ) -> RunBatchAdmission | None:
        from agent_runtime.capabilities.concurrency.batch_child_journal import (
            BatchChildTransitionRecorder,
            BatchRunBinding,
        )
        from agent_runtime.capabilities.concurrency.batch_coordinator import (
            BatchChildIdentity,
            BatchExecutionCoordinator,
        )
        from agent_runtime.capabilities.concurrency.batch_journal import (
            BatchPlanRecorder,
        )
        from agent_runtime.capabilities.concurrency.contracts import (
            ConcurrencyAllowance,
            ConcurrencyScope,
            PermitAcquisitionRequest,
            PermitCapacityPolicy,
            PermitScope,
        )
        from agent_runtime.capabilities.concurrency.graph_admission import (
            DeclaredConcurrencyPolicySource,
            RunBatchAdmission,
        )
        from agent_runtime.capabilities.concurrency.kill_switches import (
            ConcurrencyKillSwitchAllowanceSupplier,
            ConcurrencyKillSwitchGate,
        )
        from agent_runtime.capabilities.concurrency.permits import RunPermitManager
        from agent_runtime.control_plane.feature_modes import FeatureMode

        snapshot = control.snapshot
        if control.effective_modes.f6 is not FeatureMode.ENFORCE:
            # Shadow and off both resolve to the F6 safe fallback, which the
            # Step-0 policy map defines as serial. Composing the machinery to
            # then narrow every batch to width one would journal plans nothing
            # could act on.
            return None
        declarations = self._declarations()
        if not declarations:
            return None

        ceiling = BatchConcurrencyEnvironment.max_parallelism(self._environ)
        gate = ConcurrencyKillSwitchGate(
            snapshot_allowance=ConcurrencyAllowance(
                mode=FeatureMode.ENFORCE,
                max_parallelism=ceiling,
            ),
            source=self._kill_switch_source,
        )
        journal = self._journal()
        profile_id = snapshot.deployment_profile
        subject_fingerprint = snapshot.subject_fingerprint

        def permit_scopes(identity: BatchChildIdentity) -> tuple[PermitScope, ...]:
            """Return the broad-to-narrow pools one child acquires at.

            Identity only. Width, wait mode, and timeout are the coordinator's,
            which is what keeps a scope factory from being a place overlap could
            be widened.
            """

            return PermitAcquisitionRequest.for_operation(
                profile_id=profile_id,
                subject_fingerprint=subject_fingerprint,
                capability_name=identity.capability_ref or identity.operation_id,
            ).scopes

        coordinator = BatchExecutionCoordinator(
            permits=RunPermitManager(
                # Every pool the ladder above acquires needs an explicit
                # capacity: an absent entry is serial, so one missing pool
                # would silently narrow the whole deployment to width one.
                policy=PermitCapacityPolicy.from_limits(
                    {
                        ConcurrencyScope.GLOBAL: ceiling,
                        ConcurrencyScope.PROFILE: ceiling,
                        ConcurrencyScope.USER: ceiling,
                        ConcurrencyScope.CAPABILITY: ceiling,
                    }
                )
            ),
            permit_scopes=permit_scopes,
            # F6.7's gate reaching the coordinator is what lets an operator
            # narrow a batch that is already running, rather than only the next
            # one to be planned.
            live_allowance=ConcurrencyKillSwitchAllowanceSupplier(gate),
            child_journal=BatchChildTransitionRecorder(
                journal=journal,
                binding=BatchRunBinding.of(
                    org_id=org_id,
                    trace_id=trace_id,
                    snapshot=snapshot,
                ),
            ),
        )
        return RunBatchAdmission(
            recorder=BatchPlanRecorder(journal=journal, gate=gate),
            coordinator=coordinator,
            policies=DeclaredConcurrencyPolicySource(declarations),
            snapshot=snapshot,
            org_id=org_id,
            trace_id=trace_id,
            # The run half of the approval fact. The catalog above was written
            # by somebody who could not see this run's tool-use policy, so a
            # capability they honestly declared parallel-safe and non-parking
            # may still open a human decision on every dispatch here. Folding
            # the two is what makes BUG-15's rule reachable at all.
            approvals=self._approvals(runtime_context),
        )

    def _declarations(
        self,
    ) -> dict[str, tuple[CapabilityConcurrencyDeclaration, ...]]:
        """Parse the operator catalog into per-capability declarations.

        Every entry is attributed to ``PRODUCT_CATALOG`` because that is the
        trust boundary it crossed: it is deployment configuration, written by
        whoever operates the deployment, not metadata a connector sent.  The
        parser itself is F6.1's, so an unparseable field narrows to its
        vocabulary's conservative floor and is recorded as a typed rejection
        rather than silently ignored.
        """

        from agent_runtime.capabilities.concurrency.contracts import PolicySource
        from agent_runtime.capabilities.concurrency.descriptor_policy import (
            ConcurrencyDescriptorParser,
        )
        from agent_runtime.capabilities.concurrency.graph_admission import (
            graph_capability_ref,
        )

        raw = BatchConcurrencyEnvironment.raw_catalog(self._environ)
        if not isinstance(raw, Mapping):
            return {}
        parser = ConcurrencyDescriptorParser()
        declarations: dict[str, tuple[CapabilityConcurrencyDeclaration, ...]] = {}
        for capability_key, payload in raw.items():
            if len(declarations) >= BatchConcurrencyEnvironment.MAX_CATALOG_ENTRIES:
                break
            if not isinstance(capability_key, str) or not capability_key.strip():
                continue
            key = capability_key.strip()
            try:
                declaration = parser.parse(
                    capability_ref=graph_capability_ref(key),
                    source=PolicySource.PRODUCT_CATALOG,
                    payload=payload if isinstance(payload, Mapping) else None,
                )
            except Exception:  # noqa: BLE001 - one bad entry declares nothing.
                _LOGGER.warning("F6 concurrency catalog entry was rejected")
                continue
            declarations[key] = (declaration,)
        return declarations


async def activate_batch_admission(
    *,
    composer: BatchConcurrencyComposer | None,
    registry: LiveBatchAdmissionRegistry | None,
    admission: Any,
    org_id: str,
    run_id: str,
    subject_fingerprint: str,
) -> object | None:
    """Bind a composed admission to its durable past and to the cancel claim.

    One function rather than two blocks in two handlers, because the run path
    and the approval-resume path must do *identically* this: a resumed run that
    skipped the restart decision would replay exactly the writes the run path
    withheld, and the bug would live in the gap between two copies of the same
    six lines.

    Returns the registry token the caller's ``finally`` releases.  Ordering is
    load-bearing: the restart decision is adopted *before* the admission is
    published, so a cancel arriving the instant it becomes visible finds an
    admission that already knows what it may not re-run.
    """

    if composer is not None:
        admission.adopt_restart_plan(
            await composer.aplan_restart(
                org_id=org_id,
                run_id=run_id,
                subject_fingerprint=subject_fingerprint,
            )
        )
    if registry is None:
        return None
    return registry.register(run_id=run_id, admission=admission)


def build_batch_concurrency_composer(
    *,
    events: EventStorePort | None,
    snapshots: RunControlSnapshotStorePort | None,
    environ: Mapping[str, str] | None = None,
    kill_switch_source: Any | None = None,
) -> BatchConcurrencyComposer | None:
    """Return the worker's F6 composer, or ``None`` when it is not configured.

    The presence gate is read *here*, before the composer exists and therefore
    before any F6 module is imported.  That ordering is the whole point: an
    unconfigured deployment must not merely behave as it did, it must do the
    same work and load the same modules, and a gate read inside the composer
    would already have paid the import.
    """

    if events is None or snapshots is None:
        return None
    if not BatchConcurrencyEnvironment.is_configured(environ):
        return None
    return BatchConcurrencyComposer(
        events=events,
        snapshots=snapshots,
        environ=environ,
        kill_switch_source=kill_switch_source,
    )


__all__ = (
    "BatchConcurrencyComposer",
    "BatchConcurrencyEnvironment",
    "EnvironmentConcurrencyKillSwitchSource",
    "LiveBatchAdmissionRegistry",
    "activate_batch_admission",
    "build_batch_concurrency_composer",
)
