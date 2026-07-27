"""Worker-bound immutable run control and restart semantics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from agent_runtime.control_plane.contracts import (
    RunControlSnapshot,
    RunPolicyRevisions,
)
from agent_runtime.control_plane.feature_modes import (
    AgentQualityFeature,
    FeatureMode,
    FeatureModeSet,
)
from agent_runtime.control_plane.ports import (
    RunControlScopeConflict,
    RunControlSnapshotConflict,
    RunControlSnapshotWrite,
)
from runtime_api.schemas import AgentRunStatus, RunRecord
from runtime_worker.run_control import (
    LiveRunControlConstraints,
    RunControlAssignment,
    RunControlContext,
    RunControlPlaneBuilder,
    StableUserProfileHmac,
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
