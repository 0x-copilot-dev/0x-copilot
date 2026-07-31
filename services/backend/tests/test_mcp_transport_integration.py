"""Regression coverage for bounded pooled-MCP transport behavior."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import pytest
from backend_app.contracts import (
    InternalMcpRpcRequest,
    McpAuthMode,
    McpAuthState,
    McpServerHealth,
    McpServerRecord,
    McpTransport,
    TokenEnvelope,
    UpdateMcpServerRequest,
)
from backend_app.mcp_session_pool import (
    McpSessionPool,
    McpSessionPoolConfig,
    McpSessionPoolOutcome,
    VerifiedMcpSessionScopeKey,
)
from backend_app.service import McpRegistryService
from backend_app.store import InMemoryMcpStore


@dataclass
class _Transport:
    closed: int = 0
    keepalives: int = 0

    def close(self) -> None:
        self.closed += 1

    def keepalive(self) -> None:
        self.keepalives += 1


class _Factory:
    def __init__(self) -> None:
        self.transports: list[_Transport] = []

    def connect(self, _scope: VerifiedMcpSessionScopeKey) -> _Transport:
        transport = _Transport()
        self.transports.append(transport)
        return transport


@dataclass
class _Recorder:
    phases: list[tuple[str, str]]
    counts: list[tuple[str, int, str]]

    def __init__(self) -> None:
        self.phases = []
        self.counts = []

    def record_phase(
        self, *, phase: str, outcome: str, duration_seconds: float
    ) -> None:
        assert duration_seconds >= 0
        self.phases.append((phase, outcome))

    def record_count(self, *, measure: str, value: int, outcome: str) -> None:
        self.counts.append((measure, value, outcome))

    def record_pool_size(self, *, state: str, value: int) -> None:
        return


def _scope(user: str = "user") -> VerifiedMcpSessionScopeKey:
    return VerifiedMcpSessionScopeKey.from_verified_credential_reference(
        org_id="org",
        profile_partition="backend-registry-compat-v1",
        user_id=user,
        server_id="server",
        credential_reference="vault-ref",
        auth_epoch="auth-epoch",
        transport_revision="transport-revision",
        session_scope="internal-rpc",
    )


def test_pool_metrics_distinguish_open_reuse_saturation_and_keepalive() -> None:
    factory = _Factory()
    pool = McpSessionPool(
        factory=factory,
        config=McpSessionPoolConfig(max_total_sessions=1, max_sessions_per_key=1),
    )
    scope = _scope()
    first = pool.acquire(scope)
    assert first.lease is not None
    assert pool.acquire(scope).outcome is McpSessionPoolOutcome.SATURATED
    assert pool.release(first.lease, scope=scope) is McpSessionPoolOutcome.RELEASED
    second = pool.acquire(scope)
    assert second.lease is not None
    assert pool.release(second.lease, scope=scope) is McpSessionPoolOutcome.RELEASED
    assert pool.keepalive_idle() is McpSessionPoolOutcome.RELEASED

    diagnostics = pool.diagnostics()
    assert diagnostics.opened_sessions == 1
    assert diagnostics.reused_sessions == 1
    assert diagnostics.saturated_acquires == 1
    assert diagnostics.keepalive_attempts == 1


def test_session_pool_reuse_backout_is_resolved_strictly_at_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_SESSION_POOL_REUSE_ENABLED", "off")
    assert (
        McpRegistryService._session_pool_config_from_environment().reuse_enabled
        is False
    )

    monkeypatch.setenv("MCP_SESSION_POOL_REUSE_ENABLED", "invalid")
    with pytest.raises(ValueError, match="MCP_SESSION_POOL_REUSE_ENABLED"):
        McpRegistryService._session_pool_config_from_environment()


class _Vault:
    def __init__(self) -> None:
        self.decrypt_calls = 0

    def encrypt(self, plaintext: str) -> str:
        return f"enc:{plaintext}"

    def decrypt(self, ciphertext: str) -> str:
        self.decrypt_calls += 1
        return ciphertext.removeprefix("enc:")

    def key_id_for(self, _ciphertext: str) -> str:
        return "test-key"


class _Response:
    headers = {"content-type": "application/json"}

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, size: int = -1) -> bytes:
        return json.dumps(self.payload).encode()[:size]


class _Remote:
    def __init__(self, responses: list[dict[str, object] | Exception]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def __call__(self, request, timeout):
        self.calls.append(json.loads(request.data))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return _Response(response)


def _service(
    *,
    health: McpServerHealth = McpServerHealth.HEALTHY,
    oauth: bool = False,
    diagnostics: _Recorder | None = None,
):
    store = InMemoryMcpStore()
    vault = _Vault()
    service = McpRegistryService(
        store=store, token_vault=vault, mcp_diagnostics=diagnostics
    )
    record = McpServerRecord(
        org_id="org",
        user_id="user",
        name="server",
        display_name="Server",
        url="https://mcp.invalid/rpc",
        transport=McpTransport.HTTP,
        auth_mode=McpAuthMode.OAUTH2 if oauth else McpAuthMode.NONE,
        auth_state=McpAuthState.AUTHENTICATED,
        health=health,
    )
    store.create_server(record)
    if oauth:
        store.put_token(
            TokenEnvelope(
                server_id=record.server_id,
                org_id="org",
                user_id="user",
                encrypted_access_token="enc:token",
            )
        )
    return service, store, vault, record


def _rpc(service: McpRegistryService, record: McpServerRecord, lease: str, payload):
    return service.proxy_internal_rpc(
        org_id="org",
        user_id="user",
        server_id=record.server_id,
        request=InternalMcpRpcRequest(
            org_id="org", user_id="user", lease=lease, payload=payload
        ),
    )


@pytest.mark.parametrize(
    "org_id,user_id,server_id",
    [("other", "user", None), ("org", "attacker", None), ("org", "user", "other")],
)
def test_wrong_owner_rejected_before_token_decrypt_or_dispatch(
    monkeypatch, org_id, user_id, server_id
) -> None:
    service, _store, vault, record = _service(oauth=True)
    remote = _Remote([])
    monkeypatch.setattr("backend_app.mcp_transport.urlopen", remote)
    lease = service.create_internal_client_session(
        org_id="org", user_id="user", server_id=record.server_id
    ).lease
    before = vault.decrypt_calls
    with pytest.raises(ValueError):
        service.proxy_internal_rpc(
            org_id=org_id,
            user_id=user_id,
            server_id=server_id or record.server_id,
            request=InternalMcpRpcRequest(
                org_id=org_id,
                user_id=user_id,
                lease=lease,
                payload={"jsonrpc": "2.0", "method": "tools/list"},
            ),
        )
    assert vault.decrypt_calls == before
    assert remote.calls == []


def test_auth_rotation_closes_old_lease_without_capacity_leak() -> None:
    service, _store, _vault, record = _service(oauth=True)
    lease = service.create_internal_client_session(
        org_id="org", user_id="user", server_id=record.server_id
    ).lease
    old_transport = next(iter(service.session_pool._sessions.values())).transport
    service.update_server(
        org_id="org",
        user_id="user",
        server_id=record.server_id,
        request=UpdateMcpServerRequest(display_name="Rotated config"),
    )
    assert service.session_pool_diagnostics()["active_leases"] == 0
    assert old_transport._closed
    with pytest.raises(ValueError):
        service.release_internal_client_session(
            org_id="org",
            user_id="user",
            server_id=record.server_id,
            lease_token=lease,
            cancel=False,
        )
    fresh = service.create_internal_client_session(
        org_id="org", user_id="user", server_id=record.server_id
    )
    assert fresh.lease != lease
    assert service.session_pool_diagnostics()["opened_sessions"] == 2


def test_complete_paginated_tools_and_resources_publishes_exactly_once(
    monkeypatch,
) -> None:
    service, _store, _vault, record = _service()
    remote = _Remote(
        [
            {"result": {"tools": [{"name": "a"}], "nextCursor": "t2"}},
            {"result": {"tools": [{"name": "b"}]}},
            {"result": {"resources": [{"uri": "r1"}]}},
        ]
    )
    monkeypatch.setattr("backend_app.mcp_transport.urlopen", remote)
    lease = service.create_internal_client_session(
        org_id="org", user_id="user", server_id=record.server_id
    ).lease
    for payload in (
        {"jsonrpc": "2.0", "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "method": "tools/list", "params": {"cursor": "t2"}},
        {"jsonrpc": "2.0", "method": "resources/list", "params": {}},
    ):
        _rpc(service, record, lease, payload)
    revision = service.revision_authority.get_current(
        org_id="org", user_id="user", server_id=record.server_id
    )
    assert revision is not None
    assert (revision.tool_count, revision.resource_count) == (2, 1)
    feed = service.revision_authority.feed(
        org_id="org", user_id="user", after_cursor=None, limit=10
    )
    assert len(feed.notices) == 1


class _CapturingHandler(logging.Handler):
    """Collect records straight off a named logger.

    The service's structured logging does not propagate to root once the app
    has been built, so ``caplog`` can see nothing depending on which tests ran
    first. Attaching to the logger asserts on what the service really emits.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class TestPostDispatchObservationCannotDiscardAGoodResult:
    """Descriptor bookkeeping runs after the connector already answered.

    The incident: a ``SELECT *`` row splatted into an ``extra="forbid"``
    contract raised ``ValidationError`` inside the post-dispatch descriptor
    observation. ``ValidationError`` is a ``ValueError``, and the RPC route
    maps ``ValueError`` to ``400``, so a *successful* Linear round-trip —
    52 tools already discovered and returned — was converted into a client
    error that killed ``load_mcp_server`` at ``resources/list``. The runtime
    then reported it as "the MCP server could not be reached", and neither
    service logged a line naming the real cause.

    The projection bug itself is fixed. This pins the structural half: a
    bookkeeping failure must degrade revision tracking, never discard a
    result the connector already produced.
    """

    @staticmethod
    def _observation_failure(monkeypatch, exc: Exception) -> None:
        def boom(*_args, **_kwargs):
            raise exc

        monkeypatch.setattr(
            McpRegistryService, "_observe_proxied_descriptor_page", boom
        )

    def test_a_failing_observation_still_returns_the_connector_payload(
        self, monkeypatch
    ) -> None:
        service, _store, _vault, record = _service()
        remote = _Remote([{"result": {"tools": [{"name": "a"}]}}])
        monkeypatch.setattr("backend_app.mcp_transport.urlopen", remote)
        lease = service.create_internal_client_session(
            org_id="org", user_id="user", server_id=record.server_id
        ).lease
        # Exactly the live failure: a Pydantic ValidationError is a ValueError.
        self._observation_failure(
            monkeypatch, ValueError("2 validation errors for McpDescriptorRevision")
        )

        response = _rpc(
            service,
            record,
            lease,
            {"jsonrpc": "2.0", "method": "tools/list", "params": {}},
        )

        # Was: ValueError out of the service -> HTTP 400 -> the runtime's
        # "could not be reached", for a call the connector answered fine.
        assert response.payload == {"result": {"tools": [{"name": "a"}]}}

    def test_a_failing_observation_is_logged_not_swallowed(self, monkeypatch) -> None:
        # Failing open is only defensible if it is loud. Silently continuing
        # would hide the next projection bug for as long as this one hid.
        service, _store, _vault, record = _service()
        remote = _Remote([{"result": {"tools": [{"name": "a"}]}}])
        monkeypatch.setattr("backend_app.mcp_transport.urlopen", remote)
        lease = service.create_internal_client_session(
            org_id="org", user_id="user", server_id=record.server_id
        ).lease
        self._observation_failure(monkeypatch, ValueError("projection exploded"))

        # Capture on the logger itself: once any test has built the app, the
        # service's structured logging stops propagating to root and
        # ``caplog`` would silently see nothing, making this assertion pass or
        # fail on test order rather than on behaviour.
        handler = _CapturingHandler()
        service_logger = logging.getLogger("backend_app.service")
        service_logger.addHandler(handler)
        try:
            _rpc(
                service,
                record,
                lease,
                {"jsonrpc": "2.0", "method": "tools/list", "params": {}},
            )
        finally:
            service_logger.removeHandler(handler)

        records = [r for r in handler.records if r.levelno >= logging.ERROR]
        assert records, "a swallowed observation failure left no trace"
        message = "\n".join(r.getMessage() for r in records)
        assert "tools/list" in message
        assert record.server_id in message
        assert any(r.exc_info for r in records)

    def test_an_unexpected_error_type_is_contained_too(self, monkeypatch) -> None:
        # The live bug arrived as a ValueError, but the reason to contain it
        # is that it is *bookkeeping*, not that it was that class.
        service, _store, _vault, record = _service()
        remote = _Remote([{"result": {"tools": [{"name": "a"}]}}])
        monkeypatch.setattr("backend_app.mcp_transport.urlopen", remote)
        lease = service.create_internal_client_session(
            org_id="org", user_id="user", server_id=record.server_id
        ).lease
        self._observation_failure(monkeypatch, KeyError("descriptor_digest"))

        response = _rpc(
            service,
            record,
            lease,
            {"jsonrpc": "2.0", "method": "tools/list", "params": {}},
        )
        assert response.payload == {"result": {"tools": [{"name": "a"}]}}


def test_descriptor_diagnostics_record_paging_bytes_and_rejected_admission(
    monkeypatch,
) -> None:
    recorder = _Recorder()
    service, _store, _vault, record = _service(diagnostics=recorder)
    monkeypatch.setattr(
        "backend_app.mcp_transport.urlopen",
        _Remote(
            [
                {"result": {"tools": [{"name": "a"}], "nextCursor": "next"}},
                {"result": {"tools": [{"name": "b"}]}},
                {"result": {"resources": []}},
            ]
        ),
    )
    lease = service.create_internal_client_session(
        org_id="org", user_id="user", server_id=record.server_id
    ).lease
    for payload in (
        {"jsonrpc": "2.0", "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "method": "tools/list", "params": {"cursor": "next"}},
        {"jsonrpc": "2.0", "method": "resources/list", "params": {}},
    ):
        _rpc(service, record, lease, payload)

    assert recorder.counts.count(("descriptor_pages", 1, "tools")) == 2
    assert ("descriptor_pages", 1, "resources") in recorder.counts
    assert any(measure == "descriptor_bytes" for measure, _, _ in recorder.counts)
    assert ("descriptor_validation", "admitted") in recorder.phases
    assert ("revision_publication", "published") in recorder.phases

    rejected = _Recorder()
    bad_service, _store, _vault, bad_record = _service(diagnostics=rejected)
    monkeypatch.setattr(
        "backend_app.mcp_transport.urlopen",
        _Remote([{"result": {"tools": "not-a-list"}}]),
    )
    bad_lease = bad_service.create_internal_client_session(
        org_id="org", user_id="user", server_id=bad_record.server_id
    ).lease
    _rpc(
        bad_service,
        bad_record,
        bad_lease,
        {"jsonrpc": "2.0", "method": "tools/list", "params": {}},
    )
    assert ("descriptor_validation", "rejected") in rejected.phases


@pytest.mark.parametrize(
    "responses,payloads",
    [
        (
            [{"result": {"tools": [], "nextCursor": "x"}}],
            [{"jsonrpc": "2.0", "method": "tools/list", "params": {}}],
        ),
        (
            [{"result": {"tools": [], "nextCursor": "x"}}, {"result": {"tools": []}}],
            [
                {"jsonrpc": "2.0", "method": "tools/list", "params": {}},
                {
                    "jsonrpc": "2.0",
                    "method": "tools/list",
                    "params": {"cursor": "wrong"},
                },
            ],
        ),
        (
            [{"error": {"code": -1}}],
            [{"jsonrpc": "2.0", "method": "tools/list", "params": {}}],
        ),
        (
            [{"result": {"tools": [{"description": "x" * (4 * 1024 * 1024)}]}}],
            [{"jsonrpc": "2.0", "method": "tools/list", "params": {}}],
        ),
        (
            [{"result": {"tools": "bad"}}],
            [{"jsonrpc": "2.0", "method": "tools/list", "params": {}}],
        ),
        (
            [ConnectionError("down")],
            [{"jsonrpc": "2.0", "method": "tools/list", "params": {}}],
        ),
    ],
)
def test_incomplete_or_invalid_observation_never_publishes(
    monkeypatch, responses, payloads
) -> None:
    service, _store, _vault, record = _service()
    monkeypatch.setattr("backend_app.mcp_transport.urlopen", _Remote(responses))
    lease = service.create_internal_client_session(
        org_id="org", user_id="user", server_id=record.server_id
    ).lease
    for payload in payloads:
        try:
            _rpc(service, record, lease, payload)
        except (ConnectionError, ValueError):
            pass
    assert (
        service.revision_authority.get_current(
            org_id="org", user_id="user", server_id=record.server_id
        )
        is None
    )


def test_resources_method_unsupported_completes_empty(monkeypatch) -> None:
    service, _store, _vault, record = _service()
    monkeypatch.setattr(
        "backend_app.mcp_transport.urlopen",
        _Remote(
            [
                {"result": {"tools": [{"name": "a"}]}},
                {"error": {"code": -32601, "message": "not found"}},
            ]
        ),
    )
    lease = service.create_internal_client_session(
        org_id="org", user_id="user", server_id=record.server_id
    ).lease
    _rpc(
        service, record, lease, {"jsonrpc": "2.0", "method": "tools/list", "params": {}}
    )
    _rpc(
        service,
        record,
        lease,
        {"jsonrpc": "2.0", "method": "resources/list", "params": {}},
    )
    revision = service.revision_authority.get_current(
        org_id="org", user_id="user", server_id=record.server_id
    )
    assert revision is not None and revision.resource_count == 0


@pytest.mark.parametrize(
    "limit_name,responses,payloads",
    [
        (
            "_MAX_DESCRIPTOR_PAGES",
            [{"result": {"tools": [], "nextCursor": "x"}}, {"result": {"tools": []}}],
            [
                {"jsonrpc": "2.0", "method": "tools/list", "params": {}},
                {"jsonrpc": "2.0", "method": "tools/list", "params": {"cursor": "x"}},
            ],
        ),
        (
            "_MAX_DESCRIPTOR_COUNT",
            [{"result": {"tools": [{"name": "a"}, {"name": "b"}]}}],
            [{"jsonrpc": "2.0", "method": "tools/list", "params": {}}],
        ),
        (
            "_MAX_DESCRIPTOR_BYTES",
            [{"result": {"tools": [{"description": "too-large"}]}}],
            [{"jsonrpc": "2.0", "method": "tools/list", "params": {}}],
        ),
    ],
)
def test_observation_page_count_and_canonical_byte_caps_reset(
    monkeypatch, limit_name, responses, payloads
) -> None:
    monkeypatch.setattr(f"backend_app.service.{limit_name}", 1)
    service, _store, _vault, record = _service()
    monkeypatch.setattr("backend_app.mcp_transport.urlopen", _Remote(responses))
    lease = service.create_internal_client_session(
        org_id="org", user_id="user", server_id=record.server_id
    ).lease
    for payload in payloads:
        _rpc(service, record, lease, payload)
    assert (
        service.revision_authority.get_current(
            org_id="org", user_id="user", server_id=record.server_id
        )
        is None
    )


def test_degraded_usable_disabled_reenable_and_forced_shutdown() -> None:
    degraded, _store, _vault, record = _service(health=McpServerHealth.DEGRADED)
    lease = degraded.create_internal_client_session(
        org_id="org", user_id="user", server_id=record.server_id
    ).lease
    assert lease
    assert degraded.shutdown_session_pool(timeout_seconds=0) is False
    assert degraded.session_pool_diagnostics()["active_leases"] == 0

    disabled, store, _vault, record = _service(health=McpServerHealth.DISABLED)
    record = store.update_server(record.model_copy(update={"enabled": False}))
    with pytest.raises(ValueError):
        disabled.create_internal_client_session(
            org_id="org", user_id="user", server_id=record.server_id
        )
    enabled = disabled.update_server(
        org_id="org",
        user_id="user",
        server_id=record.server_id,
        request=UpdateMcpServerRequest(enabled=True),
    )
    assert enabled.enabled
    assert disabled.create_internal_client_session(
        org_id="org", user_id="user", server_id=record.server_id
    ).lease
