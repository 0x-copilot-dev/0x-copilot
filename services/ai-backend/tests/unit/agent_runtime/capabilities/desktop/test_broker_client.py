"""Unit tests for :class:`DesktopBrokerClient` — the ai-backend → broker HTTP client.

Covers: each read op round-trips + parses (camelCase aliases); broker error
codes → typed exceptions; envelope/unknown codes → protocol error; transport
failures → unavailable; malformed / oversized bodies → protocol error; auth
headers present; and that the token + virtual path never appear in logs.
"""

from __future__ import annotations

import base64
import logging

import httpx
import pytest

from agent_runtime.capabilities.desktop.broker_client import (
    BrokerClientConfig,
    BrokerGrantRequiredError,
    BrokerInvalidPathError,
    BrokerInvalidRequestError,
    BrokerNotADirectoryError,
    BrokerNotAFileError,
    BrokerNotFoundError,
    BrokerPermissionDeniedError,
    BrokerProtocolError,
    BrokerTooLargeError,
    BrokerUnavailableError,
    BrokerUnsupportedError,
    DesktopBrokerClient,
)
from tests.unit.agent_runtime.capabilities.desktop.fakes import (
    TEST_BASE_URL,
    TEST_PROTOCOL,
    TEST_TOKEN,
    FakeBrokerFs,
    RecordingBroker,
)


class BrokerClientMixin:
    """Builders for a client over a canned single-response transport."""

    HOST_PATH = "/Users/victim/Secret/passwords.txt"

    @staticmethod
    def client_returning(
        response: httpx.Response,
        *,
        max_response_bytes: int = 16 * 1024 * 1024,
    ) -> tuple[DesktopBrokerClient, list[httpx.Request]]:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return response

        client = DesktopBrokerClient(
            BrokerClientConfig(
                base_url=TEST_BASE_URL,
                token=TEST_TOKEN,
                protocol_version=TEST_PROTOCOL,
                max_response_bytes=max_response_bytes,
            ),
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        return client, seen

    @staticmethod
    def client_raising(exc: Exception) -> DesktopBrokerClient:
        def handler(request: httpx.Request) -> httpx.Response:
            raise exc

        return DesktopBrokerClient(
            BrokerClientConfig(
                base_url=TEST_BASE_URL, token=TEST_TOKEN, protocol_version=TEST_PROTOCOL
            ),
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    @staticmethod
    def single_fs_broker() -> RecordingBroker:
        return RecordingBroker(
            grants={
                "grant-1": FakeBrokerFs(
                    files={
                        "notes.txt": b"alpha\nbeta TODO\ngamma\n",
                        "src/app.py": b"print('hi')\n# TODO refactor\n",
                    }
                )
            }
        )


class TestBrokerClientRoundTrips(BrokerClientMixin):
    """Each read op reaches the right route and parses the typed result."""

    async def test_stat_parses_camelcase_alias(self) -> None:
        broker = self.single_fs_broker()
        result = await broker.client().stat("grant-1", "notes.txt")
        assert result.type == "file"
        assert result.name == "notes.txt"
        assert result.mtime_ms == 1000.0  # parsed from `mtimeMs`
        route, _headers, body = broker.requests[-1]
        assert route == "/v1/fs/stat"
        assert body == {"grant_id": "grant-1", "path": "notes.txt"}

    async def test_list_parses_entries(self) -> None:
        broker = self.single_fs_broker()
        result = await broker.client().list("grant-1", "")
        names = {(e.name, e.type) for e in result.entries}
        assert ("notes.txt", "file") in names
        assert ("src", "dir") in names
        assert result.truncated is False

    async def test_read_parses_bytes_read_alias(self) -> None:
        broker = self.single_fs_broker()
        result = await broker.client().read("grant-1", "notes.txt", max_bytes=5)
        assert base64.b64decode(result.base64) == b"alpha"
        assert result.bytes_read == 5  # parsed from `bytesRead`
        assert result.truncated is True
        assert broker.requests[-1][2] == {
            "grant_id": "grant-1",
            "path": "notes.txt",
            "max_bytes": 5,
        }

    async def test_glob_parses_paths(self) -> None:
        broker = self.single_fs_broker()
        result = await broker.client().glob("grant-1", "**/*.py")
        assert result.paths == ("src/app.py",)
        assert result.scanned == 2

    async def test_grep_parses_hits_and_defaults_literal(self) -> None:
        broker = self.single_fs_broker()
        result = await broker.client().grep("grant-1", "TODO")
        hit_paths = {(h.path, h.line) for h in result.hits}
        assert ("notes.txt", 2) in hit_paths
        assert result.files_scanned == 2  # parsed from `filesScanned`
        # Literal search is the default: no `is_regex` on the wire.
        assert "is_regex" not in broker.requests[-1][2]

    async def test_grants_snapshot_parses_path_free_projection(self) -> None:
        broker = RecordingBroker(
            grants={"grant-1": FakeBrokerFs(files={"a.txt": b"x\n"})},
            grant_meta={
                "grant-1": {
                    "mode": "read_write_no_delete",
                    "label": "My Notes",
                    "status": "active",
                    "mount": "mnt_abc123",
                }
            },
        )
        snapshot = await broker.client().grants_snapshot()
        assert snapshot.snapshot_id == "snap-fake"  # parsed from `snapshotId`
        assert snapshot.captured_at == 1000  # parsed from `capturedAt`
        assert len(snapshot.grants) == 1
        grant = snapshot.grants[0]
        assert grant.grant_id == "grant-1"  # parsed from `grantId`
        assert grant.mode == "read_write_no_delete"
        assert grant.label == "My Notes"
        assert grant.status == "active"
        assert grant.mount == "mnt_abc123"
        route, headers, body = broker.requests[-1]
        assert route == "/v1/grants/snapshot"
        assert body == {}  # empty request body
        # Auth + protocol headers ride the same transport as the fs ops.
        assert headers["authorization"] == f"Bearer {TEST_TOKEN}"
        assert headers["x-capability-protocol"] == TEST_PROTOCOL


class TestBrokerClientErrorMapping(BrokerClientMixin):
    """Broker `{error: code}` bodies map to the matching typed exception."""

    @pytest.mark.parametrize(
        ("status", "code", "expected"),
        [
            (403, "grant_required", BrokerGrantRequiredError),
            (400, "invalid_path", BrokerInvalidPathError),
            (400, "invalid_request", BrokerInvalidRequestError),
            (403, "permission_denied", BrokerPermissionDeniedError),
            (404, "not_found", BrokerNotFoundError),
            (400, "not_a_directory", BrokerNotADirectoryError),
            (400, "not_a_file", BrokerNotAFileError),
            (413, "too_large", BrokerTooLargeError),
            (404, "unsupported", BrokerUnsupportedError),
        ],
    )
    async def test_fs_code_maps_to_typed_exception(
        self, status: int, code: str, expected: type[Exception]
    ) -> None:
        client, _ = self.client_returning(httpx.Response(status, json={"error": code}))
        with pytest.raises(expected) as excinfo:
            await client.stat("grant-1", "x")
        assert excinfo.value.code == code

    @pytest.mark.parametrize(
        ("status", "code"),
        [
            (401, "unauthorized"),
            (403, "forbidden"),
            (400, "unsupported_protocol_version"),
            (413, "payload_too_large"),
            (500, "internal"),
            (418, "some_unknown_code"),
        ],
    )
    async def test_envelope_or_unknown_code_is_protocol_error(
        self, status: int, code: str
    ) -> None:
        client, _ = self.client_returning(httpx.Response(status, json={"error": code}))
        with pytest.raises(BrokerProtocolError):
            await client.stat("grant-1", "x")

    async def test_error_message_never_leaks_broker_code_as_path(self) -> None:
        client, _ = self.client_returning(
            httpx.Response(404, json={"error": "not_found"})
        )
        with pytest.raises(BrokerNotFoundError) as excinfo:
            await client.read("grant-1", "x")
        # Safe, generic message — no host path, no token.
        assert self.HOST_PATH not in str(excinfo.value)
        assert TEST_TOKEN not in str(excinfo.value)


class TestBrokerClientTransport(BrokerClientMixin):
    """Transport / malformed / oversized failures collapse to typed errors."""

    async def test_connect_error_is_unavailable(self) -> None:
        client = self.client_raising(httpx.ConnectError("connection refused"))
        with pytest.raises(BrokerUnavailableError):
            await client.stat("grant-1", "x")

    async def test_timeout_is_unavailable(self) -> None:
        client = self.client_raising(httpx.ConnectTimeout("timed out"))
        with pytest.raises(BrokerUnavailableError):
            await client.stat("grant-1", "x")

    async def test_malformed_json_is_protocol_error(self) -> None:
        client, _ = self.client_returning(httpx.Response(200, content=b"not json{{"))
        with pytest.raises(BrokerProtocolError):
            await client.stat("grant-1", "x")

    async def test_non_object_body_is_protocol_error(self) -> None:
        client, _ = self.client_returning(httpx.Response(200, json=[1, 2, 3]))
        with pytest.raises(BrokerProtocolError):
            await client.stat("grant-1", "x")

    async def test_oversized_response_is_protocol_error(self) -> None:
        big = {"name": "x" * 1024, "type": "file", "size": 0, "mtimeMs": 0}
        client, _ = self.client_returning(
            httpx.Response(200, json=big), max_response_bytes=16
        )
        with pytest.raises(BrokerProtocolError):
            await client.stat("grant-1", "x")


class TestBrokerClientAuthAndRedaction(BrokerClientMixin):
    """Auth headers are present; token and path never appear in logs."""

    async def test_auth_headers_present_on_every_request(self) -> None:
        broker = self.single_fs_broker()
        await broker.client().list("grant-1", "src")
        _route, headers, body = broker.requests[-1]
        assert headers["authorization"] == f"Bearer {TEST_TOKEN}"
        assert headers["x-capability-protocol"] == TEST_PROTOCOL
        # The request carries a virtual (grant-relative) path, never a host path.
        assert body["path"] == "src"
        assert not body["path"].startswith("/")

    async def test_token_and_path_absent_from_logs_on_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client, _ = self.client_returning(
            httpx.Response(403, json={"error": "permission_denied"})
        )
        with (
            caplog.at_level(
                logging.DEBUG, logger="agent_runtime.capabilities.desktop.broker_client"
            ),
            pytest.raises(BrokerPermissionDeniedError),
        ):
            await client.read("grant-secret", self.HOST_PATH)
        combined = "\n".join(record.getMessage() for record in caplog.records)
        # Something was logged (route/status/code), but never the token or path.
        assert TEST_TOKEN not in combined
        assert self.HOST_PATH not in combined
        assert "grant-secret" not in combined
