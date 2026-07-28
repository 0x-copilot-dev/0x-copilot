"""Worker-owned binding for the immutable run quality control plane.

The queue command is not an authority source.  Callers construct bindings only
after loading and verifying the persisted ``RunRecord``.  The bound value is a
frozen contract and exposes no mutation methods; live controls can only narrow
the snapshot's feature modes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from typing import Protocol

from pydantic import Field

from agent_runtime.control_plane.contracts import (
    BudgetEnvelope,
    RunControlSnapshot,
    RunPolicyRevisions,
)
from agent_runtime.control_plane.context import (
    RunControlBinding,
    RunControlContext,
    TaskPolicyRuntimeBinding,
)
from agent_runtime.control_plane.feature_modes import (
    AgentQualityFeature,
    FeatureMode,
    FeatureModeResolver,
    FeatureModeSet,
)
from agent_runtime.control_plane.ports import (
    RunControlSnapshotStorePort,
    RunControlSnapshotWrite,
)
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256
from runtime_api.schemas import AgentRunStatus, RunRecord


_ASSIGNMENT_HMAC_ENV = "ENTERPRISE_AUTH_SECRET"
_DEVELOPMENT_ASSIGNMENT_KEY = b"dev-run-control-assignment-key-v1"
_LEGACY_REVISION = "legacy-safe-v1"
_ACTIVE_REVISION = "active-safe-v1"
_ACTIVE_RUN_STATUSES = frozenset(
    {
        AgentRunStatus.QUEUED,
        AgentRunStatus.RUNNING,
        AgentRunStatus.WAITING_FOR_APPROVAL,
    }
)


class RunControlAssignment(RuntimeContract):
    """One ordered-independent candidate assignment for a verified subject."""

    harness_variant_ref: str
    task_policy_selection_ref: str
    policy_revisions: RunPolicyRevisions
    feature_modes: FeatureModeSet = FeatureModeSet()
    budget_envelope_ref: str
    assignment_revision: str

    @property
    def digest(self) -> str:
        """Digest the complete deployment-owned variant definition."""

        return canonical_json_sha256(self.model_dump(mode="json"))

    @classmethod
    def safe_active_v1(cls) -> "RunControlAssignment":
        """Return the current-path assignment with every new feature dark."""

        return cls._safe(_ACTIVE_REVISION)

    @classmethod
    def legacy_safe_v1(cls) -> "RunControlAssignment":
        """Return the migration assignment for pre-cutover active runs."""

        return cls._safe(_LEGACY_REVISION)

    @classmethod
    def _safe(cls, revision: str) -> "RunControlAssignment":
        policy_revisions = RunPolicyRevisions.model_validate(
            {field: revision for field in RunPolicyRevisions.model_fields}
        )
        budget_envelope = BudgetEnvelope.create(
            budget_envelope_id=revision,
            revision=revision,
        )
        return cls(
            harness_variant_ref=f"harness://{revision}",
            task_policy_selection_ref=f"task-policy://{revision}",
            policy_revisions=policy_revisions,
            feature_modes=FeatureModeSet(),
            budget_envelope_ref=budget_envelope.revision_ref,
            assignment_revision=revision,
        )


class RunControlAssignmentAllocation(RuntimeContract):
    """One verified release allocation resolved to a full control assignment."""

    assignment: RunControlAssignment
    allocation_basis_points: int = Field(ge=0, le=10_000)
    release_assignment_revision: str = Field(min_length=1, max_length=160)


class StableUserProfileHmac:
    """Opaque stable assignment key derived only from verified user/profile facts.

    Installation and device identifiers are deliberately absent.  Production
    reuses the already-required enterprise auth HMAC material; development uses
    a stable repository-wide sentinel so restarts remain deterministic.
    """

    def __init__(self, key: bytes) -> None:
        if len(key) < 16:
            raise ValueError("run-control assignment HMAC key is too short")
        self._key = bytes(key)

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "StableUserProfileHmac":
        env = environment if environment is not None else os.environ
        raw = (env.get(_ASSIGNMENT_HMAC_ENV) or "").strip()
        runtime_environment = (env.get("RUNTIME_ENVIRONMENT") or "development").strip()
        if not raw:
            if runtime_environment.lower() == "production":
                raise RuntimeError("stable run-control assignment HMAC is unavailable")
            return cls(_DEVELOPMENT_ASSIGNMENT_KEY)
        # Desktop provisions this value as hex; hosted deployments may use an
        # arbitrary high-entropy string.  Both representations remain stable.
        try:
            decoded = bytes.fromhex(raw)
        except ValueError:
            decoded = raw.encode("utf-8")
        return cls(decoded)

    def fingerprint(
        self,
        *,
        org_id: str,
        user_id: str,
        deployment_profile: str,
    ) -> str:
        """Return a protected, domain-separated subject fingerprint."""

        payload = json.dumps(
            {
                "deployment_profile": deployment_profile,
                "org_id": org_id,
                "purpose": "run-control-assignment-v1",
                "user_id": user_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hmac.new(self._key, payload, hashlib.sha256).hexdigest()


class LiveRunControlConstraints(RuntimeContract):
    """Trusted current controls evaluated at each worker bind/resume boundary."""

    modes: Mapping[AgentQualityFeature, object] = {}
    kill_switches: frozenset[AgentQualityFeature] = frozenset()


LiveConstraintsProvider = Callable[[], LiveRunControlConstraints]

TaskPolicyRecordLoader = Callable[
    [str, str, str],
    Awaitable[Sequence[object]],
]
TaskPolicyRecordAppender = Callable[
    [str, str, str, object],
    Awaitable[object],
]
BudgetEnvelopeLoader = Callable[[str], Awaitable[BudgetEnvelope | None]]


class VerifiedTaskPolicySignals(RuntimeContract):
    """Persisted server-owned facts available to F4 before model dispatch."""

    run_id: str = Field(min_length=1, max_length=160)
    conversation_id: str = Field(min_length=1, max_length=160)
    org_id: str = Field(min_length=1, max_length=160)
    user_id: str = Field(min_length=1, max_length=160)
    snapshot_id: str = Field(min_length=1, max_length=160)
    task_policy_selection_ref: str = Field(min_length=1, max_length=256)
    task_policy_revision: str = Field(min_length=1, max_length=256)
    subject_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_declared_tool_call_limit: int | None = Field(default=None, ge=1, le=100)


class TaskPolicyRuntimeFactoryPort(Protocol):
    """Domain-owned F4 factory; persistence remains the canonical run journal."""

    async def prepare(
        self,
        *,
        signals: VerifiedTaskPolicySignals,
        mode: FeatureMode,
        budget_envelope: BudgetEnvelope | None,
        load_records: Callable[[], Awaitable[Sequence[object]]],
        append_record: Callable[[object], Awaitable[object]],
    ) -> TaskPolicyRuntimeBinding: ...


@dataclass(frozen=True, slots=True)
class PreparedRunControl:
    """Effective release binding plus the optional replayed F4 controller."""

    control: RunControlBinding
    task_policy: TaskPolicyRuntimeBinding | None


class RunControlPlaneBuilder:
    """Get-or-create and rehydrate a run snapshot at the verified worker seam."""

    def __init__(
        self,
        *,
        store: RunControlSnapshotStorePort,
        deployment_profile: str,
        subject_hmac: StableUserProfileHmac,
        assignments: Sequence[RunControlAssignment] = (),
        allocations: Sequence[RunControlAssignmentAllocation] = (),
        cutover_at: datetime | None = None,
        live_constraints: LiveConstraintsProvider | None = None,
        task_policy_runtime_factory: TaskPolicyRuntimeFactoryPort | None = None,
        load_task_policy_records: TaskPolicyRecordLoader | None = None,
        append_task_policy_record: TaskPolicyRecordAppender | None = None,
        load_budget_envelope: BudgetEnvelopeLoader | None = None,
    ) -> None:
        if not deployment_profile.strip():
            raise ValueError("deployment_profile must be non-empty")
        if assignments and allocations:
            raise ValueError("assignments and allocations are mutually exclusive")
        resolved_allocations = tuple(allocations)
        if resolved_allocations:
            if (
                sum(item.allocation_basis_points for item in resolved_allocations)
                != 10_000
            ):
                raise ValueError(
                    "run-control allocations must total 10000 basis points"
                )
            allocation_refs = [
                item.assignment.harness_variant_ref for item in resolved_allocations
            ]
            if len(allocation_refs) != len(set(allocation_refs)):
                raise ValueError("run-control allocation variant refs must be unique")
        resolved_assignments = (
            tuple(item.assignment for item in resolved_allocations)
            or tuple(assignments)
            or (RunControlAssignment.safe_active_v1(),)
        )
        revisions = [item.assignment_revision for item in resolved_assignments]
        if len(revisions) != len(set(revisions)):
            raise ValueError("run-control assignment revisions must be unique")
        self._store = store
        self._deployment_profile = deployment_profile.strip()
        self._subject_hmac = subject_hmac
        # Configuration input order cannot change an assignment or digest.
        self._assignments = tuple(
            sorted(resolved_assignments, key=lambda item: item.assignment_revision)
        )
        self._allocations = tuple(
            sorted(
                resolved_allocations,
                key=lambda item: item.assignment.harness_variant_ref,
            )
        )
        self._cutover_at = cutover_at or datetime.now(timezone.utc)
        if self._cutover_at.tzinfo is None or self._cutover_at.utcoffset() is None:
            raise ValueError("cutover_at must be timezone-aware")
        self._live_constraints = live_constraints or LiveRunControlConstraints
        if (load_task_policy_records is None) != (append_task_policy_record is None):
            raise ValueError(
                "task-policy record load and append callbacks must be provided together"
            )
        if task_policy_runtime_factory is not None and (
            load_task_policy_records is None or append_task_policy_record is None
        ):
            raise ValueError(
                "task-policy runtime factory requires durable journal callbacks"
            )
        self._task_policy_runtime_factory = task_policy_runtime_factory
        self._load_task_policy_records = load_task_policy_records
        self._append_task_policy_record = append_task_policy_record
        self._load_budget_envelope = load_budget_envelope

    def install_task_policy_runtime(
        self,
        *,
        factory: TaskPolicyRuntimeFactoryPort,
        load_records: TaskPolicyRecordLoader,
        append_record: TaskPolicyRecordAppender,
        load_budget_envelope: BudgetEnvelopeLoader | None = None,
    ) -> None:
        """Install the reviewed startup composition before this builder is shared."""

        if (
            self._task_policy_runtime_factory is not None
            or self._load_task_policy_records is not None
            or self._append_task_policy_record is not None
        ):
            raise RuntimeError("task-policy runtime composition is already installed")
        self._task_policy_runtime_factory = factory
        self._load_task_policy_records = load_records
        self._append_task_policy_record = append_record
        self._load_budget_envelope = load_budget_envelope

    async def ensure_snapshot(
        self,
        *,
        run: RunRecord,
        trace_id: str,
    ) -> RunControlSnapshot:
        """Return the one durable snapshot for an already-verified run."""

        subject_fingerprint = self._subject_hmac.fingerprint(
            org_id=run.org_id,
            user_id=run.user_id,
            deployment_profile=self._deployment_profile,
        )
        existing = await self._store.get(
            org_id=run.org_id,
            run_id=run.run_id,
            subject_fingerprint=subject_fingerprint,
        )
        if existing is not None:
            self._verify_scope(run=run, snapshot=existing)
            return existing

        if run.status not in _ACTIVE_RUN_STATUSES:
            raise RuntimeError("terminal historical run has no run-control snapshot")
        assignment = (
            RunControlAssignment.legacy_safe_v1()
            if run.created_at < self._cutover_at
            else self._assignment_for(subject_fingerprint)
        )
        candidate = RunControlSnapshot.create(
            run_id=run.run_id,
            conversation_id=run.conversation_id,
            subject_fingerprint=subject_fingerprint,
            deployment_profile=self._deployment_profile,
            harness_variant_ref=assignment.harness_variant_ref,
            task_policy_selection_ref=assignment.task_policy_selection_ref,
            policy_revisions=assignment.policy_revisions,
            feature_modes=assignment.feature_modes,
            budget_envelope_ref=assignment.budget_envelope_ref,
            assignment_revision=assignment.assignment_revision,
        )
        persisted = await self._store.get_or_create(
            RunControlSnapshotWrite(
                org_id=run.org_id,
                trace_id=trace_id,
                snapshot=candidate,
            )
        )
        self._verify_scope(run=run, snapshot=persisted)
        return persisted

    def binding_for(self, snapshot: RunControlSnapshot) -> RunControlBinding:
        """Apply current emergency constraints without mutating the snapshot."""

        live = self._live_constraints()
        resolver = FeatureModeResolver()
        decisions = tuple(
            resolver.apply_live_constraint(
                feature=feature,
                snapshot_mode=snapshot.feature_modes.mode_for(feature),
                raw_live_mode=live.modes.get(feature),
                kill_switch_asserted=feature in live.kill_switches,
            )
            for feature in AgentQualityFeature
        )
        effective_modes = FeatureModeSet.model_validate(
            {decision.feature.value: decision.effective_mode for decision in decisions}
        )
        return RunControlBinding(
            snapshot=snapshot,
            effective_modes=effective_modes,
            decisions=decisions,
        )

    async def prepare_binding(
        self,
        *,
        run: RunRecord,
        snapshot: RunControlSnapshot,
    ) -> PreparedRunControl:
        """Replay and bind F4 from verified persisted facts before model use."""

        self._verify_scope(run=run, snapshot=snapshot)
        control = self.binding_for(snapshot)
        mode = control.mode_for(AgentQualityFeature.F4_TOOL_USE_CONTROLLER)
        if mode is FeatureMode.OFF:
            return PreparedRunControl(control=control, task_policy=None)

        factory = self._task_policy_runtime_factory
        loader = self._load_task_policy_records
        appender = self._append_task_policy_record
        if factory is None or loader is None or appender is None:
            raise RuntimeError(
                "enabled F4 task policy has no durable runtime composition"
            )

        async def load_records() -> Sequence[object]:
            return await loader(
                run.org_id,
                run.run_id,
                snapshot.subject_fingerprint,
            )

        async def append_record(record: object) -> object:
            return await appender(
                run.org_id,
                run.run_id,
                snapshot.subject_fingerprint,
                record,
            )

        envelope = (
            await self._load_budget_envelope(snapshot.budget_envelope_ref)
            if self._load_budget_envelope is not None
            else None
        )
        task_policy = await factory.prepare(
            signals=VerifiedTaskPolicySignals(
                run_id=run.run_id,
                conversation_id=run.conversation_id,
                org_id=run.org_id,
                user_id=run.user_id,
                snapshot_id=snapshot.snapshot_id,
                task_policy_selection_ref=snapshot.task_policy_selection_ref,
                task_policy_revision=snapshot.policy_revisions.tool_controller,
                subject_fingerprint=snapshot.subject_fingerprint,
                model_declared_tool_call_limit=self._model_tool_call_limit(run),
            ),
            mode=mode,
            budget_envelope=envelope,
            load_records=load_records,
            append_record=append_record,
        )
        if task_policy.selection.run_id != run.run_id:
            raise RuntimeError("F4 selection does not match the verified run")
        if task_policy.selection.profile_revision != (
            snapshot.policy_revisions.tool_controller
        ):
            raise RuntimeError("F4 selection revision does not match the run snapshot")
        if task_policy.profile.profile_id != task_policy.selection.profile_id:
            raise RuntimeError("F4 selected profile identity mismatch")
        if task_policy.profile.revision != task_policy.selection.profile_revision:
            raise RuntimeError("F4 selected profile revision mismatch")
        if task_policy.mode is not mode:
            raise RuntimeError("F4 runtime mode does not match effective run control")
        return PreparedRunControl(control=control, task_policy=task_policy)

    @staticmethod
    def _model_tool_call_limit(run: RunRecord) -> int | None:
        runtime_context = getattr(run, "runtime_context", None)
        model_profile = getattr(runtime_context, "model_profile", None)
        limit = getattr(model_profile, "tool_call_budget", None)
        return limit if isinstance(limit, int) and not isinstance(limit, bool) else None

    def _assignment_for(self, subject_fingerprint: str) -> RunControlAssignment:
        if self._allocations:
            bucket = int(subject_fingerprint, 16) % 10_000
            upper_bound = 0
            for allocation in self._allocations:
                upper_bound += allocation.allocation_basis_points
                if bucket < upper_bound:
                    return allocation.assignment.model_copy(
                        update={
                            "assignment_revision": (
                                allocation.release_assignment_revision
                            )
                        }
                    )
            raise RuntimeError("run-control allocations do not cover assignment bucket")
        index = int(subject_fingerprint, 16) % len(self._assignments)
        return self._assignments[index]

    def _verify_scope(
        self,
        *,
        run: RunRecord,
        snapshot: RunControlSnapshot,
    ) -> None:
        if (
            snapshot.run_id != run.run_id
            or snapshot.conversation_id != run.conversation_id
            or snapshot.deployment_profile != self._deployment_profile
        ):
            raise RuntimeError("persisted run-control snapshot scope mismatch")


__all__ = [
    "LiveRunControlConstraints",
    "PreparedRunControl",
    "RunControlAssignment",
    "RunControlAssignmentAllocation",
    "RunControlBinding",
    "RunControlContext",
    "RunControlPlaneBuilder",
    "StableUserProfileHmac",
    "BudgetEnvelopeLoader",
    "TaskPolicyRecordAppender",
    "TaskPolicyRecordLoader",
    "TaskPolicyRuntimeFactoryPort",
    "VerifiedTaskPolicySignals",
]
