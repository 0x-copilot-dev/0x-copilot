"""Facade artifact routes preserve streaming bytes and strict headers."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend_facade.app import create_app
from backend_facade.artifact_routes import ArtifactProxy
from backend_facade.auth import AuthenticatedIdentity, FacadeAuthenticator
from backend_facade.settings import FacadeSettings


class _AsyncBytes(httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self._chunks = chunks
        self.yielded = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self._chunks:
            self.yielded += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def _streaming_response(
    status_code: int,
    *,
    headers: dict[str, str],
    content: bytes,
) -> httpx.Response:
    return httpx.Response(
        status_code,
        headers=headers,
        stream=_AsyncBytes(content),
    )


class ArtifactFacadeMixin:
    SECRET = "test-auth-secret"
    ARTIFACT_ID = "art_123e4567-e89b-42d3-a456-426614174000"

    @classmethod
    def bearer(cls) -> str:
        body = json.dumps(
            {
                "org_id": "org_artifacts",
                "user_id": "user_artifacts",
                "roles": ["employee"],
                "permission_scopes": ["runtime:use"],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        payload = base64.urlsafe_b64encode(body).decode().rstrip("=")
        signature = (
            base64.urlsafe_b64encode(
                hmac.new(cls.SECRET.encode(), payload.encode(), hashlib.sha256).digest()
            )
            .decode()
            .rstrip("=")
        )
        return f"Bearer {payload}.{signature}"

    @classmethod
    def client(
        cls,
        monkeypatch: pytest.MonkeyPatch,
        handler,
    ) -> TestClient:
        monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", cls.SECRET)
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "svc-test")
        FacadeAuthenticator.touch_cache().clear()
        app = create_app(
            FacadeSettings(
                backend_url="http://backend.test",
                ai_backend_url="http://ai-backend.test",
                artifact_effects_v2=True,
            )
        )
        app.state.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
        return TestClient(app)


class TestArtifactFacade(ArtifactFacadeMixin):
    def test_missing_bearer_is_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError(f"unexpected upstream call: {request.url}")

        response = self.client(monkeypatch, handler).get(
            f"/v1/agent/artifacts/{self.ARTIFACT_ID}"
        )
        assert response.status_code == 401

    def test_upload_body_and_required_headers_stream_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}
        boundary = "artifact-boundary-42"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="kind"\r\n\r\n'
            "code\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="title"\r\n\r\n'
            "a.ts\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="media_type"\r\n\r\n'
            "text/plain\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="content"; filename="a.ts"\r\n'
            "Content-Type: text/plain\r\n\r\n"
            "exact-upload-bytes\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        split = len(body) // 3
        request_chunks = (body[:split], body[split : split * 2], body[split * 2 :])

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            captured["timeout"] = request.extensions["timeout"]
            chunks = [chunk async for chunk in request.stream]
            captured["request_chunks"] = chunks
            captured["body"] = b"".join(chunks)
            return _streaming_response(
                201,
                headers={"Content-Type": "application/json", "X-Internal": "secret"},
                content=b'{"artifact":{"replayed":false}}',
            )

        app = self.client(monkeypatch, handler).app

        async def exercise() -> httpx.Response:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://facade.test",
            ) as client:
                response = await client.post(
                    "/v1/agent/runs/run_1/artifacts",
                    headers={
                        "authorization": self.bearer(),
                        "Idempotency-Key": "idem_1",
                        "Content-Type": (f"multipart/form-data; boundary={boundary}"),
                    },
                    content=_AsyncBytes(*request_chunks),
                )
            await app.state.http_client.aclose()
            return response

        response = asyncio.run(exercise())

        assert response.status_code == 201
        assert captured["body"] == body
        headers = {key.lower(): value for key, value in captured["headers"].items()}
        assert headers["idempotency-key"] == "idem_1"
        assert headers["accept-encoding"] == "identity"
        assert headers["x-enterprise-org-id"] == "org_artifacts"
        assert headers["x-enterprise-user-id"] == "user_artifacts"
        assert headers["content-type"] == (f"multipart/form-data; boundary={boundary}")
        assert "content-length" not in headers
        assert "x-internal" not in response.headers
        assert captured["timeout"] == {
            "connect": 10.0,
            "read": 900.0,
            "write": 900.0,
            "pool": 30.0,
        }

    def test_proxy_hands_each_incoming_chunk_to_upstream_without_buffering(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        request_chunks = (b"part-one", b"-part-two", b"-part-three")
        captured: list[bytes] = []
        timeout_seen: list[httpx.Timeout] = []

        class IncrementalUpstream:
            def build_request(self, method, url, **kwargs):
                timeout_seen.append(kwargs["timeout"])
                return type(
                    "BuiltRequest",
                    (),
                    {"content_stream": kwargs["content"]},
                )()

            async def send(self, request, *, stream):
                async for chunk in request.content_stream:
                    captured.append(chunk)
                return _streaming_response(
                    201,
                    headers={"Content-Type": "application/json"},
                    content=b"{}",
                )

        async def verify(cls, request, **kwargs):
            return AuthenticatedIdentity(
                org_id="org_artifacts",
                user_id="user_artifacts",
                permission_scopes=("runtime:use",),
            )

        monkeypatch.setattr(
            FacadeAuthenticator,
            "verify_with_touch",
            classmethod(verify),
        )
        app = FastAPI()
        app.state.settings = FacadeSettings(
            backend_url="http://backend.test",
            ai_backend_url="http://ai-backend.test",
        )
        app.state.http_client = IncrementalUpstream()
        messages = [
            {
                "type": "http.request",
                "body": chunk,
                "more_body": index < len(request_chunks) - 1,
            }
            for index, chunk in enumerate(request_chunks)
        ]

        async def receive():
            if messages:
                return messages.pop(0)
            return {"type": "http.request", "body": b"", "more_body": False}

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "scheme": "http",
                "path": "/v1/agent/runs/run_1/artifacts",
                "raw_path": b"/v1/agent/runs/run_1/artifacts",
                "query_string": b"",
                "headers": [
                    (
                        b"content-type",
                        b"multipart/form-data; boundary=incremental",
                    )
                ],
                "client": ("127.0.0.1", 1),
                "server": ("facade.test", 80),
                "app": app,
            },
            receive=receive,
        )

        async def exercise() -> None:
            response = await ArtifactProxy.forward(
                app=app,
                request=request,
                upstream_path="/v1/agent/runs/run_1/artifacts",
            )
            _ = b"".join([chunk async for chunk in response.body_iterator])

        asyncio.run(exercise())
        # Starlette terminates Request.stream() with one empty sentinel.
        assert [chunk for chunk in captured if chunk] == list(request_chunks)
        assert timeout_seen == [ArtifactProxy.UPSTREAM_TIMEOUT]

    def test_download_uses_raw_iterator_and_header_allowlists(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        compressed_like_bytes = b"\x1f\x8b\x08not-decoded-by-the-facade"
        captured: dict[str, object] = {}
        upstream_stream = _AsyncBytes(
            compressed_like_bytes[:5],
            compressed_like_bytes[5:13],
            compressed_like_bytes[13:],
        )

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = dict(request.headers)
            return httpx.Response(
                206,
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(compressed_like_bytes)),
                    "Content-Range": "bytes 0-31/100",
                    "Accept-Ranges": "bytes",
                    "Content-Disposition": 'attachment; filename="a.bin"',
                    "ETag": '"abc"',
                    "X-Content-Type-Options": "nosniff",
                    "X-Storage-Key": "/private/blob",
                },
                stream=upstream_stream,
            )

        response = self.client(monkeypatch, handler).get(
            f"/v1/agent/artifacts/{self.ARTIFACT_ID}/revisions/1/content",
            headers={
                "authorization": self.bearer(),
                "Range": "bytes=0-31",
                "If-Range": '"abc"',
            },
        )

        assert response.status_code == 206
        assert response.content == compressed_like_bytes
        assert upstream_stream.yielded == 3
        upstream_headers = {
            key.lower(): value for key, value in captured["headers"].items()
        }
        assert upstream_headers["accept-encoding"] == "identity"
        assert upstream_headers["range"] == "bytes=0-31"
        assert upstream_headers["if-range"] == '"abc"'
        assert "x-storage-key" not in response.headers
        assert response.headers["x-content-type-options"] == "nosniff"

    def test_downstream_disconnect_closes_upstream_without_draining(self) -> None:
        upstream_stream = _AsyncBytes(b"one", b"two", b"three")
        upstream = httpx.Response(200, stream=upstream_stream)

        class DisconnectAfterFirst:
            def __init__(self) -> None:
                self.checks = 0

            async def is_disconnected(self) -> bool:
                self.checks += 1
                return self.checks > 1

        async def exercise() -> list[bytes]:
            return [
                chunk
                async for chunk in ArtifactProxy._raw_response(
                    request=DisconnectAfterFirst(),  # type: ignore[arg-type]
                    upstream=upstream,
                )
            ]

        assert asyncio.run(exercise()) == [b"one"]
        assert upstream_stream.yielded == 2
        assert upstream_stream.closed is True

    def test_list_forwards_only_supported_query_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["query"] = dict(request.url.params)
            return _streaming_response(
                200,
                headers={"Content-Type": "application/json"},
                content=b'{"artifacts":[]}',
            )

        response = self.client(monkeypatch, handler).get(
            "/v1/agent/runs/run_1/artifacts"
            "?kind=code&limit=20&cursor=next&org_id=attacker",
            headers={"authorization": self.bearer()},
        )

        assert response.status_code == 200
        assert captured["query"] == {
            "kind": "code",
            "limit": "20",
            "cursor": "next",
        }

    def test_promote_literal_and_delete_no_content(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        paths: list[tuple[str, str]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            paths.append((request.method, request.url.path))
            if request.method == "DELETE":
                return httpx.Response(204)
            return _streaming_response(
                201,
                headers={"Content-Type": "application/json"},
                content=b'{"replayed":false}',
            )

        client = self.client(monkeypatch, handler)
        promoted = client.post(
            "/v1/agent/artifacts:promote",
            headers={
                "authorization": self.bearer(),
                "Idempotency-Key": "idem_p",
            },
            json={
                "run_id": "run_1",
                "source_ref": "message://msg_1",
                "kind": "document",
            },
        )
        deleted = client.delete(
            f"/v1/agent/artifacts/{self.ARTIFACT_ID}",
            headers={
                "authorization": self.bearer(),
                "Idempotency-Key": "idem_d",
            },
        )

        assert promoted.status_code == 201
        assert deleted.status_code == 204
        assert paths == [
            ("POST", "/v1/agent/artifacts:promote"),
            ("DELETE", f"/v1/agent/artifacts/{self.ARTIFACT_ID}"),
        ]


class TestArtifactFacadeFeatureGate(ArtifactFacadeMixin):
    @staticmethod
    def _artifact_operation_count(paths: dict[str, object]) -> int:
        methods = {"get", "post", "delete", "put", "patch"}
        return sum(
            1
            for path, operations in paths.items()
            if "/artifacts" in path
            for method in operations
            if method in methods
        )

    @pytest.mark.parametrize("value", [None, "false", "0", "no", "off"])
    def test_shared_setting_defaults_off(
        self,
        monkeypatch: pytest.MonkeyPatch,
        value: str | None,
    ) -> None:
        if value is None:
            monkeypatch.delenv("ARTIFACT_EFFECTS_V2", raising=False)
        else:
            monkeypatch.setenv("ARTIFACT_EFFECTS_V2", value)
        assert FacadeSettings.load().artifact_effects_v2 is False

    @pytest.mark.parametrize("value", ["true", "1", "yes", "on"])
    def test_shared_setting_explicitly_enables_routes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        value: str,
    ) -> None:
        monkeypatch.setenv("ARTIFACT_EFFECTS_V2", value)
        assert FacadeSettings.load().artifact_effects_v2 is True

    def test_feature_off_preserves_route_table_and_returns_404(self) -> None:
        app = create_app(
            FacadeSettings(
                backend_url="http://backend.test",
                ai_backend_url="http://ai-backend.test",
                artifact_effects_v2=False,
            )
        )
        with TestClient(app) as client:
            paths = client.get("/openapi.json").json()["paths"]
            response = client.get(
                f"/v1/agent/artifacts/{self.ARTIFACT_ID}",
                headers={"authorization": self.bearer()},
            )
        assert self._artifact_operation_count(paths) == 0
        assert response.status_code == 404

    def test_feature_on_registers_all_eight_proxy_operations(self) -> None:
        app = create_app(
            FacadeSettings(
                backend_url="http://backend.test",
                ai_backend_url="http://ai-backend.test",
                artifact_effects_v2=True,
            )
        )
        with TestClient(app) as client:
            paths = client.get("/openapi.json").json()["paths"]
        assert self._artifact_operation_count(paths) == 8
