"""The capabilities-side join between one model turn and the F6 machinery.

Seven lanes built F6 and every one of them stopped short of the graph. The
result was a subsystem that could plan, journal, permit, gate, cancel, and
restart batches, and a runtime in which no batch ever existed: nothing turned a
model's tool calls into an :class:`~agent_runtime.capabilities.concurrency.contracts.OperationBatch`,
so the effective width of every run stayed ``1``. This module is that missing
translation, and it is deliberately the only one.

It implements **both** halves of the graph seam, on this side of the boundary:

- :class:`~agent_runtime.control_plane.parallel_admission.ParallelAdmissionPort`,
  which answers how wide one call may run. ``control_plane`` may never import
  ``capabilities.concurrency`` — that edge is a genuine cycle and an
  architecture test pins it — so the port is declared there and satisfied here,
  the same direction ``BatchAllowanceSupplier`` and
  ``ConcurrencyKillSwitchSourcePort`` already point.
- :class:`~agent_runtime.execution.contracts.RuntimeBatchAdmissionPort`, which
  carries the two moments only the graph knows: a model turn produced tool
  calls, and one of those calls is about to run.

Five properties are structural rather than conventional.

**A child cannot dispatch before its plan is durable.** ``aplan_model_batch`` is
awaited from ``aafter_model``, which the framework runs to completion before it
routes to the tool node, and it registers a batch with the coordinator only
after :meth:`BatchPlanRecorder.record` has returned a
:class:`~agent_runtime.capabilities.concurrency.batch_journal.DurableBatchPlan`
— a value only a completed append produces. There is no ordering to remember:
the handle that authorizes dispatch is the receipt for the write.

**Unknown means serial, by construction.** A call reaches F6 only by being found
in an index built from a durable plan, keyed by the provider's own tool-call id.
No verified run binding, fewer than two calls in the turn, an unparseable turn, a
policy the operator never declared, a plan that refused to persist, an
unrecognised call, a call already claimed — every one of them leaves the index
without an entry, and an absent entry is answered by the pre-F6 exclusive permit
and an unmediated body. The serial path is what happens when nothing positively
says otherwise, rather than what a branch selects.

**Width comes from the plan, never from a lease.** The Step-2 admission gate runs
*before* the coordinator sees the child at all, so the grant is bounded by
:meth:`BatchExecutionCoordinator.planned_allowance` — segment ∧ plan ∧ live kill
switch, all of which exist before any permit is acquired. ``RunPermitManager``
narrows again inside ``run_child``. Both narrow; neither widens.

**A refused child never runs.** ``run_child`` settles a refusal durably, so
falling back to the body afterwards would leave the journal claiming a child was
refused while its external effect happened. A refusal is raised as the closed
:class:`BatchChildExecutionError` the vocabulary already has for exactly this —
work that never reached the connector.

**Nothing here can fail a healthy run.** Planning is total: every fault, from a
malformed tool call to a journal that would not accept the plan, ends with the
turn unplanned and therefore serial. The only exception this module raises is the
refusal above, which is a decision rather than a fault.

**Approval-gated work is never planned at all, whatever the catalog says.** The
declared effect class and the approval requirement were decided by authorities
that never consulted each other: an operator could write ``side_effect=read`` and
``mode=parallel_safe`` about a capability whose every dispatch opens a human
decision, and the planner would admit the whole turn to one parallel segment.
:class:`GraphApprovalRequirementSource` is the run-scoped half of that fact — the
tool-use policy's ``interrupt_on`` set, the connector's live auth state — folded
with the catalog's half by ``narrowest``, so a run may only ever make a
capability *more* approval-bound than the catalog claimed.

A call that may park is then refused a plan entry, which is the same route this
module already takes for everything it cannot positively establish, and it is
deliberately stronger than planning it into serial segments. A parked child under
the coordinator's segment gate holds that gate while it waits on a person: its
siblings wait out the 300-second admission budget and are then refused, and the
suspend itself is settled durably as ``FAILED``, because a ``GraphInterrupt``
reaches the coordinator as an ordinary ``Exception``. An unplanned turn has none
of that — it is the pre-F6 exclusive permit, which parks one call at a time and
records nothing it would later have to retract.

One consequence is deliberate and worth stating plainly, because it narrows what
F6 plans at all: **an undeclared capability is now unplannable, not merely
serial.** Silence about a capability includes silence about whether it parks, and
the default deployment policy already gates ``call_mcp_tool`` — the single
umbrella every connector action flows through — at ``write=ask``. So the common
undeclared call is a parking one. Combined with the existing rule that one
unusable call makes the whole turn unplannable, F6 now records a batch only when
it positively knows *every* call in the turn is a non-parking capability an
operator declared parallel-safe. Turns it does not know that about run exactly as
they did before F6 existed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any, Final, Protocol, runtime_checkable

from agent_runtime.capabilities.concurrency.batch_cancellation import (
    BatchCancellationReason,
    BatchCancellationReport,
)
from agent_runtime.capabilities.concurrency.batch_coordinator import (
    BatchChildAdmission,
    BatchChildStatus,
    BatchExecutionCoordinator,
)
from agent_runtime.capabilities.concurrency.batch_recovery import RunRestartPlan
from agent_runtime.capabilities.concurrency.batch_run_recovery import (
    withheld_operation_ids,
)
from agent_runtime.capabilities.concurrency.batch_journal import (
    BatchJournalLimits,
    BatchPlanRecorder,
    BatchPlanRequest,
    PlannedOperation,
)
from agent_runtime.capabilities.concurrency.child_execution import (
    BatchChildExecutionError,
    BatchChildExecutionReason,
    BatchChildWork,
    RunScopedBatchChildWork,
)
from agent_runtime.capabilities.concurrency.contracts import (
    ApprovalRequirement,
    BatchFailurePolicy,
    BatchOperation,
    ConcurrencyPolicy,
)
from agent_runtime.capabilities.concurrency.descriptor_policy import (
    CapabilityConcurrencyDeclaration,
    ConcurrencyPolicyResolution,
    ConcurrencyPolicyResolver,
)
from agent_runtime.control_plane.contracts import RunControlSnapshot
from agent_runtime.control_plane.parallel_admission import (
    ParallelAdmissionGrant,
    ToolAdmissionRequest,
)
from agent_runtime.execution.call_identity import RuntimeToolCallIdentity
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256

_LOGGER = logging.getLogger(__name__)


class GraphBatchBounds:
    """Hard, content-free bounds on what one run's graph seam may hold."""

    #: One model turn can plan at most what a batch may name.
    MAX_OPERATIONS_PER_TURN: Final[int] = BatchJournalLimits.MAX_OPERATIONS
    #: Live planned children across every batch of one run. Entries are removed
    #: as they are claimed, so this bounds concurrent turns, not run length.
    MAX_TRACKED_CHILDREN: Final[int] = 1024
    #: Truncation guard for the opaque key a capability is looked up by.
    MAX_CAPABILITY_KEY_LENGTH: Final[int] = 512


class GraphBatchMessages:
    """Public, content-free text for the one fault this module can surface."""

    CAPABILITY_REF_PREFIX = "cap_"


@runtime_checkable
class GraphConcurrencyPolicySource(Protocol):
    """Resolve the F6.1 policy that governs one graph-visible tool call.

    Returning ``None`` is the undeclared case and is the common one: it yields a
    conservative policy, which the planner turns into a serial segment. A source
    is therefore only ever able to *permit* overlap for capabilities somebody
    with catalog authority positively described.
    """

    def resolution_for(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> ConcurrencyPolicyResolution | None:
        """Return the resolved policy for this call, or ``None`` if undeclared."""


class DeclaredConcurrencyPolicySource:
    """Resolve policies from a fixed, operator-supplied declaration table.

    The table is keyed by the same opaque capability key
    :func:`graph_capability_key` derives, so a declaration and a live call agree
    about what a capability *is* without either one carrying arguments. The
    resolver is F6.1's own: this class chooses no policy, it only decides which
    declarations apply, and every declaration it holds is one an operator with
    catalog authority wrote.

    Immutable after construction. A run cannot acquire a policy mid-flight, so
    the ordering a turn was planned under is the ordering the whole turn runs
    under.
    """

    __slots__ = ("_declarations", "_resolver")

    def __init__(
        self,
        declarations: Mapping[str, Sequence[CapabilityConcurrencyDeclaration]],
        *,
        resolver: ConcurrencyPolicyResolver | None = None,
    ) -> None:
        self._declarations = {
            key: tuple(value) for key, value in declarations.items() if value
        }
        self._resolver = resolver or ConcurrencyPolicyResolver()

    def __len__(self) -> int:
        return len(self._declarations)

    def resolution_for(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> ConcurrencyPolicyResolution | None:
        """Return the resolved policy for this call, or ``None`` if undeclared."""

        key = graph_capability_key(tool_name=tool_name, arguments=arguments)
        declarations = self._declarations.get(key)
        if not declarations:
            return None
        capability_ref = graph_capability_ref(key)
        if any(
            declaration.capability_ref != capability_ref for declaration in declarations
        ):
            # A declaration filed under a key it does not describe cannot be
            # attributed to this call, and guessing which one was meant would be
            # the one place an operator typo could widen something.
            return None
        return self._resolver.resolve(
            capability_ref=capability_ref,
            declarations=declarations,
        )


@runtime_checkable
class GraphApprovalRequirementSource(Protocol):
    """Answer whether one graph-visible call's dispatch can park for approval.

    This is the *run-scoped* half of the approval fact, and it exists because
    the catalog half cannot cover it. A ``PRODUCT_CATALOG`` author can say that
    a capability's own execution involves no human decision; they cannot know
    that **this run's** tool-use policy sets the ``read`` axis to ``ask``, or
    that the connector this call targets is currently unauthenticated and will
    open a Connect card. Those facts belong to the run, arrive after the catalog
    was written, and can only narrow — which is exactly the shape F6.1 already
    composes.

    The two known production sources are the ``interrupt_on`` map the tool-use
    policy enforcer builds (keyed by graph tool name) and the MCP access gate's
    live auth state. Neither is reachable from this package, so this is a port:
    declared here, satisfied by whoever composes the run.
    """

    def approval_requirement_for(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> ApprovalRequirement:
        """Return what this run knows about the call's approval requirement."""


class DeclaredApprovalRequirementSource:
    """Answer from an explicit set of tool names this run will interrupt on.

    Models the one production fact that is already computed and already keyed
    the right way: ``EnforcedToolSurface.interrupt_on``. A tool named there
    parks on every dispatch under ``require`` and on the first under ``ask``,
    so ``ALWAYS`` is the honest reading for both — neither permits overlap, and
    distinguishing them would be a distinction without a decision.

    Everything else answers ``unknown_requirement``, which defaults to
    ``UNKNOWN``. A caller that has genuinely enumerated its run's whole approval
    surface may pass ``NEVER`` for the remainder; a caller that has only a
    partial view must not, and gets serial for the rest.
    """

    __slots__ = ("_gated", "_unknown_requirement")

    def __init__(
        self,
        gated_tool_names: Sequence[str] | frozenset[str] = (),
        *,
        unknown_requirement: ApprovalRequirement = ApprovalRequirement.UNKNOWN,
    ) -> None:
        self._gated = frozenset(
            name.strip() for name in gated_tool_names if isinstance(name, str)
        ) - {""}
        self._unknown_requirement = unknown_requirement

    def approval_requirement_for(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> ApprovalRequirement:
        """Return ``ALWAYS`` for a gated tool, else the configured remainder."""

        del arguments
        if tool_name.strip() in self._gated:
            return ApprovalRequirement.ALWAYS
        return self._unknown_requirement


class GraphApprovalRequirementResolver:
    """The single fail-closed reading of an approval source's answer.

    Two absences that look alike are kept apart, exactly as F6.1 keeps them
    apart for declarations:

    - **No source at all** answers ``None`` — *not declared*. The catalog's own
      value survives untouched, which is what an absent declaration has always
      meant. This is not a hole: the catalog's structural default is already
      ``UNKNOWN``, so a deployment where nobody has said anything about approval
      is serial by the field's default rather than by this resolver's.
    - **A source that exists and cannot answer** — one that raises, or returns
      something outside the closed vocabulary — answers ``UNKNOWN``. It was
      asked and it failed, which is a fact, and the conservative one.

    Collapsing the two would make wiring an approval source strictly worse than
    not wiring one, which is the wrong incentive to build into a safety seam.
    """

    @staticmethod
    def resolve(
        source: GraphApprovalRequirementSource | None,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> ApprovalRequirement | None:
        """Return the run's declaration, ``UNKNOWN`` on fault, or ``None``."""

        if source is None:
            return None
        try:
            requirement = source.approval_requirement_for(
                tool_name=tool_name,
                arguments=arguments,
            )
        except Exception:  # noqa: BLE001 - a source that fails answers unknown.
            return ApprovalRequirement.UNKNOWN
        if not isinstance(requirement, ApprovalRequirement):
            return ApprovalRequirement.UNKNOWN
        return requirement


def graph_capability_key(*, tool_name: str, arguments: Mapping[str, Any]) -> str:
    """Return the opaque key one graph-visible call is declared under.

    An MCP call names its server and tool inside the arguments of the single
    ``call_mcp_tool`` surface, so keying on the graph tool name alone would file
    every MCP capability in the deployment under one policy. When both are
    present the key is ``server:tool``; otherwise it is the graph tool's own
    name. Nothing else from the arguments is ever read, so the key cannot carry
    a body.
    """

    server = arguments.get("server_name")
    inner = arguments.get("tool_name")
    if isinstance(server, str) and isinstance(inner, str):
        server = server.strip()
        inner = inner.strip()
        if server and inner:
            return f"{server}:{inner}"[: GraphBatchBounds.MAX_CAPABILITY_KEY_LENGTH]
    return tool_name.strip()[: GraphBatchBounds.MAX_CAPABILITY_KEY_LENGTH]


def graph_capability_ref(capability_key: str) -> str:
    """Return the opaque ``cap_`` reference a capability key is journaled as.

    F6's records constrain a capability reference to ``cap_`` plus 32 hex
    characters and otherwise treat it as opaque. Deriving it from the key by
    digest keeps two properties at once: the same capability produces the same
    reference in every run, so a journal is comparable across runs; and the
    reference reveals nothing about the connector, because a digest of a name is
    not the name.
    """

    digest = canonical_json_sha256({"capability_key": capability_key})
    return f"{GraphBatchMessages.CAPABILITY_REF_PREFIX}{digest[:32]}"


@dataclass(frozen=True, slots=True)
class PlannedGraphChild:
    """One durable plan entry, reachable from the id the provider assigned."""

    batch_id: str
    operation_id: str
    model_tool_call_id: str


class RunBatchAdmission:
    """One run's translation between graph tool calls and F6 batches.

    Constructed per run by the composition root and installed twice: into
    ``RunSerialAdmission`` as the width authority, and into
    :class:`~agent_runtime.execution.contracts.RuntimeBatchAdmissionContext` as
    the execution route. Both installs point at the *same object*, which is what
    makes "the width a call was granted" and "the batch it actually ran in" two
    readings of one decision rather than two decisions that could disagree.

    Single-loop, like every other F6 component: the index is mutated only from
    coroutines on the run's own event loop, and every mutation is await-free, so
    the check and the claim are atomic against the calls they gate.
    """

    __slots__ = (
        "_approvals",
        "_children",
        "_coordinator",
        "_deadline_at",
        "_org_id",
        "_planned_batches",
        "_policies",
        "_recorder",
        "_restart_plan",
        "_snapshot",
        "_trace_id",
        "_work",
    )

    def __init__(
        self,
        *,
        recorder: BatchPlanRecorder,
        coordinator: BatchExecutionCoordinator,
        policies: GraphConcurrencyPolicySource,
        snapshot: RunControlSnapshot,
        org_id: str,
        trace_id: str,
        deadline_at: datetime | None = None,
        approvals: GraphApprovalRequirementSource | None = None,
    ) -> None:
        self._recorder = recorder
        self._coordinator = coordinator
        self._policies = policies
        self._snapshot = snapshot
        self._org_id = org_id
        self._trace_id = trace_id
        self._deadline_at = deadline_at
        self._approvals = approvals
        self._children: dict[str, PlannedGraphChild] = {}
        self._work: dict[str, RunScopedBatchChildWork] = {}
        self._planned_batches: set[str] = set()
        self._restart_plan: RunRestartPlan | None = None

    @property
    def tracked_children(self) -> int:
        """Return how many planned children are currently claimable."""

        return len(self._children)

    @property
    def restart_plan(self) -> RunRestartPlan | None:
        """Return the durable restart decision this run is executing under."""

        return self._restart_plan

    def work_for_batch(self, batch_id: str) -> RunScopedBatchChildWork | None:
        """Return one batch's immutable child work table, if it was planned."""

        return self._work.get(batch_id)

    def adopt_restart_plan(self, plan: RunRestartPlan | None) -> None:
        """Make one run's durable restart decision binding on its execution.

        The plan is the *finding*; this is what turns it into a rule. Without
        this call a restarted run re-plans the same turn from the same replayed
        tool calls and dispatches every one of them, which is precisely the
        replay F6.6's evidence rules were derived to prevent — the analysis
        would be correct and unread.

        Only the withheld half crosses into the coordinator. "Resume this safe
        read" needs no instruction because running is already what an admitted
        child does; the decision that has to be carried is the one that stops
        something from happening.
        """

        if plan is None:
            return
        self._restart_plan = plan
        self._coordinator.withhold_on_restart(withheld_operation_ids(plan))

    async def acancel(
        self,
        *,
        reason: BatchCancellationReason = BatchCancellationReason.RUN_CANCELLED,
        drain_seconds: float | None = None,
    ) -> BatchCancellationReport | None:
        """Stop this run's batches and return what that honestly achieved.

        The route F6.6 built and nothing reached. It delegates rather than
        re-implements, so the ordering the coordinator's own docstring
        argues for — admission stops synchronously, only cancellable reads are
        interrupted, the drain is bounded, whatever is left is *indeterminate*
        and durably recorded as such — is the ordering production gets.

        Total by contract, and that matters more here than anywhere else in this
        class: this is called while a run is being torn down, frequently from a
        handler that has already decided the run is over. A cancellation route
        that could raise would turn "we could not describe the cancellation"
        into "the cancellation failed", and the caller has no better answer to
        give than the one it was already giving. ``None`` says the report could
        not be produced; it never says nothing was in flight.
        """

        try:
            return await self._coordinator.cancel(
                reason=reason,
                drain_seconds=drain_seconds,
            )
        except Exception:  # noqa: BLE001 - a run being cancelled is already over.
            _LOGGER.warning("F6 batch cancellation did not complete", exc_info=True)
            return None

    async def aplan_model_batch(
        self,
        *,
        execution_scope: str,
        model_turn: int,
        tool_calls: Sequence[Mapping[str, Any]],
    ) -> None:
        """Plan and durably record one model turn's tool calls.

        Total by contract. Every refusal below leaves the index untouched, and an
        untouched index is a serial turn — so this method has exactly one
        externally visible effect, and its absence is the pre-F6 behaviour.
        """

        try:
            await self._aplan(
                execution_scope=execution_scope,
                model_turn=model_turn,
                tool_calls=tool_calls,
            )
        except Exception:  # noqa: BLE001 - an unplannable turn is a serial turn.
            return

    def grant_for(
        self,
        request: ToolAdmissionRequest,
    ) -> ParallelAdmissionGrant | None:
        """Return the width this call may overlap at, or ``None`` for serial.

        Synchronous and allocation-light because the Step-2 admission consults it
        on the hot path before taking any lock. It reads the coordinator's
        pre-permit fold and nothing else, so it can neither wait for, nor be
        blocked by, the gate it is answering for.
        """

        planned = self._children.get(request.tool_call_id)
        if planned is None:
            return None
        allowance = self._coordinator.planned_allowance(
            batch_id=planned.batch_id,
            operation_id=planned.operation_id,
        )
        width = allowance.effective_max_parallelism
        if not allowance.permits_parallel or width < 2:
            return None
        return ParallelAdmissionGrant(
            tool_call_id=planned.model_tool_call_id,
            cohort_id=self._cohort_id(planned),
            max_parallelism=width,
        )

    async def arun_tool_body(
        self,
        *,
        tool_call_id: str,
        body: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Run one graph tool body, through the coordinator when it is planned.

        The claim is consumed before anything is awaited, so a replayed or
        duplicated wrapper for the same provider id can never present itself to
        the coordinator twice — the second arrival is simply not a planned child,
        and runs exactly as it would have before F6.
        """

        planned = self._children.pop(tool_call_id, None)
        if planned is None:
            return await body()

        async def runner(admission: BatchChildAdmission) -> Any:
            del admission
            return await body()

        result = await self._coordinator.run_child(
            batch_id=planned.batch_id,
            operation_id=planned.operation_id,
            runner=runner,
        )
        if result.error is not None:
            raise result.error
        if result.outcome.status is not BatchChildStatus.SUCCEEDED:
            # The coordinator has already settled this child durably as one that
            # did not run. Running the body now would leave the journal saying
            # "refused" about work that reached the connector.
            raise BatchChildExecutionError(BatchChildExecutionReason.NOT_ADMITTED)
        return result.value

    async def _aplan(
        self,
        *,
        execution_scope: str,
        model_turn: int,
        tool_calls: Sequence[Mapping[str, Any]],
    ) -> None:
        """Build, persist, and register one turn's batch, or leave it serial."""

        turn = max(model_turn, 1)
        if len(tool_calls) < 2 or len(tool_calls) > (
            GraphBatchBounds.MAX_OPERATIONS_PER_TURN
        ):
            # A lone call has nothing to overlap with, and the planner would
            # place it in its own serial segment anyway. Skipping the write is
            # not an optimisation: it keeps the journal free of batches that
            # never described a decision.
            return
        if (
            len(self._children) + len(tool_calls)
            > GraphBatchBounds.MAX_TRACKED_CHILDREN
        ):
            return
        batch_id = self._batch_id(execution_scope=execution_scope, model_turn=turn)
        if batch_id in self._planned_batches:
            return

        planned_operations: list[PlannedOperation] = []
        work: list[BatchChildWork] = []
        children: list[PlannedGraphChild] = []
        for tool_call in tool_calls:
            prepared = self._prepare(
                tool_call=tool_call,
                execution_scope=execution_scope,
                model_turn=turn,
            )
            if prepared is None:
                # One unusable call makes the whole turn unplannable rather than
                # partially planned: a batch that silently omitted a call would
                # let that call run outside every ordering decision F6 made
                # about its siblings.
                return
            operation, child_work, child = prepared
            planned_operations.append(operation)
            work.append(child_work)
            children.append(child)

        request = BatchPlanRequest(
            org_id=self._org_id,
            trace_id=self._trace_id,
            subject_fingerprint=self._snapshot.subject_fingerprint,
            run_id=self._snapshot.run_id,
            batch_id=batch_id,
            turn_ordinal=turn,
            operations=tuple(planned_operations),
            deadline_at=self._deadline_at,
            # Independent reads have no reason to stop one another: a sibling
            # that failed says nothing about the ones still running, and
            # stopping them would turn one connector error into several.
            failure_policy=BatchFailurePolicy.COLLECT_ALL,
        )
        plan = await self._recorder.record(request, snapshot=self._snapshot)
        self._coordinator.begin(plan)
        self._planned_batches.add(batch_id)
        self._work[batch_id] = RunScopedBatchChildWork(work)
        for child in children:
            self._children[child.model_tool_call_id] = child

    def _prepare(
        self,
        *,
        tool_call: Mapping[str, Any],
        execution_scope: str,
        model_turn: int,
    ) -> tuple[PlannedOperation, BatchChildWork, PlannedGraphChild] | None:
        """Turn one model tool call into its plan entry, or refuse the turn."""

        model_tool_call_id = str(tool_call.get("id", "") or "").strip()
        tool_name = str(tool_call.get("name", "") or "").strip()
        if not model_tool_call_id or not tool_name:
            return None
        raw_arguments = tool_call.get("args")
        arguments = raw_arguments if isinstance(raw_arguments, Mapping) else {}
        identity = RuntimeToolCallIdentity.from_current(
            execution_scope=execution_scope,
            model_turn=model_turn,
            model_tool_call_id=model_tool_call_id,
        )
        if identity is None:
            # No verified run binding. The gateway would allocate an operation
            # id the plan never named, so there is nothing honest to record.
            return None

        resolution = self._policies.resolution_for(
            tool_name=tool_name,
            arguments=arguments,
        )
        if self._approval_for(
            resolution,
            tool_name=tool_name,
            arguments=arguments,
        ).may_park:
            # A capability whose dispatch can pause for a human is refused a
            # plan entry entirely, which makes the whole turn unplannable and
            # therefore serial on the pre-F6 exclusive permit. Planning it into
            # serial *segments* would also forbid the overlap, but it would put
            # a parked child under the coordinator's segment gate — where a
            # sibling waits on the admission budget while the first one waits on
            # a person, and where the suspend is settled durably as a failure.
            # Not planning it is the route this module already documents for
            # everything it cannot positively establish.
            return None
        operation = BatchOperation(
            operation_id=identity.operation_id,
            # One turn's calls were all authorized under one snapshot, so they
            # share an epoch and no epoch barrier is introduced between them.
            authorization_epoch=self._snapshot.snapshot_id,
            # The model emitted these calls together with no ordering between
            # them, which is the attestation of independence. It is only ever
            # *usable* because a declared policy must also say the capability is
            # a parallel-safe read; without one the planner serializes anyway.
            dependency_ids=(),
            resource_fingerprints=self._resource_fingerprints(resolution),
        )
        planned = (
            PlannedOperation.of(
                operation=operation,
                capability_ref=graph_capability_ref(
                    graph_capability_key(tool_name=tool_name, arguments=arguments)
                ),
            )
            if resolution is None
            else PlannedOperation.resolved(operation=operation, resolution=resolution)
        )
        child_work = BatchChildWork(
            operation_id=identity.operation_id,
            server_name=str(arguments.get("server_name", "") or "") or tool_name,
            tool_name=str(arguments.get("tool_name", "") or "") or tool_name,
            arguments=dict(arguments),
            model_tool_call_id=model_tool_call_id,
            model_turn=model_turn,
            execution_scope=execution_scope,
        )
        child = PlannedGraphChild(
            batch_id=self._batch_id(
                execution_scope=execution_scope,
                model_turn=model_turn,
            ),
            operation_id=identity.operation_id,
            model_tool_call_id=model_tool_call_id,
        )
        return (planned, child_work, child)

    def _approval_for(
        self,
        resolution: ConcurrencyPolicyResolution | None,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> ApprovalRequirement:
        """Return everything known about whether this call's dispatch can park.

        The catalog's ``approval_requirement`` and the run's are two readings of
        one fact, folded by ``narrowest`` like every other pair of F6
        declarations: the run may only make a capability more approval-bound
        than the catalog claimed, never less. A catalog that says ``NEVER`` and
        a run whose tool-use policy gates the tool resolves to the run's answer,
        which is the whole point — the catalog author could not have known.

        An undeclared capability has no resolution and therefore no catalog
        claim, which is the conservative floor. It is already unplannable for
        other reasons; answering ``UNKNOWN`` here simply says so earlier.
        """

        declared = (
            resolution.approval_requirement
            if resolution is not None
            else ApprovalRequirement.conservative()
        )
        run_requirement = GraphApprovalRequirementResolver.resolve(
            self._approvals,
            tool_name=tool_name,
            arguments=arguments,
        )
        if run_requirement is None:
            return declared
        return ApprovalRequirement.narrowest(declared, run_requirement)

    @staticmethod
    def _resource_fingerprints(
        resolution: ConcurrencyPolicyResolution | None,
    ) -> tuple[str, ...] | None:
        """Return this call's resource subjects, or ``None`` for unknown.

        ``None`` and ``()`` are different claims and the planner treats them
        differently: ``()`` attests that the call has no resource subject at
        all, while ``None`` says nobody knows, which is a serial barrier.

        A policy that declares a resource-key template is declaring that this
        capability *does* have a subject and that overlap must be decided per
        subject. The graph seam cannot render that key — the template's
        dimensions are connector-specific and its digest needs keyed material —
        so the honest answer is unknown, and the call is serialized.
        """

        policy = resolution.policy if resolution is not None else ConcurrencyPolicy()
        return None if policy.resource_key_template is not None else ()

    def _batch_id(self, *, execution_scope: str, model_turn: int) -> str:
        """Return the one batch id a given turn of a given scope may occupy.

        Derived rather than allocated, so a replayed turn converges on the same
        batch the journal already holds instead of writing a second plan for the
        same work. It is also why exactly one batch exists per turn per scope,
        which is half of why one cohort is live at a time.
        """

        digest = canonical_json_sha256(
            {
                "execution_scope": execution_scope,
                "model_turn": model_turn,
                "run_id": self._snapshot.run_id,
                "snapshot_id": self._snapshot.snapshot_id,
            }
        )
        return f"batch-{digest[:32]}"

    def _cohort_id(self, planned: PlannedGraphChild) -> str:
        """Return the identity of the set this call shares a width with.

        A cohort is one *segment* of one batch, which is the exact scope the
        durable plan decided may run together. It cannot be the batch: a batch's
        segments are ordered and carry different widths, so one width across a
        whole batch would be a width the plan never authorized.

        The coordinator admits only children of its current segment cursor, and
        a turn produces exactly one batch, so at most one cohort of an execution
        scope is live at any moment — which is what keeps the run's admitted
        width equal to the cohort's own width rather than a sum over cohorts.
        """

        segment_index = self._segment_index(planned)
        return f"{planned.batch_id}#{segment_index}"

    def _segment_index(self, planned: PlannedGraphChild) -> int:
        """Return the plan's segment for one child, or a per-child fallback.

        The fallback is deliberately *not* a shared value. If the segment cannot
        be read the child gets a cohort of its own, whose width is then bounded
        by ``planned_allowance`` — which answers serial for exactly the states
        that make the segment unreadable.
        """

        try:
            identities = self._coordinator.identities(planned.batch_id)
        except Exception:  # noqa: BLE001 - an unreadable plan is a serial plan.
            return -1
        for identity in identities:
            if identity.operation_id == planned.operation_id:
                if identity.segment_index is not None:
                    return identity.segment_index
                break
        return -1


__all__ = (
    "DeclaredApprovalRequirementSource",
    "DeclaredConcurrencyPolicySource",
    "GraphApprovalRequirementResolver",
    "GraphApprovalRequirementSource",
    "GraphBatchBounds",
    "GraphBatchMessages",
    "GraphConcurrencyPolicySource",
    "PlannedGraphChild",
    "RunBatchAdmission",
    "graph_capability_key",
    "graph_capability_ref",
)
