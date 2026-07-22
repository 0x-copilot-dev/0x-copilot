"""Tests for the ``/v1/local-models/*`` facade proxy (Round 2 + PRD-P8).

Covers auth gating, identity + service-header forwarding to ai-backend for
the JSON routes, byte-for-byte SSE passthrough for the pull stream, and the
PRD-P8 ``POST /runtime/start`` route (auth, faithful 404 propagation,
non-shadowing by the delete wildcard, and audit emission).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from backend_facade.app import create_app
from backend_facade.auth import FacadeAuthenticator
from backend_facade.settings import FacadeSettings

_TEST_SECRET = "test-auth-secret"
_RUNTIME_START = "/v1/local-models/runtime/start"
_AUDIT_LOGGER_NAME = "backend_facade.local_models"


def _hmac_token(payload: dict[str, object], secret: str) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    payload_b64 = base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")
    sig = hmac.new(
        secret.encode(), payload_b64.encode("ascii"), hashlib.sha256
    ).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")
    return f"{payload_b64}.{sig_b64}"


def _bearer_headers(monkeypatch) -> dict[str, str]:
    monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _TEST_SECRET)
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")
    token = _hmac_token(
        {
            "org_id": "org_acme",
            "user_id": "usr_sarah",
            "roles": ["employee"],
            "permission_scopes": ["runtime:use"],
        },
        _TEST_SECRET,
    )
    return {"authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _clear_touch_cache() -> None:
    FacadeAuthenticator.touch_cache().clear()


class _AuditCapture(logging.Handler):
    """Collect records straight off the local-models logger.

    Attached to the child logger rather than root on purpose:
    ``create_app`` calls ``configure_logging()``, which clears root's
    handlers and would drop a root-attached ``caplog`` handler.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def payloads(self, event: str) -> list[dict[str, object]]:
        return [
            payload
            for record in self.records
            if record.name == _AUDIT_LOGGER_NAME
            and isinstance(payload := getattr(record, "log_event", None), dict)
            and payload.get("event") == event
        ]


@pytest.fixture
def audit_capture():
    handler = _AuditCapture()
    logger = logging.getLogger(_AUDIT_LOGGER_NAME)
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


def _install_fake_upstream(
    monkeypatch,
    captured: list[httpx.Request],
    *,
    runtime_start_status: int = 200,
    runtime_start_body: dict[str, object] | None = None,
    runtime_start_raises: Exception | None = None,
):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        path = request.url.path
        if path == "/v1/local-models/runtime/start" and request.method == "POST":
            if runtime_start_raises is not None:
                raise runtime_start_raises
            return httpx.Response(
                runtime_start_status,
                json=runtime_start_body
                if runtime_start_body is not None
                else {
                    "enabled": True,
                    "ollama_running": True,
                    "ollama_version": "0.5.1",
                    "runtime_state": "running",
                    "runtime_managed": True,
                },
            )
        if path == "/v1/local-models/status":
            return httpx.Response(
                200,
                json={
                    "enabled": True,
                    "ollama_running": True,
                    "ollama_version": "0.5.1",
                },
            )
        if path == "/v1/local-models/size":
            return httpx.Response(
                200,
                json={
                    "repo": request.url.params.get("repo"),
                    "quant": request.url.params.get("quant"),
                    "filename": "Model-Q4_K_M.gguf",
                    "size_bytes": 808_000_000,
                },
            )
        if path == "/v1/local-models" and request.method == "GET":
            return httpx.Response(200, json={"models": []})
        if path == "/v1/local-models/pull" and request.method == "GET":
            frame = (
                b"event: local_model_pull\nid: 1\n"
                b'data: {"sequence_no":1,"status":"success","done":true}\n\n'
            )
            return httpx.Response(
                200, content=frame, headers={"content-type": "text/event-stream"}
            )
        if request.method == "DELETE" and path.startswith("/v1/local-models/"):
            return httpx.Response(204)
        return httpx.Response(404)

    class _Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs) -> None:  # noqa: D401
            super().__init__(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Client)


def _client() -> TestClient:
    return TestClient(
        create_app(
            FacadeSettings(
                backend_url="http://backend.local",
                ai_backend_url="http://ai.local",
            )
        )
    )


class TestGating:
    def test_status_unauthenticated_rejected(self) -> None:
        assert _client().get("/v1/local-models/status").status_code == 401

    def test_runtime_start_unauthenticated_rejected(self, monkeypatch) -> None:
        captured: list[httpx.Request] = []
        _install_fake_upstream(monkeypatch, captured)
        assert _client().post(_RUNTIME_START).status_code == 401
        # A rejected caller must never reach the host-side spawn.
        assert captured == []

    def test_runtime_start_rejects_malformed_bearer(self, monkeypatch) -> None:
        monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _TEST_SECRET)
        captured: list[httpx.Request] = []
        _install_fake_upstream(monkeypatch, captured)
        resp = _client().post(
            _RUNTIME_START, headers={"authorization": "Bearer not-a-real-token"}
        )
        assert resp.status_code == 401
        assert captured == []


class TestRuntimeStart:
    def test_returns_upstream_status_body(self, monkeypatch, audit_capture) -> None:
        captured: list[httpx.Request] = []
        _install_fake_upstream(monkeypatch, captured)
        resp = _client().post(_RUNTIME_START, headers=_bearer_headers(monkeypatch))
        assert resp.status_code == 200
        body = resp.json()
        assert body["runtime_state"] == "running"
        assert body["runtime_managed"] is True
        # Back-compat fields (PRD-P8 D3) still ride through untouched.
        assert body["ollama_running"] is True
        upstream = captured[-1]
        assert upstream.method == "POST"
        assert upstream.url.path == "/v1/local-models/runtime/start"
        assert str(upstream.url).startswith("http://ai.local/")
        assert upstream.url.params.get("org_id") == "org_acme"
        assert upstream.url.params.get("user_id") == "usr_sarah"
        headers = {k.lower(): v for k, v in upstream.headers.items()}
        assert headers["x-enterprise-service-token"] == "test-service-token"
        assert headers["x-enterprise-user-id"] == "usr_sarah"

    def test_upstream_404_propagates_as_404(self, monkeypatch) -> None:
        """Feature flag or manage-runtime flag off upstream → 404, not 500."""

        captured: list[httpx.Request] = []
        _install_fake_upstream(
            monkeypatch,
            captured,
            runtime_start_status=404,
            runtime_start_body={"detail": "CONFIGURATION_ERROR"},
        )
        resp = _client().post(_RUNTIME_START, headers=_bearer_headers(monkeypatch))
        assert resp.status_code == 404
        assert resp.json()["detail"] == "CONFIGURATION_ERROR"

    def test_emits_audit_event_on_success(self, monkeypatch, audit_capture) -> None:
        captured: list[httpx.Request] = []
        _install_fake_upstream(monkeypatch, captured)
        resp = _client().post(_RUNTIME_START, headers=_bearer_headers(monkeypatch))
        assert resp.status_code == 200
        events = audit_capture.payloads("local_models.runtime.start")
        assert len(events) == 1
        event = events[0]
        assert event["org_id"] == "org_acme"
        assert event["user_id"] == "usr_sarah"
        assert event["method"] == "POST"
        assert event["route"] == _RUNTIME_START
        assert event["status_code"] == 200
        assert event["level"] == "info"
        assert event["metadata"] == {
            "runtime": "ollama",
            "outcome": "started",
            "runtime_state": "running",
        }

    def test_emits_audit_event_on_upstream_rejection(
        self, monkeypatch, audit_capture
    ) -> None:
        captured: list[httpx.Request] = []
        _install_fake_upstream(
            monkeypatch,
            captured,
            runtime_start_status=404,
            runtime_start_body={"detail": "CONFIGURATION_ERROR"},
        )
        assert (
            _client()
            .post(_RUNTIME_START, headers=_bearer_headers(monkeypatch))
            .status_code
            == 404
        )
        events = audit_capture.payloads("local_models.runtime.start")
        assert len(events) == 1
        assert events[0]["status_code"] == 404
        assert events[0]["level"] == "warning"
        assert events[0]["metadata"]["outcome"] == "rejected"

    def test_audit_event_carries_no_bearer_material(
        self, monkeypatch, audit_capture
    ) -> None:
        captured: list[httpx.Request] = []
        _install_fake_upstream(monkeypatch, captured)
        headers = _bearer_headers(monkeypatch)
        _client().post(_RUNTIME_START, headers=headers)
        serialized = json.dumps(audit_capture.payloads("local_models.runtime.start"))
        assert headers["authorization"].split(" ", 1)[1] not in serialized

    def test_unreachable_upstream_becomes_502_with_safe_message(
        self, monkeypatch, audit_capture
    ) -> None:
        """A dead ai-backend must not surface as an unhandled 500, and the
        safe message must not carry transport/OS detail."""

        captured: list[httpx.Request] = []
        _install_fake_upstream(
            monkeypatch,
            captured,
            runtime_start_raises=httpx.ConnectError(
                "[Errno 61] Connection refused to /var/run/ollama.sock"
            ),
        )
        resp = _client().post(_RUNTIME_START, headers=_bearer_headers(monkeypatch))
        assert resp.status_code == 502
        detail = resp.json()["detail"]
        assert detail == "Local model runtime is unavailable"
        assert "Errno" not in detail and "ollama.sock" not in detail

        events = audit_capture.payloads("local_models.runtime.start")
        assert len(events) == 1
        assert events[0]["status_code"] == 502
        assert events[0]["level"] == "warning"
        # Not "rejected": the facade never learned whether the start happened.
        assert events[0]["metadata"]["outcome"] == "unreachable"
        assert "runtime_state" not in events[0]["metadata"]
        assert "Errno" not in json.dumps(events[0])

    def test_sends_no_request_body_upstream(self, monkeypatch) -> None:
        """Cross-service contract: ai-backend's ``/runtime/start`` takes no
        body. Sending one would 422 the moment upstream declares a model."""

        captured: list[httpx.Request] = []
        _install_fake_upstream(monkeypatch, captured)
        assert (
            _client()
            .post(_RUNTIME_START, headers=_bearer_headers(monkeypatch))
            .status_code
            == 200
        )
        assert captured[-1].content == b""

    def test_not_shadowed_by_delete_wildcard(self, monkeypatch) -> None:
        """``POST .../runtime/start`` must not be eaten by ``{name:path}``."""

        captured: list[httpx.Request] = []
        _install_fake_upstream(monkeypatch, captured)
        # ``create_app`` builds the shared httpx client eagerly, so the fake
        # transport has to be installed before the app is constructed.
        app = create_app(
            FacadeSettings(
                backend_url="http://backend.local",
                ai_backend_url="http://ai.local",
            )
        )
        indexed = [
            (index, route.path)
            for index, route in enumerate(app.routes)
            if getattr(route, "path", "").startswith("/v1/local-models")
        ]
        start_index = next(
            index for index, path in indexed if path == "/v1/local-models/runtime/start"
        )
        wildcard_index = next(index for index, path in indexed if "{name" in path)
        assert start_index < wildcard_index

        # And behaviourally: POST lands on the new route, while the wildcard
        # still owns DELETE for a same-shaped path.
        client = TestClient(app)
        auth = _bearer_headers(monkeypatch)
        assert client.post(_RUNTIME_START, headers=auth).status_code == 200
        assert captured[-1].method == "POST"
        assert client.request("DELETE", _RUNTIME_START, headers=auth).status_code == 204
        assert captured[-1].method == "DELETE"
        assert captured[-1].url.path == "/v1/local-models/runtime/start"


class TestJsonProxy:
    def test_status_forwards_identity_and_service_headers(self, monkeypatch) -> None:
        captured: list[httpx.Request] = []
        _install_fake_upstream(monkeypatch, captured)
        resp = _client().get(
            "/v1/local-models/status", headers=_bearer_headers(monkeypatch)
        )
        assert resp.status_code == 200
        assert resp.json()["ollama_version"] == "0.5.1"
        req = captured[-1]
        assert str(req.url).startswith("http://ai.local/v1/local-models/status")
        assert req.url.params.get("org_id") == "org_acme"
        headers = {k.lower(): v for k, v in req.headers.items()}
        assert headers["x-enterprise-service-token"] == "test-service-token"
        assert headers["x-enterprise-user-id"] == "usr_sarah"

    def test_size_forwards_repo_and_quant(self, monkeypatch) -> None:
        captured: list[httpx.Request] = []
        _install_fake_upstream(monkeypatch, captured)
        resp = _client().get(
            "/v1/local-models/size",
            params={"repo": "acme/Tiny-GGUF", "quant": "Q4_K_M"},
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 200
        assert resp.json()["size_bytes"] == 808_000_000
        assert captured[-1].url.params.get("repo") == "acme/Tiny-GGUF"
        assert captured[-1].url.params.get("quant") == "Q4_K_M"

    def test_delete_returns_204(self, monkeypatch) -> None:
        captured: list[httpx.Request] = []
        _install_fake_upstream(monkeypatch, captured)
        resp = _client().request(
            "DELETE",
            "/v1/local-models/hf.co/acme/Tiny-GGUF:Q4_K_M",
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 204
        assert captured[-1].method == "DELETE"
        assert "Tiny-GGUF:Q4_K_M" in captured[-1].url.path


class TestSseProxy:
    def test_pull_streams_sse_through(self, monkeypatch) -> None:
        captured: list[httpx.Request] = []
        _install_fake_upstream(monkeypatch, captured)
        resp = _client().get(
            "/v1/local-models/pull",
            params={"repo": "acme/Tiny-GGUF", "quant": "Q4_K_M"},
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert "event: local_model_pull" in resp.text
        assert '"done":true' in resp.text
        assert captured[-1].method == "GET"
        assert captured[-1].url.params.get("repo") == "acme/Tiny-GGUF"
