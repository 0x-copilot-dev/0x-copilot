"""The bypass master switch is folded server-side, at run-create, once.

Drives a REAL ``create_run`` through the BYOK coordinator harness (in-memory
store, persisted workspace defaults, a genuinely enqueued run command) and reads
the decision off the sealed ``AgentRuntimeContext`` the worker will later claim.
Nothing here injects the resolver: the assertion is on what the coordinator
actually put on the run.

The property that matters: tier 1 lives in the workspace-defaults row, so a
client that sends a bypass selection while the Settings switch is off gets
``manual`` regardless of what it asked for.
"""

from __future__ import annotations

from agent_runtime.execution.filesystem_bypass import (
    FilesystemBypassMode,
    FilesystemBypassResolver,
    FilesystemBypassSelection,
    FilesystemBypassSource,
)
from runtime_api.schemas import (
    CreateRunRequest,
    WorkspaceBehaviorOverrides,
    WorkspaceDefaultsRecord,
)
from tests.unit.agent_runtime.api.test_run_coordinator_byok import (
    _ORG_ID,
    _USER_ID,
    ByokCoordinatorMixin,
)


async def _seed_master_switch(store, *, enabled: bool) -> None:  # noqa: ANN001
    await store.upsert_workspace_defaults(
        record=WorkspaceDefaultsRecord(
            org_id=_ORG_ID,
            behavior_overrides=WorkspaceBehaviorOverrides(
                filesystem_bypass_enabled=enabled
            ),
        )
    )


def _run_request(
    conversation_id: str,
    *,
    selection: FilesystemBypassSelection | None = None,
) -> CreateRunRequest:
    kwargs: dict[str, object] = {
        "conversation_id": conversation_id,
        "org_id": _ORG_ID,
        "user_id": _USER_ID,
        "user_input": "hello",
        "model": {"provider": "openai", "model_name": "gpt-5.4-mini"},
    }
    if selection is not None:
        kwargs["filesystem_bypass"] = selection
    return CreateRunRequest(**kwargs)


def test_the_master_switch_defaults_to_offering_the_control() -> None:
    """The product decision, asserted on the contract rather than on prose.

    Flipped from False once the pill began deciding whether a host write pauses.
    While it decided nothing observable, hiding it cost nothing; now, False would
    mean every write in a folder the user explicitly attached as writable asks
    forever, with no control on screen to say otherwise.

    The switch makes the control VISIBLE. It does not turn bypass on — that is
    the next test, and it is the assertion that keeps this one safe.
    """

    assert WorkspaceBehaviorOverrides().filesystem_bypass_enabled is True


def test_offering_the_control_is_not_the_same_as_using_it() -> None:
    """Master on, nobody picked: still Manual.

    The pair of these two tests IS the safety argument for the default above. If
    this one ever flips, turning on a Settings toggle would silently start
    auto-approving writes.
    """

    decision = FilesystemBypassResolver.resolve(master_enabled=True)

    assert decision.offered is True
    assert decision.mode is FilesystemBypassMode.MANUAL
    assert decision.skips_approval_pause is False


def test_a_run_request_without_a_selection_reads_as_none() -> None:
    """Absent is distinguishable from an explicit Manual pick on the wire."""

    request = CreateRunRequest(
        conversation_id="conv_1",
        org_id="org_1",
        user_id="user_1",
        user_input="hi",
    )
    assert request.filesystem_bypass is None


class TestMasterSwitchGatesEveryOtherTier(ByokCoordinatorMixin):
    async def test_no_workspace_row_at_all_seals_manual(self) -> None:
        """A deployment that never touched the setting cannot be in bypass."""

        run_coordinator, store, conversation_id = await self._build(with_key=True)

        await run_coordinator.create_run(
            _run_request(
                conversation_id,
                selection=FilesystemBypassSelection(
                    message=FilesystemBypassMode.BYPASS
                ),
            )
        )

        sealed = store.run_commands[0].runtime_context.filesystem_bypass
        assert sealed.mode is FilesystemBypassMode.MANUAL
        assert sealed.offered is False

    async def test_master_off_refuses_a_client_supplied_bypass(self) -> None:
        """A stale or hostile client cannot opt itself in."""

        run_coordinator, store, conversation_id = await self._build(with_key=True)
        await _seed_master_switch(store, enabled=False)

        await run_coordinator.create_run(
            _run_request(
                conversation_id,
                selection=FilesystemBypassSelection(run=FilesystemBypassMode.BYPASS),
            )
        )

        sealed = store.run_commands[0].runtime_context.filesystem_bypass
        assert sealed.mode is FilesystemBypassMode.MANUAL
        assert sealed.source is FilesystemBypassSource.MASTER_OFF

    async def test_master_on_alone_still_seals_manual(self) -> None:
        """Turning the switch on offers the control; it does not use it."""

        run_coordinator, store, conversation_id = await self._build(with_key=True)
        await _seed_master_switch(store, enabled=True)

        await run_coordinator.create_run(_run_request(conversation_id))

        sealed = store.run_commands[0].runtime_context.filesystem_bypass
        assert sealed.offered is True
        assert sealed.mode is FilesystemBypassMode.MANUAL
        assert sealed.skips_approval_pause is False

    async def test_master_on_plus_a_message_selection_seals_bypass(self) -> None:
        run_coordinator, store, conversation_id = await self._build(with_key=True)
        await _seed_master_switch(store, enabled=True)

        await run_coordinator.create_run(
            _run_request(
                conversation_id,
                selection=FilesystemBypassSelection(
                    message=FilesystemBypassMode.BYPASS
                ),
            )
        )

        sealed = store.run_commands[0].runtime_context.filesystem_bypass
        assert sealed.mode is FilesystemBypassMode.BYPASS
        assert sealed.source is FilesystemBypassSource.MESSAGE
        assert sealed.skips_approval_pause is True

    async def test_a_message_manual_overrides_a_sticky_run_bypass(self) -> None:
        """Precedence, end to end: one turn can opt back out of a run bypass."""

        run_coordinator, store, conversation_id = await self._build(with_key=True)
        await _seed_master_switch(store, enabled=True)

        await run_coordinator.create_run(
            _run_request(
                conversation_id,
                selection=FilesystemBypassSelection(
                    run=FilesystemBypassMode.BYPASS,
                    message=FilesystemBypassMode.MANUAL,
                ),
            )
        )

        sealed = store.run_commands[0].runtime_context.filesystem_bypass
        assert sealed.mode is FilesystemBypassMode.MANUAL
        assert sealed.source is FilesystemBypassSource.MESSAGE
