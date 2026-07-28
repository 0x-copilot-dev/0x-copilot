"""Worker-bound immutable run control and restart semantics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from agent_runtime.control_plane.contracts import (
    BudgetEnvelope,
    RunControlSnapshot,
    RunPolicyRevisions,
)
from agent_runtime.control_plane.context import (
    TaskPolicyProgressProjection,
    TaskPolicyRuntimeBinding,
)
from agent_runtime.control_plane.feature_modes import (
    AgentQualityFeature,
    FeatureMode,
    FeatureModeSet,
)
from agent_runtime.control_plane.model_reliability import (
    ModelReliabilityControl,
    ModelReliabilityControlSnapshot,
    ModelReliabilityDecisionReason,
    ModelReliabilityLiveConstraints,
)
from agent_runtime.control_plane.ports import (
    RunControlScopeConflict,
    RunControlSnapshotConflict,
    RunControlSnapshotWrite,
)
from agent_runtime.capabilities.task_policy import (
    RequestFingerprint,
    TaskFamily,
    TaskPolicyProfile,
    TaskPolicyRequest,
    TaskPolicyResolver,
    ToolUseController,
)
from runtime_api.schemas import AgentRunStatus, RunRecord
from runtime_worker.run_control import (
    LiveRunControlConstraints,
    RunControlAssignment,
    RunControlContext,
    RunControlPlaneBuilder,
    StableUserProfileHmac,
    VerifiedTaskPolicySignals,
)


class _SnapshotStore:
    def __init__(self) -> None:
        self.snapshot: RunControlSnapshot | None = None
        self.creations = 0

    async def get(
        self,
        *,
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
    ) -> RunControlSnapshot | None:
        del org_id
        if self.snapshot is None or self.snapshot.run_id != run_id:
            return None
        if self.snapshot.subject_fingerprint != subject_fingerprint:
            raise RunControlScopeConflict(run_id=run_id)
        return self.snapshot

    async def get_or_create(self, write: RunControlSnapshotWrite) -> RunControlSnapshot:
        self.creations += 1
        if self.snapshot is None:
            self.snapshot = write.snapshot
            return write.snapshot
        if self.snapshot.snapshot_digest != write.snapshot.snapshot_digest:
            raise RunControlSnapshotConflict(run_id=write.snapshot.run_id)
        return self.snapshot


def _run(*, created_at: datetime, user_id: str = "user-1") -> RunRecord:
    return RunRecord.model_construct(
        run_id="run-1",
        conversation_id="conversation-1",
        org_id="org-1",
        user_id=user_id,
        trace_id="trace-1",
        created_at=created_at,
    )


def _builder(
    *,
    store: _SnapshotStore,
    cutover_at: datetime,
    assignments: tuple[RunControlAssignment, ...] = (),
    live_constraints=lambda: LiveRunControlConstraints(),
    profile: str = "single_user_desktop",
) -> RunControlPlaneBuilder:
    return RunControlPlaneBuilder(
        store=store,
        deployment_profile=profile,
        subject_hmac=StableUserProfileHmac(b"x" * 32),
        assignments=assignments,
        cutover_at=cutover_at,
        live_constraints=live_constraints,
    )


def _assignment(revision: str, mode: FeatureMode) -> RunControlAssignment:
    base = RunControlAssignment.safe_active_v1()
    return base.model_copy(
        update={
            "assignment_revision": revision,
            "harness_variant_ref": f"harness://{revision}",
            "task_policy_selection_ref": f"task-policy://{revision}",
            "policy_revisions": RunPolicyRevisions.model_validate(
                {field: revision for field in RunPolicyRevisions.model_fields}
            ),
            "feature_modes": FeatureModeSet.model_validate(
                {feature.value: mode for feature in AgentQualityFeature}
            ),
        }
    )


def _assignment_with_reliability(
    *,
    revision: str,
    f10_mode: FeatureMode,
    controls: ModelReliabilityControlSnapshot,
) -> RunControlAssignment:
    base = _assignment(revision, FeatureMode.OFF)
    return RunControlAssignment.model_validate(
        {
            **base.model_dump(exclude={"feature_modes", "model_reliability_controls"}),
            "feature_modes": FeatureModeSet(f10=f10_mode),
            "model_reliability_controls": controls,
        }
    )


def test_subject_assignment_is_stable_per_verified_user_and_profile() -> None:
    hmac_assignment = StableUserProfileHmac(b"k" * 32)

    first = hmac_assignment.fingerprint(
        org_id="org-1",
        user_id="user-1",
        deployment_profile="single_user_desktop",
    )
    assert first == hmac_assignment.fingerprint(
        org_id="org-1",
        user_id="user-1",
        deployment_profile="single_user_desktop",
    )
    assert first != hmac_assignment.fingerprint(
        org_id="org-1",
        user_id="user-2",
        deployment_profile="single_user_desktop",
    )
    assert first != hmac_assignment.fingerprint(
        org_id="org-1",
        user_id="user-1",
        deployment_profile="saas_multi_tenant",
    )


def test_assignment_digest_binds_model_reliability_authority() -> None:
    base = _assignment_with_reliability(
        revision="release-v1",
        f10_mode=FeatureMode.ENFORCE,
        controls=ModelReliabilityControlSnapshot(),
    )
    released = _assignment_with_reliability(
        revision="release-v1",
        f10_mode=FeatureMode.ENFORCE,
        controls=ModelReliabilityControlSnapshot(
            same_deployment_retry=FeatureMode.ENFORCE,
        ),
    )

    assert base.digest != released.digest
    assert RunControlAssignment.safe_active_v1().model_reliability_controls.is_all_off
    assert (
        RunControlAssignment.safe_active_v1().model_reliability_controls.alternate_route
        is FeatureMode.OFF
    )
    assert (
        RunControlAssignment.safe_active_v1().model_reliability_controls.equivalent_route
        is FeatureMode.OFF
    )


@pytest.mark.parametrize(
    ("parent", "child"),
    (
        (FeatureMode.OFF, FeatureMode.SHADOW),
        (FeatureMode.OFF, FeatureMode.ENFORCE),
        (FeatureMode.SHADOW, FeatureMode.ENFORCE),
    ),
)
def test_assignment_rejects_subcontrol_broader_than_f10(
    parent: FeatureMode,
    child: FeatureMode,
) -> None:
    with pytest.raises(ValidationError, match="cannot exceed the parent F10"):
        _assignment_with_reliability(
            revision="invalid-release",
            f10_mode=parent,
            controls=ModelReliabilityControlSnapshot(
                same_deployment_retry=child,
            ),
        )


@pytest.mark.asyncio
async def test_new_run_get_or_create_is_restart_stable_and_order_independent() -> None:
    cutover = datetime.now(timezone.utc)
    run = _run(created_at=cutover + timedelta(seconds=1))
    candidates = (
        _assignment("variant-z", FeatureMode.OFF),
        _assignment("variant-a", FeatureMode.OFF),
    )
    store = _SnapshotStore()
    first_builder = _builder(
        store=store,
        cutover_at=cutover,
        assignments=candidates,
    )
    first = await first_builder.ensure_snapshot(run=run, trace_id="trace-1")

    independent = await _builder(
        store=_SnapshotStore(),
        cutover_at=cutover,
        assignments=tuple(reversed(candidates)),
    ).ensure_snapshot(run=run, trace_id="trace-independent")
    assert independent.snapshot_digest == first.snapshot_digest

    restarted_builder = _builder(
        store=store,
        cutover_at=cutover + timedelta(hours=1),
        assignments=tuple(reversed(candidates)),
    )
    restarted = await restarted_builder.ensure_snapshot(run=run, trace_id="trace-2")

    assert restarted is first
    assert restarted.snapshot_digest == first.snapshot_digest
    assert store.creations == 1


@pytest.mark.asyncio
async def test_assignment_subcontrols_are_snapshot_bound_and_live_narrowed() -> None:
    cutover = datetime.now(timezone.utc)
    assignment = _assignment_with_reliability(
        revision="f10-release-v7",
        f10_mode=FeatureMode.ENFORCE,
        controls=ModelReliabilityControlSnapshot(
            same_deployment_retry=FeatureMode.ENFORCE,
            alternate_route=FeatureMode.ENFORCE,
            equivalent_route=FeatureMode.ENFORCE,
            circuit_influence=FeatureMode.ENFORCE,
            qualification_authority_ref="qualification://f1/public-research",
            qualification_authority_revision="f1-qualification-r7",
        ),
    )
    live = LiveRunControlConstraints(
        modes={AgentQualityFeature.F10_MODEL_INVOCATION: FeatureMode.SHADOW},
        model_reliability=ModelReliabilityLiveConstraints(
            modes={
                # This attempted broadening is still capped by parent SHADOW.
                ModelReliabilityControl.SAME_DEPLOYMENT_RETRY: FeatureMode.ENFORCE,
            },
            kill_switches=frozenset({ModelReliabilityControl.ALTERNATE_ROUTE}),
        ),
    )
    builder = _builder(
        store=_SnapshotStore(),
        cutover_at=cutover,
        assignments=(assignment,),
        live_constraints=lambda: live,
    )

    snapshot = await builder.ensure_snapshot(
        run=_run(created_at=cutover + timedelta(seconds=1)),
        trace_id="trace-f10",
    )
    binding = builder.binding_for(snapshot)

    assert snapshot.schema_version == 2
    assert snapshot.model_reliability_controls == (
        assignment.model_reliability_controls
    )
    assert binding.model_reliability.effective_f10_mode is FeatureMode.SHADOW
    assert (
        binding.model_reliability.same_deployment_retry.effective_mode
        is FeatureMode.SHADOW
    )
    assert (
        binding.model_reliability.same_deployment_retry.reason
        is ModelReliabilityDecisionReason.LIVE_CONSTRAINT
    )
    assert binding.model_reliability.alternate_route.effective_mode is FeatureMode.OFF
    assert binding.model_reliability.alternate_route.kill_switch_asserted
    assert (
        binding.model_reliability.equivalent_route.effective_mode is FeatureMode.SHADOW
    )
    assert (
        binding.model_reliability.qualification_authority_revision
        == "f1-qualification-r7"
    )


@pytest.mark.asyncio
async def test_f4_prepare_uses_verified_scope_and_durable_callbacks() -> None:
    cutover = datetime.now(timezone.utc)
    run = _run(created_at=cutover + timedelta(seconds=1))
    store = _SnapshotStore()
    factory = _TaskPolicyFactory()
    loaded_scopes: list[tuple[str, str, str]] = []
    appended_scopes: list[tuple[str, str, str, object]] = []

    async def load_records(
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
    ) -> tuple[object, ...]:
        loaded_scopes.append((org_id, run_id, subject_fingerprint))
        return ({"kind": "prior"},)

    async def append_record(
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
        record: object,
    ) -> object:
        appended_scopes.append((org_id, run_id, subject_fingerprint, record))
        return record

    builder = RunControlPlaneBuilder(
        store=store,
        deployment_profile="single_user_desktop",
        subject_hmac=StableUserProfileHmac(b"x" * 32),
        assignments=(_assignment("f4-v1", FeatureMode.ENFORCE),),
        cutover_at=cutover,
        task_policy_runtime_factory=factory,
        load_task_policy_records=load_records,
        append_task_policy_record=append_record,
    )
    snapshot = await builder.ensure_snapshot(run=run, trace_id="trace-1")
    prepared = await builder.prepare_binding(run=run, snapshot=snapshot)

    assert prepared.task_policy is not None
    assert prepared.task_policy.selection.run_id == run.run_id
    assert factory.loaded == ({"kind": "prior"},)
    assert factory.signals is not None
    assert factory.signals.subject_fingerprint == snapshot.subject_fingerprint
    assert loaded_scopes == [(run.org_id, run.run_id, snapshot.subject_fingerprint)]
    assert appended_scopes == [
        (
            run.org_id,
            run.run_id,
            snapshot.subject_fingerprint,
            {"kind": "selection"},
        )
    ]
    token = RunControlContext.bind_for_run(
        prepared.control,
        task_policy=prepared.task_policy,
    )
    try:
        assert RunControlContext.task_policy() is prepared.task_policy
        assert RunControlContext.task_policy_progress() == (
            prepared.task_policy.progress()
        )
    finally:
        RunControlContext.unbind(token)


@pytest.mark.asyncio
async def test_f4_off_preserves_feature_off_without_runtime_factory() -> None:
    cutover = datetime.now(timezone.utc)
    run = _run(created_at=cutover + timedelta(seconds=1))
    builder = _builder(store=_SnapshotStore(), cutover_at=cutover)
    snapshot = await builder.ensure_snapshot(run=run, trace_id="trace-1")

    prepared = await builder.prepare_binding(run=run, snapshot=snapshot)

    assert prepared.task_policy is None
    assert (
        prepared.control.mode_for(AgentQualityFeature.F4_TOOL_USE_CONTROLLER)
        is FeatureMode.OFF
    )


@pytest.mark.asyncio
async def test_enabled_f4_without_durable_composition_fails_before_model() -> None:
    cutover = datetime.now(timezone.utc)
    run = _run(created_at=cutover + timedelta(seconds=1))
    builder = _builder(
        store=_SnapshotStore(),
        cutover_at=cutover,
        assignments=(_assignment("f4-v1", FeatureMode.ENFORCE),),
    )
    snapshot = await builder.ensure_snapshot(run=run, trace_id="trace-1")

    with pytest.raises(
        RuntimeError,
        match="enabled F4 task policy has no durable runtime composition",
    ):
        await builder.prepare_binding(run=run, snapshot=snapshot)


@pytest.mark.asyncio
async def test_old_active_run_without_snapshot_gets_legacy_safe_v1() -> None:
    cutover = datetime.now(timezone.utc)
    store = _SnapshotStore()
    builder = _builder(store=store, cutover_at=cutover)

    snapshot = await builder.ensure_snapshot(
        run=_run(created_at=cutover - timedelta(seconds=1)),
        trace_id="trace-1",
    )

    assert snapshot.assignment_revision == "legacy-safe-v1"
    assert set(snapshot.feature_modes.as_safe_mapping().values()) == {"off"}


@pytest.mark.asyncio
async def test_terminal_historical_run_is_not_rewritten() -> None:
    cutover = datetime.now(timezone.utc)
    run = _run(created_at=cutover - timedelta(seconds=1)).model_copy(
        update={"status": AgentRunStatus.COMPLETED}
    )

    with pytest.raises(
        RuntimeError,
        match="terminal historical run has no run-control snapshot",
    ):
        await _builder(
            store=_SnapshotStore(),
            cutover_at=cutover,
        ).ensure_snapshot(run=run, trace_id="trace-1")


@pytest.mark.asyncio
async def test_changed_assignment_for_same_run_is_a_hard_conflict() -> None:
    cutover = datetime.now(timezone.utc)
    run = _run(created_at=cutover + timedelta(seconds=1))
    store = _SnapshotStore()
    original = _builder(
        store=store,
        cutover_at=cutover,
        assignments=(_assignment("revision-1", FeatureMode.OFF),),
    )
    await original.ensure_snapshot(run=run, trace_id="trace-1")
    # Simulate the competing first-writer race: neither builder observed the
    # other's snapshot before trying the atomic get_or_create operation.
    store.get = lambda **_: _return_none()  # type: ignore[method-assign]
    changed = _builder(
        store=store,
        cutover_at=cutover,
        assignments=(_assignment("revision-2", FeatureMode.OFF),),
    )

    with pytest.raises(RunControlSnapshotConflict):
        await changed.ensure_snapshot(run=run, trace_id="trace-2")


async def _return_none() -> None:
    return None


class _TaskPolicyFactory:
    def __init__(self) -> None:
        self.loaded: tuple[object, ...] = ()
        self.signals: VerifiedTaskPolicySignals | None = None
        self.appended: object | None = None

    async def prepare(
        self,
        *,
        signals: VerifiedTaskPolicySignals,
        mode: FeatureMode,
        budget_envelope: BudgetEnvelope | None,
        load_records,
        append_record,
    ) -> TaskPolicyRuntimeBinding:
        del budget_envelope
        self.signals = signals
        self.loaded = tuple(await load_records())
        self.appended = await append_record({"kind": "selection"})
        profile = TaskPolicyProfile.conservative_unknown(
            revision=signals.task_policy_revision
        )
        selection = TaskPolicyResolver(
            (profile,),
            policy_revision=signals.task_policy_revision,
        ).resolve_selection(
            TaskPolicyRequest(
                run_id=signals.run_id,
                policy_revision=signals.task_policy_revision,
            )
        )
        controller = ToolUseController(profile=profile)
        projection = TaskPolicyProgressProjection(
            profile_id=profile.profile_id,
            profile_revision=profile.revision,
            task_family=TaskFamily.UNKNOWN.value,
        )
        return TaskPolicyRuntimeBinding(
            selection=selection,
            profile=profile,
            controller=controller,
            fingerprinter=RequestFingerprint(key=b"f" * 32),
            mode=mode,
            progress_projector=lambda: projection,
        )


@pytest.mark.asyncio
async def test_cross_profile_rehydration_fails_closed() -> None:
    cutover = datetime.now(timezone.utc)
    run = _run(created_at=cutover + timedelta(seconds=1))
    store = _SnapshotStore()
    await _builder(store=store, cutover_at=cutover).ensure_snapshot(
        run=run,
        trace_id="trace-1",
    )

    with pytest.raises(RunControlScopeConflict):
        await _builder(
            store=store,
            cutover_at=cutover,
            profile="saas_multi_tenant",
        ).ensure_snapshot(run=run, trace_id="trace-2")


@pytest.mark.asyncio
async def test_live_controls_only_narrow_and_context_is_read_only() -> None:
    cutover = datetime.now(timezone.utc)
    constraints = LiveRunControlConstraints(
        modes={
            AgentQualityFeature.F1_HARNESS_QUALITY: FeatureMode.SHADOW,
            AgentQualityFeature.F2_PROMPT_ASSEMBLY: FeatureMode.ENFORCE,
        },
        kill_switches=frozenset({AgentQualityFeature.F3_CAPABILITY_DISCOVERY}),
    )
    store = _SnapshotStore()
    builder = _builder(
        store=store,
        cutover_at=cutover,
        assignments=(_assignment("enforced", FeatureMode.ENFORCE),),
        live_constraints=lambda: constraints,
    )
    snapshot = await builder.ensure_snapshot(
        run=_run(created_at=cutover + timedelta(seconds=1)),
        trace_id="trace-1",
    )
    binding = builder.binding_for(snapshot)

    assert (
        binding.mode_for(AgentQualityFeature.F1_HARNESS_QUALITY) is FeatureMode.SHADOW
    )
    assert (
        binding.mode_for(AgentQualityFeature.F2_PROMPT_ASSEMBLY) is FeatureMode.ENFORCE
    )
    assert (
        binding.mode_for(AgentQualityFeature.F3_CAPABILITY_DISCOVERY) is FeatureMode.OFF
    )
    token = RunControlContext.bind_for_run(binding)
    try:
        assert RunControlContext.require_current() is binding
        with pytest.raises(ValidationError):
            binding.effective_modes.f1 = FeatureMode.ENFORCE
    finally:
        RunControlContext.unbind(token)
    assert RunControlContext.current() is None

    off_builder = _builder(
        store=_SnapshotStore(),
        cutover_at=cutover,
        assignments=(_assignment("off", FeatureMode.OFF),),
        live_constraints=lambda: LiveRunControlConstraints(
            modes={AgentQualityFeature.F1_HARNESS_QUALITY: FeatureMode.ENFORCE}
        ),
    )
    off_snapshot = await off_builder.ensure_snapshot(
        run=_run(created_at=cutover + timedelta(seconds=1)),
        trace_id="trace-1",
    )
    assert (
        off_builder.binding_for(off_snapshot).mode_for(
            AgentQualityFeature.F1_HARNESS_QUALITY
        )
        is FeatureMode.OFF
    )
