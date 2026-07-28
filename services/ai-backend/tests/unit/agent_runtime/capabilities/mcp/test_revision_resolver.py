from __future__ import annotations

import asyncio
from collections.abc import Mapping

import httpx
import pytest
from pydantic import ValidationError

from agent_runtime.capabilities.mcp.revision_resolver import (
    McpDescriptorRevisionResolver,
    RevisionResolveState,
)
from agent_runtime.capabilities.mcp.revision_wire import (
    BackendMcpRevision,
    BackendMcpRevisionClient,
    BackendMcpRevisionCursorExpired,
    BackendMcpRevisionNotFound,
    BackendMcpRevisionNotice,
)
from copilot_service_contracts.headers import ORG_HEADER, USER_HEADER


def _revision(
    server_id: str = "server-a", revision: str = "revision-a"
) -> dict[str, object]:
    return {
        "server_id": server_id,
        "revision": revision,
        "subject_scope_hash": "scope-a",
        "profile_id": "profile-a",
        "config_generation": 1,
        "auth_generation": 2,
        "transport_generation": 3,
        "tool_filter_generation": 4,
        "tool_count": 5,
        "resource_count": 6,
        "descriptor_digest": "digest-a",
        "observed_at": "2026-01-01T00:00:00Z",
        "source": "backend",
    }


def _notice(new_revision: str | None = "revision-b") -> BackendMcpRevisionNotice:
    return BackendMcpRevisionNotice.model_validate(
        {
            "cursor": "cursor-a",
            "notice_id": "notice-a",
            "sequence_no": 1,
            "server_id": "server-a",
            "profile_id": "profile-a",
            "subject_scope_hash": "scope-a",
            "old_revision": "revision-a",
            "new_revision": new_revision,
            "reason": "config_changed",
            "occurred_at": "2026-01-01T00:01:00Z",
        }
    )


class _HttpClient:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = responses
        self.requests: list[Mapping[str, object]] = []

    async def get(self, _url: str, **kwargs: object) -> httpx.Response:
        self.requests.append(kwargs)
        return self.responses.pop(0)


class _Client:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.gate = asyncio.Event()

    async def get_exact(self, **kwargs: str) -> BackendMcpRevision:
        self.calls.append(kwargs["server_id"])
        await self.gate.wait()
        return BackendMcpRevision.model_validate(_revision(kwargs["server_id"]))


def _response(status_code: int, payload: object) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("GET", "http://backend.test/internal"),
    )


@pytest.mark.asyncio
async def test_wire_parses_complete_backend_payloads_and_rejects_extra_fields() -> None:
    client = _HttpClient(
        [
            _response(200, _revision()),
            _response(
                200,
                {
                    "notices": [_notice().model_dump(mode="json")],
                    "next_cursor": "cursor-b",
                },
            ),
        ]
    )
    wire = BackendMcpRevisionClient("http://backend.test", http_client=client)  # type: ignore[arg-type]

    exact = await wire.get_exact(org_id="org-a", user_id="user-a", server_id="server-a")
    feed = await wire.feed(
        org_id="org-a", user_id="user-a", after_cursor=None, limit=100
    )

    assert exact.descriptor_digest == "digest-a"
    assert feed.notices[0].reason.value == "config_changed"
    assert client.requests[0]["headers"] == {ORG_HEADER: "org-a", USER_HEADER: "user-a"}
    assert client.requests[1]["params"] == {
        "org_id": "org-a",
        "user_id": "user-a",
        "after_cursor": None,
        "limit": 100,
    }
    with pytest.raises(ValidationError):
        BackendMcpRevision.model_validate(
            {**_revision(), "descriptor": {"secret": "no"}}
        )
    with pytest.raises(ValidationError):
        BackendMcpRevisionNotice.model_validate(
            {**_notice().model_dump(mode="json"), "descriptor": {"secret": "no"}}
        )
    with pytest.raises(ValidationError):
        BackendMcpRevision.model_validate(
            {**_revision(), "observed_at": "2026-01-01T00:00:00"}
        )
    with pytest.raises(ValueError):
        await wire.get_exact(org_id="org-a", user_id="user-a", server_id="")


@pytest.mark.asyncio
async def test_wire_uses_typed_not_found_and_cursor_expired_errors() -> None:
    client = _HttpClient([_response(404, {}), _response(410, {})])
    wire = BackendMcpRevisionClient("http://backend.test", http_client=client)  # type: ignore[arg-type]

    with pytest.raises(BackendMcpRevisionNotFound):
        await wire.get_exact(org_id="org-a", user_id="user-a", server_id="server-a")
    with pytest.raises(BackendMcpRevisionCursorExpired):
        await wire.feed(org_id="org-a", user_id="user-a", after_cursor="expired")
    with pytest.raises(ValueError):
        await wire.feed(org_id="org-a", user_id="user-a", after_cursor=None, limit=101)


@pytest.mark.asyncio
async def test_resolver_is_single_flight_warm_and_rejects_remapped_response() -> None:
    client = _Client()
    resolver = McpDescriptorRevisionResolver(client, ttl_seconds=10, max_entries=2)
    await resolver.register(
        org_id="org-a", user_id="user-a", server_name="name", server_id="server-a"
    )
    first = asyncio.create_task(
        resolver.resolve(org_id="org-a", user_id="user-a", server_name="name")
    )
    second = asyncio.create_task(
        resolver.resolve(org_id="org-a", user_id="user-a", server_name="name")
    )
    await asyncio.sleep(0)
    await resolver.register(
        org_id="org-a", user_id="user-a", server_name="name", server_id="server-b"
    )
    client.gate.set()

    assert (await first).state is RevisionResolveState.UNAVAILABLE
    assert (await second).state is RevisionResolveState.FRESH
    assert client.calls == ["server-a", "server-b"]
    assert (
        await resolver.resolve(org_id="org-a", user_id="user-a", server_name="name")
    ).state is RevisionResolveState.FRESH
    assert client.calls == ["server-a", "server-b"]


@pytest.mark.asyncio
async def test_notice_only_uses_opaque_revision_equality_and_lru_is_capped() -> None:
    client = _Client()
    client.gate.set()
    resolver = McpDescriptorRevisionResolver(client, ttl_seconds=10, max_entries=1)
    await resolver.register(
        org_id="org-a", user_id="user-a", server_name="one", server_id="server-a"
    )
    assert (
        await resolver.resolve(org_id="org-a", user_id="user-a", server_name="one")
    ).state is RevisionResolveState.FRESH
    await resolver.apply_notice(_notice("revision-a"))
    assert (
        await resolver.resolve(org_id="org-a", user_id="user-a", server_name="one")
    ).state is RevisionResolveState.FRESH
    assert client.calls == ["server-a"]
    await resolver.apply_notice(_notice("not-an-ordered-revision"))
    assert (
        await resolver.resolve(org_id="org-a", user_id="user-a", server_name="one")
    ).state is RevisionResolveState.FRESH
    assert client.calls == ["server-a", "server-a"]
    await resolver.register(
        org_id="org-a", user_id="user-a", server_name="two", server_id="server-b"
    )
    assert len(resolver._entries) == 1


def test_invalid_resolver_bounds() -> None:
    client = _Client()
    with pytest.raises(ValueError):
        McpDescriptorRevisionResolver(client, ttl_seconds=0)
    with pytest.raises(ValueError):
        McpDescriptorRevisionResolver(client, max_entries=0)
