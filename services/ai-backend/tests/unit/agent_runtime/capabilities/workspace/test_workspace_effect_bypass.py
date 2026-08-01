"""Bypass mode drives the REAL C3 composition — one lane, ledgered, bounded.

Every case here goes through ``WorkspaceGatewayBackend`` → ``OperationGateway``
→ ``WorkspaceGrantGate`` → ``WorkspaceOperationAdapter`` → ``EffectStager``,
reusing the harness the C3 suite already built. Nothing asserts against an
injected double of the thing under test: the ledger and outbox are the fakes
the whole suite uses, and what is asserted is what the composition put in them.

Three properties, in order of how badly they must not break:

1. the LANE — bypass appends the same ``effect.staged`` /
   ``effect.projection_bound`` / ``effect.decision`` rows and enqueues exactly
   one C2 commit. It never writes, never reaches the base, never adds a second
   path to the disk.
2. the BOUND — an ungranted path, a read-only grant, and a destructive op are
   all unaffected by bypass.
3. the TIERS — master off refuses a selection instead of honouring it.
"""

from __future__ import annotations

import pytest

from agent_runtime.capabilities.operations.context import OperationContext
from agent_runtime.execution.filesystem_bypass import (
    MANUAL_FILESYSTEM_BYPASS,
    FilesystemBypassBound,
    FilesystemBypassDecision,
    FilesystemBypassMode,
    FilesystemBypassResolver,
    FilesystemBypassSelection,
    FilesystemBypassSource,
    FilesystemBypassTarget,
)
from agent_runtime.surfaces_v2.entities import OperationRequest
from agent_runtime.surfaces_v2.ledger_ids import OperationArgsRefCodec
from agent_runtime.surfaces_v2.ledger_models import (
    EffectClass,
    EffectPolicy,
    LedgerEventType,
    Producer,
)

from tests.unit.agent_runtime.capabilities.workspace.test_workspace_effect_gateway import (  # noqa: E501
    MOUNT,
    RUN_ID,
    _grant,
    _harness,
)

BYPASS_ON = FilesystemBypassDecision(
    master_enabled=True,
    mode=FilesystemBypassMode.BYPASS,
    source=FilesystemBypassSource.MESSAGE,
)


_OPERATION_ID = "op_00000000-0000-4000-8000-000000000000"


def _stage_request() -> OperationRequest:
    """A minimal request for the snapshot seam — only ids are read from it."""

    return OperationRequest(
        operation_id=_OPERATION_ID,
        run_id=RUN_ID,
        producer=Producer.MODEL,
        capability="workspace",
        op="write",
        canonical_args_ref=OperationArgsRefCodec.format(_OPERATION_ID),
        args_digest="0" * 64,
        requested_at="2026-07-31T00:00:00+00:00",
    )


def _stage_events(harness) -> list[str]:  # noqa: ANN001 — test-local harness type
    stages = list(harness.ledger.events_by_stage.values())
    assert len(stages) == 1, "expected exactly one stage per operation"
    return [event.event_type for event in stages[0]]


def _staged_payload(harness) -> dict:  # noqa: ANN001 — test-local harness type
    events = next(iter(harness.ledger.events_by_stage.values()))
    staged = next(
        event
        for event in events
        if event.event_type == LedgerEventType.EFFECT_STAGED.value
    )
    return dict(staged.payload)


# ---------------------------------------------------------------------------
# 1. The lane
# ---------------------------------------------------------------------------


async def test_bypass_removes_the_pause_and_keeps_every_ledger_row() -> None:
    """The point of the feature, and the proof it did not become a shortcut.

    Manual and bypass are run through the identical composition. Manual stops
    after ``projection_bound`` with nothing queued. Bypass appends ONE more row
    — an ``effect.decision`` authored by the POLICY actor — and enqueues
    exactly one C2 commit command. No extra write path appears in either.
    """

    path = f"/workspace/{MOUNT}/report.csv"

    manual = _harness()
    token = manual.bind()
    try:
        await manual.backend.awrite(path, "account,total\nAcme,10\n")
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    assert _stage_events(manual) == ["effect.staged", "effect.projection_bound"]
    assert manual.outbox.enqueue_calls == 0

    bypassed = _harness(bypass=BYPASS_ON)
    token = bypassed.bind()
    try:
        await bypassed.backend.awrite(path, "account,total\nAcme,10\n")
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    assert _stage_events(bypassed) == [
        "effect.staged",
        "effect.projection_bound",
        LedgerEventType.EFFECT_DECISION_RECORDED.value,
    ]
    # The ledger is NOT skipped: the approval is recorded, attributed, and the
    # commit is queued through the same outbox a human click uses.
    decision = next(
        event
        for event in next(iter(bypassed.ledger.events_by_stage.values()))
        if event.event_type == LedgerEventType.EFFECT_DECISION_RECORDED.value
    )
    assert decision.payload["decision"] == "approve"
    assert decision.payload["actor"] == "policy"
    assert bypassed.outbox.enqueue_calls == 1

    # And still no second write lane: the host base was never touched, in
    # either mode.
    assert manual.base.mutation_calls == []
    assert bypassed.base.mutation_calls == []


async def test_bypass_records_auto_on_the_staged_row_not_a_silent_skip() -> None:
    """The staged row itself carries the AUTO posture and its reason.

    A run history that showed ``policy=ask`` followed by a policy approval
    would be unreadable. The stage says up front that it was eligible to skip
    the pause and why.
    """

    harness = _harness(bypass=BYPASS_ON)
    token = harness.bind()
    try:
        await harness.backend.awrite(f"/workspace/{MOUNT}/a.csv", "x\n")
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    assert _staged_payload(harness)["policy"] == "auto"


async def test_manual_run_stays_byte_identical_to_the_pre_bypass_behaviour() -> None:
    """The default decision composes exactly as before bypass existed."""

    harness = _harness(bypass=MANUAL_FILESYSTEM_BYPASS)
    token = harness.bind()
    try:
        result = await harness.backend.awrite(f"/workspace/{MOUNT}/a.csv", "x\n")
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    assert (
        result.error == "Workspace change staged for review; the host was not modified."
    )
    assert _staged_payload(harness)["policy"] == "ask"
    assert harness.outbox.enqueue_calls == 0


# ---------------------------------------------------------------------------
# 2. The bound — bypass may never widen what was granted
# ---------------------------------------------------------------------------


async def test_an_ungranted_path_still_asks_under_bypass() -> None:
    """The bound that makes this feature safe to ship.

    ``expose_grant=False`` is a path with no grant at all — the ad-hoc case.
    Under bypass it must be refused exactly as it is under manual: no stage, no
    decision, no commit. One approval click must never become blanket write
    access to the machine.
    """

    harness = _harness(expose_grant=False, bypass=BYPASS_ON)
    token = harness.bind()
    try:
        result = await harness.backend.awrite(f"/workspace/{MOUNT}/a.csv", "x\n")
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    assert result.error == "Workspace access is required; no host change was made."
    assert harness.ledger.events_by_stage == {}
    assert harness.outbox.enqueue_calls == 0
    assert harness.base.mutation_calls == []


async def test_a_read_only_grant_still_asks_under_bypass() -> None:
    """A folder attached WITHOUT write permission is not a bypass target."""

    harness = _harness(grant=_grant(mode="read_only"), bypass=BYPASS_ON)
    token = harness.bind()
    try:
        result = await harness.backend.awrite(f"/workspace/{MOUNT}/a.csv", "x\n")
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    assert result.error == "This workspace grant is read-only; no host change was made."
    assert harness.ledger.events_by_stage == {}
    assert harness.outbox.enqueue_calls == 0


async def test_a_delete_still_requires_a_human_under_bypass() -> None:
    """Destructive is not reversible, so the bound refuses it in every mode.

    This one is load-bearing at the adapter, not at the gate: a delete inside a
    fully-writable granted folder passes every gate. It is the reversibility
    fact in :class:`FilesystemBypassTarget` that keeps the pause.
    """

    path = f"/workspace/{MOUNT}/existing.md"
    harness = _harness(files={path: b"old body\n"}, bypass=BYPASS_ON)
    token = harness.bind()
    try:
        await harness.backend.adelete(path)
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    assert _stage_events(harness) == ["effect.staged", "effect.projection_bound"]
    assert _staged_payload(harness)["policy"] == "require"
    assert harness.outbox.enqueue_calls == 0
    assert harness.base.mutation_calls == []


async def test_a_blocking_user_policy_still_wins_over_bypass() -> None:
    """Bypass competes in the fold; it never overrides a stricter policy.

    An operator who set Write actions to Block has said the agent may not
    write at all. Bypass is an approval-pause preference, not an authority
    grant, so the block must survive it.
    """

    harness = _harness(bypass=BYPASS_ON)
    token = harness.bind(write_policy="block")
    try:
        await harness.backend.awrite(f"/workspace/{MOUNT}/a.csv", "x\n")
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    # ``EffectStagePolicyResolver`` folds to BLOCK and ``EffectStager.stage``
    # refuses before appending anything, so a blocked write under bypass leaves
    # no stage, no decision, and nothing queued.
    assert harness.ledger.events_by_stage == {}
    assert harness.outbox.enqueue_calls == 0
    assert harness.base.mutation_calls == []


@pytest.mark.parametrize(
    ("grant_kwargs", "reason"),
    [
        ({"mode": "read_only"}, "a folder attached without write permission"),
        ({"status": "revoked"}, "a grant the user has since revoked"),
    ],
)
async def test_the_snapshot_itself_refuses_a_non_writable_grant(
    grant_kwargs: dict[str, str],
    reason: str,
) -> None:
    """The bound at the layer where it is the ONLY guard.

    The grant gate refuses these before the adapter is reached, and A4 refuses
    them again after — which is why the end-to-end cases above still pass with
    the bound deleted. This drives ``_policy_snapshot`` directly, the one place
    where nothing else is watching, so weakening
    :class:`FilesystemBypassBound` fails here instead of silently relying on a
    neighbour that might be refactored.
    """

    harness = _harness(bypass=BYPASS_ON)
    token = harness.bind()
    try:
        snapshot = harness.adapter._policy_snapshot(
            request=_stage_request(),
            effect_class=EffectClass.EXTERNAL_REVERSIBLE,
            grant=_grant(**grant_kwargs),
        )
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    assert snapshot.allow_always is False, reason
    assert snapshot.user_policy is EffectPolicy.ASK


async def test_the_snapshot_grants_allow_always_for_a_writable_reversible_target() -> (
    None
):
    """The positive half of the same seam, so the refusals above mean something."""

    harness = _harness(bypass=BYPASS_ON)
    token = harness.bind()
    try:
        snapshot = harness.adapter._policy_snapshot(
            request=_stage_request(),
            effect_class=EffectClass.EXTERNAL_REVERSIBLE,
            grant=_grant(),
        )
        destructive = harness.adapter._policy_snapshot(
            request=_stage_request(),
            effect_class=EffectClass.EXTERNAL_DESTRUCTIVE,
            grant=_grant(),
        )
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    assert snapshot.allow_always is True
    assert snapshot.user_policy is EffectPolicy.AUTO
    # Same grant, same bypass, destructive op: still no allow_always.
    assert destructive.allow_always is False


@pytest.mark.parametrize(
    ("granted", "writable", "reversible", "expected"),
    [
        (True, True, True, True),
        (False, True, True, False),
        (True, False, True, False),
        (True, True, False, False),
        (False, False, False, False),
    ],
)
def test_the_bound_is_a_conjunction_of_all_three_facts(
    granted: bool,
    writable: bool,
    reversible: bool,
    expected: bool,
) -> None:
    """Exhaustive over the rule itself, so weakening any clause fails here."""

    assert (
        FilesystemBypassBound.permits(
            BYPASS_ON,
            FilesystemBypassTarget(
                granted=granted,
                writable=writable,
                reversible=reversible,
            ),
        )
        is expected
    )


def test_a_manual_decision_never_permits_any_target() -> None:
    assert not FilesystemBypassBound.permits(
        MANUAL_FILESYSTEM_BYPASS,
        FilesystemBypassTarget(granted=True, writable=True, reversible=True),
    )


# ---------------------------------------------------------------------------
# 3. The tiers
# ---------------------------------------------------------------------------


def test_master_off_refuses_a_selection_instead_of_honouring_it() -> None:
    """Tier 1 is not advisory. A client cannot opt itself in."""

    for selection in (
        FilesystemBypassSelection(message=FilesystemBypassMode.BYPASS),
        FilesystemBypassSelection(run=FilesystemBypassMode.BYPASS),
        FilesystemBypassSelection(
            run=FilesystemBypassMode.BYPASS,
            message=FilesystemBypassMode.BYPASS,
        ),
    ):
        decision = FilesystemBypassResolver.resolve(
            master_enabled=False,
            selection=selection,
        )
        assert decision.mode is FilesystemBypassMode.MANUAL
        assert decision.offered is False
        # Refused, and visibly so — a run record can show that a control was
        # offered by a stale client rather than silently dropping it.
        assert decision.source is FilesystemBypassSource.MASTER_OFF


def test_master_off_with_no_selection_is_not_reported_as_a_refusal() -> None:
    decision = FilesystemBypassResolver.resolve(master_enabled=False)
    assert decision.source is FilesystemBypassSource.MASTER
    assert decision.mode is FilesystemBypassMode.MANUAL


def test_master_on_alone_does_not_turn_bypass_on() -> None:
    """Flipping the Settings switch makes the control available, nothing more."""

    decision = FilesystemBypassResolver.resolve(master_enabled=True)
    assert decision.offered is True
    assert decision.mode is FilesystemBypassMode.MANUAL
    assert decision.skips_approval_pause is False


@pytest.mark.parametrize(
    ("run", "message", "expected"),
    [
        # message wins over run, in BOTH directions — the reason precedence is
        # message > run and not the other way round is that a per-message
        # Manual has to be able to turn a sticky run bypass back off.
        (FilesystemBypassMode.BYPASS, FilesystemBypassMode.MANUAL, "manual"),
        (FilesystemBypassMode.MANUAL, FilesystemBypassMode.BYPASS, "bypass"),
        (None, FilesystemBypassMode.BYPASS, "bypass"),
        (FilesystemBypassMode.BYPASS, None, "bypass"),
        (FilesystemBypassMode.MANUAL, None, "manual"),
    ],
)
def test_message_beats_run_beats_master(
    run: FilesystemBypassMode | None,
    message: FilesystemBypassMode | None,
    expected: str,
) -> None:
    decision = FilesystemBypassResolver.resolve(
        master_enabled=True,
        selection=FilesystemBypassSelection(run=run, message=message),
    )
    assert decision.mode.value == expected
    assert decision.source is (
        FilesystemBypassSource.MESSAGE
        if message is not None
        else FilesystemBypassSource.RUN
    )


async def test_a_master_off_decision_reaching_the_adapter_still_asks() -> None:
    """Defence in depth: even if a refused decision were mis-plumbed.

    ``FilesystemBypassResolver`` already collapses a master-off selection to
    manual, so this state should be unreachable. Constructing it by hand and
    driving the real adapter proves the staging seam re-reads ``mode`` rather
    than trusting that somebody upstream did the check.
    """

    refused = FilesystemBypassDecision(
        master_enabled=False,
        mode=FilesystemBypassMode.MANUAL,
        source=FilesystemBypassSource.MASTER_OFF,
    )
    harness = _harness(bypass=refused)
    token = harness.bind()
    try:
        await harness.backend.awrite(f"/workspace/{MOUNT}/a.csv", "x\n")
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    assert _stage_events(harness) == ["effect.staged", "effect.projection_bound"]
    assert harness.outbox.enqueue_calls == 0


async def test_the_commit_command_names_the_same_run_and_stage() -> None:
    """The queued command is the ordinary one, not a bypass-specific shape."""

    harness = _harness(bypass=BYPASS_ON)
    token = harness.bind()
    try:
        await harness.backend.awrite(f"/workspace/{MOUNT}/a.csv", "x\n")
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    stage_id = next(iter(harness.ledger.events_by_stage))
    (command,) = harness.outbox.commands.values()
    assert command.run_id == RUN_ID
    assert command.stage_id == stage_id
