"""C2 wire tests for the private staged workspace authority client."""

from __future__ import annotations

from collections.abc import AsyncIterator
import json

import httpx
import pytest

from agent_runtime.capabilities.desktop.broker_client import (
    BrokerClientConfig,
    DesktopBrokerClient,
    WorkspaceAuthorityDeniedError,
)

BASE = "http://127.0.0.1:45123"
TOKEN = "broker-secret"
DIGEST = "a" * 64


async def _chunks() -> AsyncIterator[bytes]:
    yield b"hel"
    yield b"lo"


def _client(handler: httpx.MockTransport) -> DesktopBrokerClient:
    return DesktopBrokerClient(
        BrokerClientConfig(base_url=BASE, token=TOKEN),
        http_client=httpx.AsyncClient(transport=handler),
    )


class TestWorkspaceAuthorityClient:
    async def test_prepare_upload_commit_are_private_typed_and_streamed(self) -> None:
        seen: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            assert request.headers["authorization"] == f"Bearer {TOKEN}"
            assert request.headers["x-capability-protocol"] == "1"
            if request.url.path == "/internal/workspace/v2/host-sessions":
                assert request.method == "POST"
                assert json.loads((await request.aread()).decode("utf-8")) == {
                    "run_id": "run_1",
                    "user_id": "user_1",
                }
                return httpx.Response(
                    201,
                    json={
                        "host_session_ref": f"whs_{'x' * 43}",
                        "expires_at": 1_900_000_000_000,
                        "grants": [
                            {
                                "grant_id": "grant_1",
                                "mount": "mnt_1",
                                "mode": "read_write",
                                "status": "active",
                            }
                        ],
                    },
                )
            if request.url.path == "/internal/workspace/v2/prepare":
                assert request.method == "POST"
                body = json.loads((await request.aread()).decode("utf-8"))
                assert body["host_session_ref"] == f"whs_{'x' * 43}"
                assert "read_capability" not in body
                assert "root" not in body and "permit" not in body
                return httpx.Response(
                    201,
                    json={
                        "prepared_ref": "workspace-prepared://prepared_1",
                        "expires_at": 1_900_000_000_000,
                        "observed_target_digest": DIGEST,
                        "upload_slots": [
                            {"slot": "slot_1", "digest": "b" * 64, "size": 5}
                        ],
                    },
                )
            if request.url.path.endswith("/content/slot_1"):
                assert request.method == "PUT"
                assert request.headers["content-type"] == "application/octet-stream"
                assert request.headers["x-workspace-upload-final"] == "true"
                assert await request.aread() == b"hello"
                return httpx.Response(200, json={"accepted": True, "sealed": True})
            if request.url.path.endswith("/commit"):
                assert request.method == "POST"
                assert json.loads((await request.aread()).decode("utf-8")) == {
                    "host_session_ref": f"whs_{'x' * 43}"
                }
                return httpx.Response(
                    200,
                    json={
                        "outcome": "applied",
                        "receipt_ref": "workspace-receipt://wcc_1",
                        "result_digest": "c" * 64,
                    },
                )
            raise AssertionError(request.url.path)

        client = _client(httpx.MockTransport(handler))
        host = await client.workspace_host_session(run_id="run_1", user_id="user_1")
        prepared = await client.workspace_prepare(
            host_session_ref=host.host_session_ref,
            change_set={
                "stage_id": "stg_1",
                "revision": 1,
                "decision_ledger_id": "rrun1·7",
                "grant_id": "grant_1",
                "mount": "mnt_1",
                "change_set_digest": DIGEST,
                "target_digest": "c" * 64,
                "proposal_digest": "d" * 64,
                "entries": [],
            },
        )
        await client.workspace_upload(
            prepared_ref=prepared.prepared_ref,
            slot="slot_1",
            content=_chunks(),
            final=True,
        )
        result = await client.workspace_commit(
            prepared_ref=prepared.prepared_ref,
            host_session_ref=host.host_session_ref,
        )
        assert result.outcome == "applied"
        assert result.receipt_ref == "workspace-receipt://wcc_1"
        assert [request.url.path for request in seen] == [
            "/internal/workspace/v2/host-sessions",
            "/internal/workspace/v2/prepare",
            "/internal/workspace/v2/prepared/prepared_1/content/slot_1",
            "/internal/workspace/v2/prepared/prepared_1/commit",
        ]

    @staticmethod
    def _host_session_transport(
        grant: dict[str, object],
    ) -> httpx.MockTransport:
        return httpx.MockTransport(
            lambda _request: httpx.Response(
                201,
                json={
                    "host_session_ref": f"whs_{'x' * 43}",
                    "expires_at": 1_900_000_000_000,
                    "grants": [grant],
                },
            )
        )

    async def test_the_real_broker_wire_shape_is_accepted(self) -> None:
        """The private allowlist must match what Electron actually sends.

        `toHostSessionGrant` emits `grantId`; `grant_id` is only the Pydantic
        alias. Written in field spelling the allowlist matched NO real grant, so
        every live host session failed closed — invisibly, because every fixture
        in this suite spoke the alias. This one speaks the wire.
        """

        client = _client(
            self._host_session_transport(
                {
                    "grantId": "grant_1",
                    "mount": "mnt_1",
                    "mode": "read_write",
                    "label": "Projects",
                    "status": "active",
                }
            )
        )

        host = await client.workspace_host_session(run_id="run_1", user_id="user_1")

        assert [g.grant_id for g in host.grants] == ["grant_1"]

    async def test_a_root_on_the_private_bootstrap_fails_the_session_closed(
        self,
    ) -> None:
        """This channel stays path-free even though `/v1/grants/*` no longer is.

        The root reversal was scoped to the audience that performs the READ. C2's
        private write bootstrap is not that audience, and admitting a root here
        would make the narrow contract this assertion exists to hold negotiable.
        """

        client = _client(
            self._host_session_transport(
                {
                    "grantId": "grant_1",
                    "mount": "mnt_1",
                    "mode": "read_write",
                    "status": "active",
                    "root": "/Users/ada/Projects",
                }
            )
        )

        with pytest.raises(Exception, match="workspace host session response"):
            await client.workspace_host_session(run_id="run_1", user_id="user_1")

    async def test_host_session_rejection_is_typed_and_prepared_uri_is_validated(
        self,
    ) -> None:
        client = _client(
            httpx.MockTransport(
                lambda _request: httpx.Response(
                    403, json={"error": "workspace_permit_denied"}
                )
            )
        )
        with pytest.raises(WorkspaceAuthorityDeniedError):
            await client.workspace_commit(
                prepared_ref="workspace-prepared://prepared_1",
                host_session_ref=f"whs_{'x' * 43}",
            )
        with pytest.raises(Exception, match="workspace prepared reference"):
            await client.workspace_abort(prepared_ref="workspace-prepared:///host/path")
