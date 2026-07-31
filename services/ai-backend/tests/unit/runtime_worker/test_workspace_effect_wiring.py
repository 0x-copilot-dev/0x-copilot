"""Composition and no-fallthrough guards for C3 enforce mode."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
import json
import logging
from types import SimpleNamespace

import httpx
import pytest

from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.capabilities.workspace.contracts import (
    WorkspaceBaseEntry,
    WorkspaceBaseMatch,
)
from agent_runtime.capabilities.workspace.deep_backend import (
    WorkspaceGatewayBackend,
    WorkspaceTombstoneBackend,
)
from agent_runtime.capabilities.workspace.effects import WorkspaceGrantBinding
from agent_runtime.effects.executor import EffectExecutionScope
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    ModelConfig,
    RuntimeDependencies,
)
from agent_runtime.execution.deep_agent_builder import (
    WORKSPACE_STAGED_WRITE_GUIDANCE,
)
from agent_runtime.execution.factory import (
    _composed_deep_backend,
    _instructions_with_workspace,
    _workspace_write_permissions,
    acreate_agent_runtime,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.artifact_references import InMemoryArtifactReferenceStore
from runtime_adapters.in_memory.artifact_blob_store import InMemoryArtifactBlobStore
from runtime_adapters.in_memory.artifact_publication import (
    InMemoryArtifactPublicationCoordinator,
)
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_adapters.in_memory.workspace_overlay_store import (
    InMemoryWorkspaceOverlayStore,
)
from runtime_api.schemas import RunRecord, RuntimeRunCommand
from runtime_worker.handlers.approval import RuntimeApprovalHandler
from runtime_worker.handlers.run import RuntimeRunHandler
from runtime_worker.workspace_backend_wiring import WorkspaceBackendWorkerWiring
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


def _settings() -> RuntimeSettings:
    capabilities = (
        "operation_gateway",
        "effect_stager",
        "effect_commit",
        "mcp_gateway",
        "workspace_overlay",
        "workspace_commit",
    )
    return RuntimeSettings.load(
        environ={
            "SURFACES_V2": "true",
            "OPERATION_GATEWAY_MODE": OperationGatewayMode.ENFORCE.value,
            "WORKSPACE_EFFECT_MODE": OperationGatewayMode.ENFORCE.value,
            "EFFECT_STAGER_MODE": "enforce",
            "EFFECT_COMMIT_MODE": "enforce",
            "MCP_GATEWAY_MODE": "enforce",
            "WORKSPACE_OVERLAY_MODE": "enforce",
            "WORKSPACE_COMMIT_MODE": "enforce",
            "E2_ROLLOUT_COHORTS_JSON": json.dumps(
                [
                    {
                        "capability": capability,
                        "org_id": "org-c3",
                        "user_id": "user-c3",
                    }
                    for capability in capabilities
                ]
            ),
        }
    )


def _context() -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id="user-c3",
        org_id="org-c3",
        roles={"employee"},
        model_profile=ModelConfig(
            provider="openai",
            model_name="gpt-5.4-mini",
            max_input_tokens=4096,
            timeout_seconds=30,
            temperature=0,
        ),
        run_id="run-c3",
        trace_id="trace-c3",
    )


def _run() -> RunRecord:
    context = _context()
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


def _command() -> RuntimeRunCommand:
    context = _context()
    return RuntimeRunCommand(
        run_id=context.run_id or "run-c3",
        conversation_id="conv-c3",
        org_id=context.org_id,
        user_id=context.user_id,
        trace_id=context.trace_id,
        runtime_context=context,
    )


def _scope() -> EffectExecutionScope:
    return EffectExecutionScope(
        org_id="org-c3",
        user_id="user-c3",
        conversation_id="conv-c3",
        run_id="run-c3",
        owner_ref="principal://users/user-c3",
    )


class EmptyReadBase:
    async def stat(self, _virtual_path: str) -> WorkspaceBaseEntry | None:
        return None

    async def read(
        self,
        _virtual_path: str,
        *,
        start: int | None = None,
        end: int | None = None,
    ) -> AsyncIterator[bytes]:
        del start, end

        async def _stream() -> AsyncIterator[bytes]:
            if False:  # pragma: no cover - typed empty async iterator.
                yield b""

        return _stream()

    async def list(self, _virtual_path: str) -> Sequence[WorkspaceBaseEntry]:
        return ()

    async def glob(self, _pattern: str) -> Sequence[WorkspaceBaseEntry]:
        return ()

    async def grep(
        self, _query: str, _paths: Sequence[str] | None = None
    ) -> Sequence[WorkspaceBaseMatch]:
        return ()


class UnusedAuthority:
    pass


def _handler(
    *,
    sessions: InMemoryWorkspaceHostSessionRegistry | None,
    broker: RecordingBroker | None = None,
    settings: RuntimeSettings | None = None,
) -> tuple[RuntimeRunHandler, InMemoryRuntimeApiStore]:
    store = InMemoryRuntimeApiStore()
    publication = InMemoryArtifactPublicationCoordinator()
    return (
        RuntimeRunHandler(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings or _settings(),
            artifact_blob_store=InMemoryArtifactBlobStore(publication),
            artifact_reference_store=InMemoryArtifactReferenceStore(publication),
            workspace_host_sessions=sessions,
            workspace_overlay_store=InMemoryWorkspaceOverlayStore(),
            workspace_broker_http_client=(
                None
                if broker is None
                else httpx.AsyncClient(transport=broker.transport())
            ),
        ),
        store,
    )


async def test_enforce_without_host_authority_mounts_tombstone() -> None:
    handler, _store = _handler(sessions=InMemoryWorkspaceHostSessionRegistry())
    run = _run()
    services = handler._build_mcp_operation_gateway_services(run)
    assert services is not None

    backend = await handler._workspace_backend_for_run(
        _command(),
        run=run,
        mcp_gateway_services=services,
    )

    assert isinstance(backend, WorkspaceTombstoneBackend)
    result = await backend.awrite("finance/report.csv", "x")
    assert result.error is not None
    assert "no local file was changed" in result.error


async def test_enforce_stale_legacy_approval_resumes_into_tombstone() -> None:
    store = InMemoryRuntimeApiStore()
    handler = RuntimeApprovalHandler(
        persistence=store,
        event_store=store,
        settings=_settings(),
    )

    backend = await handler._workspace_backend_for_resume(_run())

    assert isinstance(backend, WorkspaceTombstoneBackend)
    result = await backend.awrite("finance/report.csv", "x")
    assert result.error is not None
    assert "no local file was changed" in result.error


async def test_enforce_backend_exposes_no_stager_adapter_or_host_writer() -> None:
    sessions = InMemoryWorkspaceHostSessionRegistry()
    sessions.bind(
        scope=_scope(),
        session=WorkspaceHostSession(
            grants=(
                WorkspaceGrantBinding(
                    mount_name="finance",
                    grant_id="grant-finance",
                    mount_label="Finance",
                    mode="read_write",
                ),
            ),
            base_read=EmptyReadBase(),
            host_session_ref=f"whs_{'x' * 43}",
            authority=UnusedAuthority(),  # type: ignore[arg-type]
        ),
    )
    handler, _store = _handler(sessions=sessions)
    run = _run()
    services = handler._build_mcp_operation_gateway_services(run)
    assert services is not None

    backend = await handler._workspace_backend_for_run(
        _command(),
        run=run,
        mcp_gateway_services=services,
    )

    assert isinstance(backend, WorkspaceGatewayBackend)
    # The worker composition supplies the real A4 stager when binding the
    # opaque operation route.  The model-visible backend intentionally cannot
    # inspect that composition or walk to its raw overlay engine.
    assert not hasattr(backend, "_adapter")
    assert not hasattr(backend, "_gateway")
    assert not hasattr(backend._operations, "_adapter")
    assert not hasattr(backend._operations, "_mutations")
    assert not hasattr(backend, "client")
    assert not hasattr(backend, "apply")


async def test_tombstone_route_cannot_fall_through_to_state_backend() -> None:
    composite = _composed_deep_backend(
        None,
        workspace_backend=WorkspaceTombstoneBackend(),
    )
    assert composite is not None

    result = await composite.awrite("/workspace/finance/report.csv", "x")

    assert result.error is not None
    assert "no local file was changed" in result.error
    assert "/workspace/" in composite.routes


def test_staged_workspace_never_installs_generic_filesystem_interrupt() -> None:
    assert _workspace_write_permissions(True, effect_staged=True) == ()


# --- attached folders in ENFORCE mode -----------------------------------------

#: The folder the user attached in these tests, and one they never did.
_ATTACHED = "/Users/ada/Projects"
_UNGRANTED = "/Users/ada/Secrets"


def _attach(root: str = _ATTACHED, *, mode: str = "read_only") -> RecordingBroker:
    """A broker whose active snapshot carries one attached folder."""

    return RecordingBroker(
        grants={"grant-projects": FakeBrokerFs(files={"notes.md": b"hello\n"})},
        grant_meta={
            "grant-projects": {
                "label": "Projects",
                "mount": "mnt_projects",
                "mode": mode,
                "root": root,
            }
        },
    )


class TestEnforceLaneGrantedRoots:
    """An ATTACHED folder must stop asking in ENFORCE, and only that folder.

    The defect: ``run.py`` branches on ``workspace_effect_mode``, and in ENFORCE
    the ``/workspace/`` object is ``WorkspaceGatewayBackend`` /
    ``WorkspaceTombstoneBackend``. Neither can name a host root — their
    host-session projection is path-free by design, and that channel is C2's
    private WRITE bootstrap, not something to widen — so ``_granted_host_roots``
    read the capability, found nothing, built no ``allow`` rule, and every read
    of a folder the user had explicitly attached interrupted and asked AGAIN.
    Attaching a folder bought the user nothing in this lane.

    These drive the real path: the real ENFORCE handler builds the real C3
    backend, the real broker wiring resolves the roots off the real
    ``/v1/grants/snapshot`` projection, the real ``acreate_agent_runtime``
    composes them, and the assertions read the rule list deepagents was actually
    handed plus the floor the composite actually mounted.
    """

    @staticmethod
    def _broker_env(monkeypatch: pytest.MonkeyPatch) -> None:
        """The two variables the desktop supervisor forwards to a child."""

        monkeypatch.setenv("DESKTOP_WORKSPACE_BROKER_URL", TEST_BASE_URL)
        monkeypatch.setenv("DESKTOP_WORKSPACE_BROKER_TOKEN", TEST_TOKEN)

    @staticmethod
    async def _enforce_backend(
        handler: RuntimeRunHandler, run: RunRecord
    ) -> WorkspaceGatewayBackend:
        services = handler._build_mcp_operation_gateway_services(run)
        assert services is not None
        backend = await handler._workspace_backend_for_run(
            _command(), run=run, mcp_gateway_services=services
        )
        assert isinstance(backend, WorkspaceGatewayBackend)
        return backend

    @staticmethod
    def _bound_sessions() -> InMemoryWorkspaceHostSessionRegistry:
        sessions = InMemoryWorkspaceHostSessionRegistry()
        sessions.bind(
            scope=_scope(),
            session=WorkspaceHostSession(
                grants=(
                    WorkspaceGrantBinding(
                        mount_name="projects",
                        grant_id="grant-projects",
                        mount_label="Projects",
                        mode="read_only",
                    ),
                ),
                base_read=EmptyReadBase(),
                host_session_ref=f"whs_{'x' * 43}",
                authority=UnusedAuthority(),  # type: ignore[arg-type]
            ),
        )
        return sessions

    async def test_the_enforce_backend_itself_still_cannot_name_a_host_root(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The premise, pinned: this is why the roots come from the broker.

        If ``WorkspaceGatewayBackend`` ever grows a ``granted_roots`` property,
        somebody has put a host path on the host-session channel and this test
        should be the thing that says so.
        """

        self._broker_env(monkeypatch)
        handler, _store = _handler(sessions=self._bound_sessions(), broker=_attach())
        backend = await self._enforce_backend(handler, _run())

        assert not hasattr(backend, "granted_roots")

    async def test_an_attached_folder_stops_asking_in_enforce_mode(
        self, monkeypatch: pytest.MonkeyPatch, fake_dependencies: RuntimeDependencies
    ) -> None:
        """The fix, end to end, read off the rules deepagents was actually given."""

        from deepagents.middleware.filesystem import _check_fs_permission

        self._broker_env(monkeypatch)
        broker = _attach()
        handler, _store = _handler(sessions=self._bound_sessions(), broker=broker)
        backend = await self._enforce_backend(handler, _run())

        roots = await handler._granted_host_roots_for_run(backend)
        assert [root.path for root in roots or ()] == [_ATTACHED]

        builder = CapturingAgentBuilder()
        await acreate_agent_runtime(
            context=_context(),
            dependencies=fake_dependencies.model_copy(
                update={
                    "workspace_backend": backend,
                    "granted_host_roots": roots,
                }
            ),
            agent_builder=builder,
        )
        rules = list(builder.calls[0].permissions)

        # The attached folder reads without a consent card...
        assert _check_fs_permission(rules, "read", _ATTACHED) == "allow"
        assert _check_fs_permission(rules, "read", f"{_ATTACHED}/notes.md") == "allow"
        # ...and nothing else does. Attaching one folder is not attaching a disk.
        assert _check_fs_permission(rules, "read", _UNGRANTED) == "interrupt"
        assert _check_fs_permission(rules, "read", f"{_UNGRANTED}/x") == "interrupt"
        # A sibling that merely shares a prefix is a different folder.
        assert (
            _check_fs_permission(rules, "read", f"{_ATTACHED}Archive/x") == "interrupt"
        )

    async def test_reading_an_attached_file_raises_no_consent_card(
        self, monkeypatch: pytest.MonkeyPatch, fake_dependencies: RuntimeDependencies
    ) -> None:
        """ "Stops asking" stated against the thing that decides whether it asks.

        ``_check_fs_permission`` is the verdict; the CARD comes from deepagents'
        own interrupt predicate, built from the same rules. Asserting the verdict
        alone once let a rename hide the fact that nothing was pinning the
        prompt, so this drives the predicate builder itself.

        Only ``read_file`` is asserted, and that is not laziness:
        ``ls``/``glob``/``grep`` use the BULK predicate, which fires on any
        overlap with an interrupt anchor — and rule 4's anchor is ``/``. Those
        always ask, attached or not, by design (see ``host_floor``'s header).
        """

        from deepagents.middleware._fs_interrupt import (
            _build_interrupt_on_from_permissions,
        )

        self._broker_env(monkeypatch)
        handler, _store = _handler(sessions=self._bound_sessions(), broker=_attach())
        backend = await self._enforce_backend(handler, _run())
        roots = await handler._granted_host_roots_for_run(backend)

        builder = CapturingAgentBuilder()
        await acreate_agent_runtime(
            context=_context(),
            dependencies=fake_dependencies.model_copy(
                update={"workspace_backend": backend, "granted_host_roots": roots}
            ),
            agent_builder=builder,
        )
        config = _build_interrupt_on_from_permissions(
            list(builder.calls[0].permissions)
        )["read_file"]
        asks = config["when"] if isinstance(config, dict) else config.when

        def _request(path: str) -> SimpleNamespace:
            return SimpleNamespace(tool_call={"args": {"file_path": path}})

        assert asks(_request(f"{_ATTACHED}/notes.md")) is False
        assert asks(_request(f"{_UNGRANTED}/notes.md")) is True

    async def test_the_floor_admits_the_same_folder_the_rules_do(
        self, monkeypatch: pytest.MonkeyPatch, fake_dependencies: RuntimeDependencies
    ) -> None:
        """Rules and floor must be one decision, in this lane too.

        The rule set is blind to dot segments, so ``<attached>/.git/config`` is
        decided by the floor alone. A floor built from a different source than
        the rules would refuse a folder the user had just attached.
        """

        self._broker_env(monkeypatch)
        handler, _store = _handler(sessions=self._bound_sessions(), broker=_attach())
        backend = await self._enforce_backend(handler, _run())
        roots = await handler._granted_host_roots_for_run(backend)

        builder = CapturingAgentBuilder()
        await acreate_agent_runtime(
            context=_context(),
            dependencies=fake_dependencies.model_copy(
                update={"workspace_backend": backend, "granted_host_roots": roots}
            ),
            agent_builder=builder,
        )
        floor = builder.calls[0].memory_backend.default

        assert [root.path for root in floor.roots] == [_ATTACHED]
        assert floor.permits_read(f"{_ATTACHED}/.git/config") is True
        assert floor.permits_read(f"{_UNGRANTED}/.env") is False
        assert floor.permits_read("/Users/ada/.ssh/id_rsa") is False

    async def test_attaching_a_folder_never_authorizes_a_host_write(
        self, monkeypatch: pytest.MonkeyPatch, fake_dependencies: RuntimeDependencies
    ) -> None:
        """D7, restated for the lane that now has roots.

        Widening READS is the whole change. A read-only grant must not gain a
        writable scratch, and no grant of any mode makes the user's own content
        directly writable — host writes stay on the staged C3 → ledger → C2 lane.
        """

        from deepagents.middleware.filesystem import _check_fs_permission

        self._broker_env(monkeypatch)
        handler, _store = _handler(sessions=self._bound_sessions(), broker=_attach())
        backend = await self._enforce_backend(handler, _run())
        roots = await handler._granted_host_roots_for_run(backend)

        builder = CapturingAgentBuilder()
        await acreate_agent_runtime(
            context=_context(),
            dependencies=fake_dependencies.model_copy(
                update={"workspace_backend": backend, "granted_host_roots": roots}
            ),
            agent_builder=builder,
        )
        rules = list(builder.calls[0].permissions)
        floor = builder.calls[0].memory_backend.default

        assert _check_fs_permission(rules, "write", f"{_ATTACHED}/notes.md") == "deny"
        assert _check_fs_permission(rules, "write", f"{_UNGRANTED}/x") == "deny"
        # `.copilot` is matcher-blind, so the deny rule cannot see it at all —
        # the floor is the only thing standing between a read_only grant and a
        # writable scratch directory inside it.
        assert floor.permits_write(f"{_ATTACHED}/.copilot/scratch.md") is False
        assert floor.permits_write(f"{_ATTACHED}/notes.md") is False

    async def test_a_writable_grant_gets_scratch_and_nothing_more(
        self, monkeypatch: pytest.MonkeyPatch, fake_dependencies: RuntimeDependencies
    ) -> None:
        """The one direct host write there is, and its exact extent."""

        from deepagents.middleware.filesystem import _check_fs_permission

        self._broker_env(monkeypatch)
        handler, _store = _handler(
            sessions=self._bound_sessions(), broker=_attach(mode="read_write")
        )
        backend = await self._enforce_backend(handler, _run())
        roots = await handler._granted_host_roots_for_run(backend)

        builder = CapturingAgentBuilder()
        await acreate_agent_runtime(
            context=_context(),
            dependencies=fake_dependencies.model_copy(
                update={"workspace_backend": backend, "granted_host_roots": roots}
            ),
            agent_builder=builder,
        )
        rules = list(builder.calls[0].permissions)
        floor = builder.calls[0].memory_backend.default

        assert (
            _check_fs_permission(rules, "write", f"{_ATTACHED}/.copilot/notes.md")
            == "allow"
        )
        assert floor.permits_write(f"{_ATTACHED}/.copilot/notes.md") is True
        # The user's own content stays on the staged lane even here.
        assert _check_fs_permission(rules, "write", f"{_ATTACHED}/notes.md") == "deny"
        assert floor.permits_write(f"{_ATTACHED}/notes.md") is False

    async def test_a_rollout_denied_run_still_reads_what_the_user_attached(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The tombstone gates workspace EFFECTS, never host reads.

        A run the E2 cohort does not admit resumes into ``WorkspaceTombstoneBackend``
        so it can stage nothing. That says nothing about whether a folder the user
        attached is readable — and the host rules are composed for that run either
        way, so leaving the roots out would only mean it kept asking.
        """

        self._broker_env(monkeypatch)
        handler, _store = _handler(sessions=None, broker=_attach())
        run = _run()
        services = handler._build_mcp_operation_gateway_services(run)
        assert services is not None
        backend = await handler._workspace_backend_for_run(
            _command(), run=run, mcp_gateway_services=services
        )
        assert isinstance(backend, WorkspaceTombstoneBackend)

        roots = await handler._granted_host_roots_for_run(backend)
        assert [root.path for root in roots or ()] == [_ATTACHED]

    async def test_an_unreachable_broker_keeps_every_folder_asking(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A dropped grant degrades toward ASK, never toward open."""

        self._broker_env(monkeypatch)

        def _refuse(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("broker is down")

        wiring = WorkspaceBackendWorkerWiring(
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(_refuse))
        )
        with caplog.at_level(logging.WARNING):
            assert await wiring.granted_host_roots() == ()
        assert "workspace_backend.granted_roots_unavailable" in caplog.text

    async def test_no_broker_config_resolves_nothing_and_says_nothing(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Off the desktop path this is not a fault, so it is not a warning."""

        monkeypatch.delenv("DESKTOP_WORKSPACE_BROKER_URL", raising=False)
        monkeypatch.delenv("DESKTOP_WORKSPACE_BROKER_TOKEN", raising=False)
        monkeypatch.delenv("DESKTOP_BROKER_URL", raising=False)
        monkeypatch.delenv("DESKTOP_BROKER_TOKEN", raising=False)
        broker = _attach()

        with caplog.at_level(logging.WARNING):
            roots = await WorkspaceBackendWorkerWiring(
                http_client=httpx.AsyncClient(transport=broker.transport())
            ).granted_host_roots()

        assert roots == ()
        assert broker.requests == []
        assert "granted_roots_unavailable" not in caplog.text

    async def test_a_revoked_grant_is_not_an_attached_folder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Detaching a folder must take its ``allow`` rule with it."""

        self._broker_env(monkeypatch)
        broker = _attach()
        broker.grant_meta["grant-projects"]["status"] = "revoked"

        roots = await WorkspaceBackendWorkerWiring(
            http_client=httpx.AsyncClient(transport=broker.transport())
        ).granted_host_roots()

        assert roots == ()

    async def test_an_older_broker_that_sends_no_root_keeps_asking(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Degraded, not broken: no root on the wire means no ``allow`` rule."""

        self._broker_env(monkeypatch)
        broker = _attach()
        del broker.grant_meta["grant-projects"]["root"]

        roots = await WorkspaceBackendWorkerWiring(
            http_client=httpx.AsyncClient(transport=broker.transport())
        ).granted_host_roots()

        assert roots == ()

    async def test_the_compatibility_lane_asks_the_broker_exactly_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The non-enforce lane already holds this run's snapshot; reuse it.

        Its ``BrokeredWorkspaceBackend`` was built FROM the grant snapshot and
        exposes ``granted_roots``, so resolving roots must read that rather than
        take a second, independently-timed snapshot of the same fact.
        """

        self._broker_env(monkeypatch)
        broker = _attach()
        handler, _store = _handler(
            sessions=None,
            broker=broker,
            settings=RuntimeSettings.load(environ={"WORKSPACE_EFFECT_MODE": "off"}),
        )
        assert (
            handler.settings.execution.workspace_effect_mode is OperationGatewayMode.OFF
        )

        backend = await handler._workspace_backend_for_run(
            _command(), run=_run(), mcp_gateway_services=None
        )
        roots = await handler._granted_host_roots_for_run(backend)

        assert [root.path for root in roots or ()] == [_ATTACHED]
        snapshots = [
            route for route, _, _ in broker.requests if route == "/v1/grants/snapshot"
        ]
        assert len(snapshots) == 1

    async def test_the_private_write_bootstrap_is_still_path_free(self) -> None:
        """The design constraint this fix deliberately routed around.

        Putting the root on the host-session grant would have been the shortest
        edit. That envelope is C2's private WRITE bootstrap, re-tightened in
        01fb6df2 after the root reversal accidentally routed it through
        ``toBrokerGrant``; the read path does not use host sessions at all. So
        the roots come off ``/v1/grants/snapshot`` instead, and this stays shut.
        """

        from agent_runtime.capabilities.desktop.broker_client import (
            BrokerProtocolError,
            WorkspaceHostSessionGrant,
            _assert_host_session_wire_is_private,
        )

        assert "root" not in WorkspaceHostSessionGrant.model_fields
        with pytest.raises(BrokerProtocolError):
            _assert_host_session_wire_is_private(
                {
                    "host_session_ref": f"whs_{'x' * 43}",
                    "expires_at": 1,
                    "grants": [
                        {
                            "grantId": "grant-projects",
                            "mount": "mnt_projects",
                            "mode": "read_only",
                            "label": "Projects",
                            "status": "active",
                            "root": _ATTACHED,
                        }
                    ],
                }
            )


class TestResumeKeepsAttachedFolders:
    """An approval must not restart the asking it just ended.

    A resume rebuilds the agent, which rebuilds the rule set. In ENFORCE it
    resumes into a tombstone, so without a broker-sourced resolution the resumed
    turn would go back to prompting for a folder the user had attached — mid
    conversation, right after they approved something.
    """

    async def test_resume_resolves_the_same_attached_folder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DESKTOP_WORKSPACE_BROKER_URL", TEST_BASE_URL)
        monkeypatch.setenv("DESKTOP_WORKSPACE_BROKER_TOKEN", TEST_TOKEN)
        broker = _attach()
        store = InMemoryRuntimeApiStore()
        handler = RuntimeApprovalHandler(
            persistence=store,
            event_store=store,
            settings=_settings(),
            workspace_broker_http_client=httpx.AsyncClient(
                transport=broker.transport()
            ),
        )

        backend = await handler._workspace_backend_for_resume(_run())
        assert isinstance(backend, WorkspaceTombstoneBackend)

        roots = await handler._granted_host_roots_for_resume(backend)
        assert [root.path for root in roots or ()] == [_ATTACHED]

    async def test_resume_off_the_desktop_path_resolves_nothing(self) -> None:
        """No workspace object at all ⇒ no host rules ⇒ nothing to resolve."""

        store = InMemoryRuntimeApiStore()
        handler = RuntimeApprovalHandler(
            persistence=store, event_store=store, settings=_settings()
        )

        assert await handler._granted_host_roots_for_resume(None) is None

    assert _workspace_write_permissions(False, effect_staged=True) == ()
    assert _workspace_write_permissions(True) == ()

    instructions = _instructions_with_workspace(
        instructions="BASE",
        workspace_active=True,
        workspace_writable=True,
        workspace_effect_staging=True,
    )
    assert WORKSPACE_STAGED_WRITE_GUIDANCE in instructions
    assert "do NOT immediately modify the host file" in instructions
