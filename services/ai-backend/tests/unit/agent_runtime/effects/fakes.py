"""Hermetic structural ports for the pure effect-domain tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from agent_runtime.effects.contracts import (
    EffectActorIdentity,
    EffectCommitCommand,
    EffectPolicySnapshot,
    EffectProposalKind,
    EffectRevisionProposal,
    EffectStageScope,
    ProposedEffect,
)
from agent_runtime.effects.errors import EffectStageIdempotencyConflict
from agent_runtime.effects.ports import StructuralEvent
from agent_runtime.surfaces_v2.entities import EffectTarget
from agent_runtime.surfaces_v2.ledger_models import (
    EffectActor,
    EffectClass,
    EffectExecutorKind,
    EffectPolicy,
)

_OPERATION_ID = "op_00000000-0000-4000-8000-000000000001"
_ARTIFACT_ID = "art_00000000-0000-4000-8000-000000000001"
_STAGE_IDS = (
    "stg_00000000-0000-4000-8000-000000000001",
    "stg_00000000-0000-4000-8000-000000000002",
    "stg_00000000-0000-4000-8000-000000000003",
    "stg_00000000-0000-4000-8000-000000000004",
)


@dataclass
class FakeLedger:
    """In-memory semantic append port, including the A4 idempotency contract."""

    events_by_stage: dict[str, list[StructuralEvent]] = field(default_factory=dict)
    bindings: dict[tuple[str, str], tuple[str, StructuralEvent]] = field(
        default_factory=dict
    )
    append_calls: int = 0

    async def list_stage_events(
        self,
        *,
        scope: EffectStageScope,
        stage_id: str,
    ) -> tuple[StructuralEvent, ...]:
        return tuple(self.events_by_stage.get(stage_id, ()))

    async def append_stage_event(
        self,
        *,
        scope: EffectStageScope,
        event_type: str,
        payload: Mapping[str, object],
        idempotency_key: str,
        request_fingerprint: str,
    ) -> StructuralEvent:
        binding_key = (scope.run_id, idempotency_key)
        existing = self.bindings.get(binding_key)
        if existing is not None:
            fingerprint, event = existing
            if fingerprint != request_fingerprint:
                raise EffectStageIdempotencyConflict()
            return event
        stage_id = payload["stage_id"]
        assert isinstance(stage_id, str)
        sequence_no = sum(len(events) for events in self.events_by_stage.values()) + 1
        event = StructuralEvent(
            run_id=scope.run_id,
            ledger_id=f"rtest·{sequence_no:03d}",
            sequence_no=sequence_no,
            event_type=event_type,
            payload=dict(payload),
            created_at=str(
                payload.get("created_at")
                or payload.get("decided_at")
                or f"2026-07-24T00:00:{sequence_no:02d}+00:00"
            ),
        )
        self.events_by_stage.setdefault(stage_id, []).append(event)
        self.bindings[binding_key] = (request_fingerprint, event)
        self.append_calls += 1
        return event


@dataclass
class FakeOutbox:
    """Command-only store; intentionally exposes no dispatch or effect method."""

    commands: dict[str, EffectCommitCommand] = field(default_factory=dict)
    enqueue_calls: int = 0

    async def enqueue_after_decision(self, command: EffectCommitCommand) -> None:
        existing = self.commands.get(command.idempotency_key)
        if existing is not None:
            if existing != command:
                raise EffectStageIdempotencyConflict()
            return
        self.commands[command.idempotency_key] = command
        self.enqueue_calls += 1


@dataclass
class FakeClock:
    tick: int = 0

    def now(self) -> str:
        self.tick += 1
        return f"2026-07-24T00:00:{self.tick:02d}+00:00"


@dataclass
class FakeStageIds:
    index: int = 0

    def new_stage_id(self) -> str:
        stage_id = _STAGE_IDS[self.index]
        self.index += 1
        return stage_id


class ExplodingEffectHandle:
    """Any accidental effect call fails the test and records the attempted method."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def _boom(self, name: str) -> None:
        self.calls.append(name)
        raise AssertionError(f"effect method {name} must never be called")

    def execute(self) -> None:
        self._boom("execute")

    def apply(self) -> None:
        self._boom("apply")

    def commit(self) -> None:
        self._boom("commit")

    def send(self) -> None:
        self._boom("send")


def scope() -> EffectStageScope:
    return EffectStageScope(run_id="run_a4_test", owner_ref="principal://users/user-1")


def user() -> EffectActorIdentity:
    return EffectActorIdentity(
        actor=EffectActor.USER,
        principal_ref="principal://users/user-1",
    )


def foreign_user() -> EffectActorIdentity:
    return EffectActorIdentity(
        actor=EffectActor.USER,
        principal_ref="principal://users/user-2",
    )


def policy_actor() -> EffectActorIdentity:
    return EffectActorIdentity(
        actor=EffectActor.POLICY,
        principal_ref="principal://policy/allow-always",
    )


def policy_snapshot(
    *,
    descriptor_known: bool = True,
    allow_always: bool = False,
    sensitive_target: bool = False,
    deployment_policy: EffectPolicy | None = None,
    organization_policy: EffectPolicy | None = None,
    grant_policy: EffectPolicy | None = None,
    capability_policy: EffectPolicy | None = None,
    user_policy: EffectPolicy | None = None,
) -> EffectPolicySnapshot:
    return EffectPolicySnapshot(
        snapshot_ref="policy://runs/a4-test/snapshot-1",
        descriptor_known=descriptor_known,
        allow_always=allow_always,
        sensitive_target=sensitive_target,
        deployment_policy=deployment_policy,
        organization_policy=organization_policy,
        grant_policy=grant_policy,
        capability_policy=capability_policy,
        user_policy=user_policy,
    )


def proposal(
    *,
    kind: EffectProposalKind = EffectProposalKind.CANONICAL_ARGUMENTS,
    executor: EffectExecutorKind = EffectExecutorKind.MCP,
    effect_class: EffectClass = EffectClass.EXTERNAL_REVERSIBLE,
    proposal_digest: str = "a" * 64,
    target_digest: str = "b" * 64,
    agent_hold: bool = False,
) -> ProposedEffect:
    target_ref = (
        "workspace-target://grant-token/path-token"
        if executor is EffectExecutorKind.WORKSPACE
        else f"{executor.value}-target://capability/target-token"
    )
    return ProposedEffect(
        operation_id=_OPERATION_ID,
        executor=executor,
        target=EffectTarget(
            executor=executor,
            capability="demo-capability",
            op="mutate",
            target_ref=target_ref,
            precondition_ref="precondition://targets/current-token",
            display_label="Demo target",
        ),
        target_digest=target_digest,
        display_target="Demo target",
        proposal_kind=kind,
        proposal_ref=f"artifact://{_ARTIFACT_ID}/revisions/1",
        proposal_digest=proposal_digest,
        proposal_media_type="application/json",
        precondition_ref="precondition://targets/current-token",
        precondition_digest="c" * 64,
        effect_class=effect_class,
        policy_snapshot_ref="policy://runs/a4-test/snapshot-1",
        agent_hold=agent_hold,
        safe_summary_ref="summary://stages/a4-test/1",
    )


def revision_from(
    proposed: ProposedEffect,
    *,
    proposal_digest: str = "d" * 64,
    target_digest: str | None = None,
    target_ref: str | None = None,
) -> EffectRevisionProposal:
    return EffectRevisionProposal(
        proposal_kind=proposed.proposal_kind,
        proposal_ref=f"artifact://{_ARTIFACT_ID}/revisions/2",
        proposal_digest=proposal_digest,
        proposal_media_type=proposed.proposal_media_type,
        target_ref=target_ref or proposed.target.target_ref,
        target_digest=target_digest or proposed.target_digest,
        display_target=proposed.display_target,
        precondition_ref=proposed.precondition_ref,
        precondition_digest=proposed.precondition_digest,
        safe_diff_ref="diff://stages/a4-test/1-2",
    )
