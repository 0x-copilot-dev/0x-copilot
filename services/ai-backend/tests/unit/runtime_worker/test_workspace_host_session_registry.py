"""C3 private desktop host-session bootstrap tests."""

from __future__ import annotations

import json

import httpx

from agent_runtime.capabilities.desktop.broker_client import (
    BrokerClientConfig,
    DesktopBrokerClient,
)
from agent_runtime.effects.executor import EffectExecutionScope
from runtime_adapters.artifact_references import InMemoryArtifactReferenceStore
from runtime_adapters.in_memory.artifact_blob_store import InMemoryArtifactBlobStore
from runtime_adapters.in_memory.artifact_publication import (
    InMemoryArtifactPublicationCoordinator,
)
from runtime_worker.workspace_effect_storage import (
    DesktopWorkspaceHostSessionRegistry,
    desktop_workspace_host_sessions_from_env,
)

_BASE = "http://127.0.0.1:45124"
_TOKEN = "private-worker-token"
_SCOPE = EffectExecutionScope(
    org_id="org_private",
    user_id="user_private",
    conversation_id="conv_private",
    run_id="run_private",
    owner_ref="principal://users/user_private",
)


def _stores() -> tuple[InMemoryArtifactBlobStore, InMemoryArtifactReferenceStore]:
    publication = InMemoryArtifactPublicationCoordinator()
    return InMemoryArtifactBlobStore(publication), InMemoryArtifactReferenceStore(
        publication
    )


def _registry(handler: httpx.MockTransport) -> DesktopWorkspaceHostSessionRegistry:
    blobs, references = _stores()
    return DesktopWorkspaceHostSessionRegistry(
        client=DesktopBrokerClient(
            BrokerClientConfig(base_url=_BASE, token=_TOKEN),
            http_client=httpx.AsyncClient(transport=handler),
        ),
        blobs=blobs,
        references=references,
    )


async def test_registry_bootstraps_only_an_opaque_host_session_and_caches_it() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path == "/internal/workspace/v2/host-sessions"
        assert request.headers["authorization"] == f"Bearer {_TOKEN}"
        assert json.loads((await request.aread()).decode()) == {
            "run_id": _SCOPE.run_id,
            "user_id": _SCOPE.user_id,
        }
        return httpx.Response(
            201,
            json={
                "host_session_ref": f"whs_{'x' * 43}",
                "expires_at": 1_900_000_000_000,
                "grants": [
                    {
                        "grant_id": "grant_private",
                        "mount": "mnt_private",
                        "mode": "read_write",
                        "status": "active",
                    }
                ],
            },
        )

    registry = _registry(httpx.MockTransport(handler))
    first = await registry.get(_SCOPE)
    second = await registry.get(_SCOPE)

    assert first is not None
    assert second is first
    assert calls == 1
    assert first.host_session_ref == f"whs_{'x' * 43}"
    assert first.base_read is None
    assert [(grant.mount_name, grant.grant_id) for grant in first.grants] == [
        ("mnt_private", "grant_private")
    ]
    assert not hasattr(first, "read_capability")
    assert not hasattr(first, "permit")
    assert not hasattr(first, "root")


async def test_registry_fails_closed_when_the_private_host_session_is_unavailable() -> (
    None
):
    registry = _registry(
        httpx.MockTransport(
            lambda _request: httpx.Response(
                403, json={"error": "workspace_capability_denied"}
            )
        )
    )
    assert await registry.get(_SCOPE) is None


async def test_registry_rejects_a_host_session_response_with_sensitive_fields() -> None:
    """A future broker response cannot smuggle a permit into the worker."""

    registry = _registry(
        httpx.MockTransport(
            lambda _request: httpx.Response(
                201,
                json={
                    "host_session_ref": f"whs_{'x' * 43}",
                    "expires_at": 1_900_000_000_000,
                    "grants": [],
                    "permit": "wcp_must_never_cross_this_boundary",
                },
            )
        )
    )

    assert await registry.get(_SCOPE) is None


def test_env_factory_requires_the_dedicated_private_broker_contract() -> None:
    blobs, references = _stores()
    assert (
        desktop_workspace_host_sessions_from_env(
            blobs=blobs,
            references=references,
            env={},
        )
        is None
    )
    assert (
        desktop_workspace_host_sessions_from_env(
            blobs=blobs,
            references=references,
            env={"RUNTIME_ENABLE_DESKTOP_WORKSPACE": "true"},
        )
        is None
    )
    assert isinstance(
        desktop_workspace_host_sessions_from_env(
            blobs=blobs,
            references=references,
            env={
                "RUNTIME_ENABLE_DESKTOP_WORKSPACE": "true",
                "DESKTOP_WORKSPACE_BROKER_URL": _BASE,
                "DESKTOP_WORKSPACE_BROKER_TOKEN": _TOKEN,
                "DESKTOP_WORKSPACE_BROKER_AUDIENCE": "desktop-capability-broker",
            },
        ),
        DesktopWorkspaceHostSessionRegistry,
    )
