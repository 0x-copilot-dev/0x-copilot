"""Bypass, a granted folder and the `.tmp` scratch — composed together, for real.

Two lanes built the halves of this in isolation and neither could write these
tests, because each one's harness supplied the other's fact:

* the bypass lane (PRD-FS-10/11) proved every property of
  `WorkspaceOperationAdapter` by constructing `WorkspaceGatewayServices(bypass=…)`
  in the test. That proves the ADAPTER reads the decision and nothing at all
  about the run handler HANDING it over — and the handler is the only producer,
  because the decision is sealed at run-create and persisted on the run;
* the scratch lane (PRD-FS-12) proved every property of `$COPILOT_HOME/.tmp` by
  building an `AgentScratchRoot` and passing it to the rule set and the floor
  directly, never through `factory.acreate_agent_runtime`.

So everything below drives the REAL composition. The run record carries the
persisted decision; the real `RuntimeRunHandler` builds the real ENFORCE
backend; the real `EffectStager` writes to the real `RuntimeEffectLedger`,
which appends real runtime events to the store; and the real
`acreate_agent_runtime` composes the rules and the floor. Nothing here injects
the thing it is asserting.

WHAT BYPASS IS, restated as the shape of these assertions: it removes the
approval PAUSE and nothing else. There is one host-write lane —
staged C3 overlay → A4 effect ledger → C2 commit executor — and a bypassed
write goes down all of it, with the approval row authored by
`EffectActor.POLICY` instead of a human. "Faster" must never mean "fewer rows".
"""

from __future__ import annotations

import pytest

from agent_runtime.capabilities.desktop.agent_scratch import agent_scratch_root
from agent_runtime.capabilities.operations.context import (
    OperationContext,
    VerifiedOperationIdentity,
)
from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.capabilities.tools.permissions import ToolUsePolicySnapshot
from agent_runtime.capabilities.workspace.deep_backend import WorkspaceGatewayBackend
from agent_runtime.capabilities.workspace.effects import WorkspaceGrantBinding
from agent_runtime.execution.contracts import RuntimeDependencies
from agent_runtime.execution.factory import (
    _host_filesystem_permissions,
    acreate_agent_runtime,
)
from agent_runtime.execution.filesystem_bypass import (
    MANUAL_FILESYSTEM_BYPASS,
    FilesystemBypassDecision,
    FilesystemBypassMode,
    FilesystemBypassSource,
)
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import RunRecord
from runtime_worker.handlers.run import RuntimeRunHandler
from runtime_worker.workspace_effect_storage import (
    InMemoryWorkspaceHostSessionRegistry,
    WorkspaceHostSession,
)

from tests.unit.agent_runtime.agent.helpers import CapturingAgentBuilder
from tests.unit.agent_runtime.capabilities.desktop.fakes import (
    TEST_BASE_URL,
    TEST_TOKEN,
    FakeBrokerFs,
    RecordingBroker,
)
from tests.unit.runtime_worker.test_workspace_effect_wiring import (
    EmptyReadBase,
    UnusedAuthority,
    _command,
    _context,
    _handler,
    _scope,
)

# `asyncio_mode = "auto"` (pyproject) runs the async cases; no module mark, so
# the synchronous rule-set cases below are not decorated with one they ignore.

#: The folder the user attached, in the host's own spelling. The broker reports
#: it; the mount below is how the model addresses it.
ATTACHED = "/Users/ada/Projects"
MOUNT = "projects"
UNGRANTED_MOUNT = "elsewhere"

BYPASS_ON = FilesystemBypassDecision(
    master_enabled=True,
    mode=FilesystemBypassMode.BYPASS,
    source=FilesystemBypassSource.MESSAGE,
)

_DECISION_EVENT = "effect.decision_recorded"


def _run_with(bypass: FilesystemBypassDecision) -> RunRecord:
    """A run whose PERSISTED context carries the sealed decision.

    This is the only way the worker ever learns the mode — `RunCoordinator`
    folds master ▸ run ▸ message once at run-create and stores the answer on the
    run, so re-deriving it here would be re-deciding it.
    """

    context = _context().model_copy(update={"filesystem_bypass": bypass})
    return RunRecord(
        run_id="run-c3",
        conversation_id="conv-c3",
        org_id="org-c3",
        user_id="user-c3",
        user_message_id="msg-c3",
        trace_id="trace-c3",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        runtime_context=context,
    )


def _sessions(*, mode: str = "read_write") -> InMemoryWorkspaceHostSessionRegistry:
    """One bound host session exposing exactly one granted mount."""

    sessions = InMemoryWorkspaceHostSessionRegistry()
    sessions.bind(
        scope=_scope(),
        session=WorkspaceHostSession(
            grants=(
                WorkspaceGrantBinding(
                    mount_name=MOUNT,
                    grant_id="grant-projects",
                    mount_label="Projects",
                    mode=mode,
                ),
            ),
            base_read=EmptyReadBase(),
            host_session_ref=f"whs_{'x' * 43}",
            authority=UnusedAuthority(),  # type: ignore[arg-type]
        ),
    )
    return sessions


async def _enforce_backend(
    *,
    bypass: FilesystemBypassDecision,
    mode: str = "read_write",
    broker: RecordingBroker | None = None,
) -> tuple[
    RuntimeRunHandler, InMemoryRuntimeApiStore, WorkspaceGatewayBackend, RunRecord
]:
    """The real ENFORCE composition for a run that carries `bypass`."""

    handler, store = _handler(sessions=_sessions(mode=mode), broker=broker)
    run = _run_with(bypass)
    # The ledger appends through the run record, so the store has to know it.
    store.runs[run.run_id] = run
    services = handler._build_mcp_operation_gateway_services(run)
    assert services is not None
    backend = await handler._workspace_backend_for_run(
        _command(), run=run, mcp_gateway_services=services
    )
    assert isinstance(backend, WorkspaceGatewayBackend)
    return handler, store, backend, run


async def _write(backend: WorkspaceGatewayBackend, path: str) -> object:
    """Drive one model-shaped write through the composed backend."""

    token = OperationContext.bind_for_run(
        identity=VerifiedOperationIdentity(
            org_id="org-c3",
            user_id="user-c3",
            conversation_id="conv-c3",
            run_id="run-c3",
        ),
        # ASK is the posture bypass has to overcome. With `auto` here the test
        # would pass through the pre-existing user-AUTO lane and prove nothing
        # about bypass at all.
        policy_snapshot=ToolUsePolicySnapshot.from_response(
            user={"write": "ask", "destructive": "require"}
        ),
        ledger_emitter=None,
        artifact_service=None,
        mode=OperationGatewayMode.ENFORCE,
        canonical_arguments_durable=True,
    )
    try:
        return await backend.awrite(path, "account,total\nAcme,10\n")
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]


async def _effect_events(store: InMemoryRuntimeApiStore) -> list[tuple[str, dict]]:
    """Every effect row this run actually appended, in stream order."""

    envelopes = await store.list_events_after(
        org_id="org-c3", run_id="run-c3", after_sequence=0
    )
    return [
        (envelope.event_type.value, dict(envelope.payload))
        for envelope in envelopes
        if envelope.event_type.value.startswith("effect.")
    ]


class TestTheBypassHandOffIsItselfExercised:
    """`run.py` must actually PASS the run's decision to the adapter.

    The line under test is one keyword argument:

        bypass=run.runtime_context.filesystem_bypass

    Delete it and `WorkspaceGatewayServices.bypass` falls back to its default,
    `MANUAL_FILESYSTEM_BYPASS` — every bypassed run silently asks again and the
    whole feature is inert. That deletion was invisible to the entire suite,
    because every bypass test constructed the services itself.

    So these two cases differ in ONE input: the decision persisted on the run.
    Everything else — handler, backend, stager, ledger, queue — is identical, so
    a difference in the rows can only have come through the hand-off.
    """

    async def test_a_bypassed_run_records_the_policy_approval(self) -> None:
        _handler_, store, backend, _run = await _enforce_backend(bypass=BYPASS_ON)

        await _write(backend, f"/workspace/{MOUNT}/report.csv")

        events = await _effect_events(store)
        assert [name for name, _ in events] == [
            "effect.staged",
            "effect.projection_bound",
            _DECISION_EVENT,
        ]
        _name, decision = events[-1]
        assert decision["decision"] == "approve"
        # POLICY, not USER: the run's history still answers "who approved this",
        # and answers it honestly.
        assert decision["actor"] == "policy"
        assert len(store.effect_commit_commands) == 1

    async def test_a_manual_run_through_the_same_composition_still_pauses(
        self,
    ) -> None:
        _handler_, store, backend, _run = await _enforce_backend(
            bypass=MANUAL_FILESYSTEM_BYPASS
        )

        await _write(backend, f"/workspace/{MOUNT}/report.csv")

        events = await _effect_events(store)
        assert [name for name, _ in events] == [
            "effect.staged",
            "effect.projection_bound",
        ]
        assert store.effect_commit_commands == []

    async def test_the_decision_comes_from_the_run_not_from_the_worker(
        self,
    ) -> None:
        """The hand-off reads the PERSISTED context, so a stale run stays stale.

        A run created under Manual must keep asking even if the deployment's
        master switch has been turned on since. Stated as its own case because
        "read it from the run" and "re-resolve it here" are indistinguishable
        while the two agree — and they only disagree after a Settings change,
        which is exactly when getting it wrong retro-authorizes a run the user
        started under a different posture.
        """

        _handler_, store, backend, run = await _enforce_backend(
            bypass=MANUAL_FILESYSTEM_BYPASS
        )
        # The master switch flips ON in the workspace defaults the worker can
        # see. The run's own sealed decision does not move with it.
        assert run.runtime_context.filesystem_bypass == MANUAL_FILESYSTEM_BYPASS

        await _write(backend, f"/workspace/{MOUNT}/report.csv")

        assert _DECISION_EVENT not in [name for name, _ in await _effect_events(store)]
        assert store.effect_commit_commands == []


class TestBypassInsideAGrantedFolderWithScratch:
    """All three facts at once: bypass ON, a writable grant, `.tmp` live.

    Neither lane could write this. The bypass lane had no scratch and no real
    grant resolution; the scratch lane had no bypass. Composed, the question
    they leave open is whether the three of them add up to a second way onto the
    disk — a bypassed write that skips the ledger, or a grant that becomes
    directly writable because the agent now has somewhere legitimate to write.

    They do not, and each half of that is asserted against the layer that owns
    it: the LEDGER for what the staged lane recorded, and the FLOOR for what the
    filesystem tools may touch.
    """

    @staticmethod
    def _broker_env(monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DESKTOP_WORKSPACE_BROKER_URL", TEST_BASE_URL)
        monkeypatch.setenv("DESKTOP_WORKSPACE_BROKER_TOKEN", TEST_TOKEN)

    @staticmethod
    def _broker() -> RecordingBroker:
        """A broker whose active snapshot carries the one attached folder."""

        return RecordingBroker(
            grants={"grant-projects": FakeBrokerFs(files={"notes.md": b"hello\n"})},
            grant_meta={
                "grant-projects": {
                    "label": "Projects",
                    "mount": f"mnt_{MOUNT}",
                    "mode": "read_write",
                    "root": ATTACHED,
                }
            },
        )

    async def test_a_bypassed_write_is_staged_and_ledgered_never_skipped(
        self,
    ) -> None:
        """The point of the whole feature, proved on the composition that ships.

        Three rows and one queued commit. The C2 executor is still the only
        thing that will touch the disk; what bypass removed is the wait between
        `projection_bound` and `decision`, not any step.
        """

        _handler_, store, backend, _run = await _enforce_backend(bypass=BYPASS_ON)

        result = await _write(backend, f"/workspace/{MOUNT}/report.csv")

        events = await _effect_events(store)
        assert [name for name, _ in events] == [
            "effect.staged",
            "effect.projection_bound",
            _DECISION_EVENT,
        ]
        # The staged row itself says it was eligible, so a reader of the run's
        # history is never shown `policy=ask` followed by a policy approval.
        assert events[0][1]["policy"] == "auto"
        assert events[-1][1]["actor"] == "policy"
        assert len(store.effect_commit_commands) == 1
        commit = store.effect_commit_commands[0]
        assert commit.run_id == "run-c3"
        assert commit.stage_id == events[0][1]["stage_id"]
        # Bypass did not become "the write already happened": the model is told
        # the change is on the staged lane, which it is.
        #
        # NOTE for whoever reads this next — the adapter DOES compose a distinct
        # bypass summary (`effects._BYPASS_APPROVED_SUMMARY`, "approved
        # automatically … and queued to apply") and it never reaches here:
        # `OperationGateway._disposition` rebuilds `agent_summary` for a STAGED
        # outcome and `effects.staged_disposition_message` then replaces even
        # that with the fixed staged string. The constant is dead today. That is
        # a copy defect, not a safety one — the message understates what
        # happened rather than overstating it — and fixing it means teaching
        # `OperationDisposition` to carry the auto-approval, which is a contract
        # change and not a merge's business.
        assert result.error is not None
        assert "host was not modified" in result.error

    async def test_an_ungranted_path_still_asks_under_bypass(self) -> None:
        """The bound. Bypass never widens WHAT was granted, only WHEN it waits.

        The mount is one the host session does not expose, so the grant gate
        refuses before anything is staged. Under bypass that refusal has to be
        byte-identical to the manual one: no stage, no decision, no commit. A
        single approval must never become blanket write access to the machine.
        """

        _handler_, store, backend, _run = await _enforce_backend(bypass=BYPASS_ON)

        result = await _write(backend, f"/workspace/{UNGRANTED_MOUNT}/report.csv")

        assert result.error is not None
        assert await _effect_events(store) == []
        assert store.effect_commit_commands == []

    async def test_nothing_is_written_into_the_granted_folder_itself(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """The filesystem tools' view of the same run, with the same three facts.

        The staged lane above is one door onto the disk. This is the other: the
        `FilesystemPermission` rules and `HostFilesystemFloor` that deepagents
        was actually handed. Bypass must not open it, and the scratch must not
        drag a granted folder open with it.

        Read as a set, the four assertions say: the agent may write in its own
        `.tmp` and nowhere else on the host; the folder the user attached is
        readable and not writable — not its content, and not the `.copilot`
        subdirectory PRD-FS-12 D7 stopped siting there.
        """

        self._broker_env(monkeypatch)
        handler, _store, backend, run = await _enforce_backend(
            bypass=BYPASS_ON, broker=self._broker()
        )
        # Resolved by the real broker wiring off the real snapshot — the
        # ENFORCE workspace object structurally cannot name a host root.
        roots = await handler._granted_host_roots_for_run(backend)
        assert [root.path for root in roots or ()] == [ATTACHED]

        builder = CapturingAgentBuilder()
        await acreate_agent_runtime(
            context=run.runtime_context,
            dependencies=fake_dependencies.model_copy(
                update={"workspace_backend": backend, "granted_host_roots": roots}
            ),
            agent_builder=builder,
        )
        floor = builder.calls[0].memory_backend.default
        scratch = agent_scratch_root()

        # The one host location the agent may write — and it needed no grant.
        assert floor.permits_write(f"{scratch.posix}/conv-c3/run-c3/notes.md") is True
        # The attached folder: readable, never directly writable, under bypass.
        assert floor.permits_read(f"{ATTACHED}/.git/config") is True
        assert floor.permits_write(f"{ATTACHED}/report.csv") is False
        assert floor.permits_write(f"{ATTACHED}/.copilot/notes.md") is False


class TestTheScratchReachesTheRulesTheFactoryHandsDeepagents:
    """The factory must put the scratch allow in the rule set, not just the floor.

    This closes a seam the merge exposed. `_host_filesystem_permissions` builds
    the rules with `scratch=…`; deleting that argument left 9729 tests green,
    because on the default configuration `COPILOT_HOME` is itself dotted
    (`~/.0xcopilot`), so every path beneath the scratch is invisible to
    `wcmatch` and `_check_fs_permission` answers `allow` by the unmatched
    default whether or not the rule exists. The floor is what genuinely decides
    there — which is why every behavioural test survived the mutation.

    The rule is still load-bearing, in the two configurations one edit away: a
    visible scratch name, and a future upstream `DOTGLOB` that would make rule 5
    total over hidden paths and deny every scratch write. Both are futures, so
    neither can be asserted by behaviour today. What CAN be asserted, and is, is
    that the rule set the factory composes names the scratch at all.
    """

    def test_the_composed_rule_set_names_the_scratch_root(self) -> None:
        scratch = agent_scratch_root()

        rules = _host_filesystem_permissions(object(), granted_host_roots=())

        assert any(
            any(scratch.posix in path for path in rule.paths or ()) for rule in rules
        ), "the factory built host rules that never mention the agent's scratch"

    def test_the_scratch_allow_precedes_the_catch_all_deny(self) -> None:
        """Order is the security property — first match wins.

        A scratch allow appended AFTER rule 5 would be unreachable the moment
        the matcher can see the path, which is the exact day it starts to
        matter.
        """

        scratch = agent_scratch_root()
        rules = list(_host_filesystem_permissions(object(), granted_host_roots=()))

        allow_at = next(
            index
            for index, rule in enumerate(rules)
            if any(scratch.posix in path for path in rule.paths or ())
        )
        deny_at = next(
            index
            for index, rule in enumerate(rules)
            if rule.mode == "deny" and "/**" in (rule.paths or ())
        )
        assert allow_at < deny_at

    def test_the_threaded_scratch_is_the_one_that_lands_in_the_rules(self) -> None:
        """`agent_scratch=` must be USED, not merely accepted.

        The whole reason it is threaded is so a run's rules and its floor are
        built from one object. A parameter that is quietly ignored in favour of
        a fresh env read would keep every other test green while re-opening the
        divergence the threading exists to close.
        """

        class _Elsewhere:
            posix = "/tmp/somewhere-else/.tmp"

            @staticmethod
            def allow_rules() -> tuple[dict[str, object], ...]:
                return (
                    {
                        "operations": ["read", "write"],
                        "paths": ["/tmp/somewhere-else/.tmp/**"],
                        "mode": "allow",
                    },
                )

        rules = _host_filesystem_permissions(
            object(), granted_host_roots=(), agent_scratch=_Elsewhere()
        )

        assert any(
            "/tmp/somewhere-else/.tmp/**" in (rule.paths or ()) for rule in rules
        )
        assert not any(
            any(agent_scratch_root().posix in path for path in rule.paths or ())
            for rule in rules
        )
