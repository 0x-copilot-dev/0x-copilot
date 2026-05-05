"""W0.2 — `_forward_json` HTTP-aware no-content handling + unified `forward_json`.

Bug 2 in the W0 QA report: facade returned HTTP 500 on `DELETE
/v1/agent/conversations/{cid}` because `_forward_json` always called
`response.json()` on the upstream reply, which raises `JSONDecodeError`
on a 204-with-empty-body response. This test pins the corrected behavior:
2xx no-content → ``{}`` (or ``None`` when ``expect_json=False``).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from backend_facade.app import _forward_json, forward_json


@pytest.fixture
def mock_transport_factory():
    """Build an httpx MockTransport that returns a canned response per request."""

    def _factory(
        status_code: int, content: bytes = b"", headers: dict[str, str] | None = None
    ):
        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code, headers=headers or {}, content=content)

        return httpx.MockTransport(_handler)

    return _factory


def _patched_forward(monkeypatch, transport: httpx.MockTransport):
    """Patch httpx.AsyncClient so _forward_json uses the canned transport."""

    real_async_client = httpx.AsyncClient

    class _PatchedClient(real_async_client):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs.setdefault("transport", transport)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("backend_facade.app.httpx.AsyncClient", _PatchedClient)


@pytest.mark.parametrize(
    ("status_code", "content", "headers"),
    [
        (204, b"", {}),
        (200, b"", {"content-length": "0"}),
        (200, b"", {}),  # falsy content even without explicit content-length
    ],
)
def test_no_content_returns_empty_dict(
    monkeypatch, mock_transport_factory, status_code, content, headers
) -> None:
    """2xx no-content responses return ``{}`` when ``expect_json=True``."""

    transport = mock_transport_factory(status_code, content, headers)
    _patched_forward(monkeypatch, transport)

    result = asyncio.run(
        _forward_json(
            base_url="http://upstream.test",
            method="DELETE",
            path="/v1/anything",
            expect_json=True,
        )
    )
    assert result == {}


def test_no_content_returns_none_when_expect_json_false(
    monkeypatch, mock_transport_factory
) -> None:
    transport = mock_transport_factory(204, b"")
    _patched_forward(monkeypatch, transport)

    result = asyncio.run(
        _forward_json(
            base_url="http://upstream.test",
            method="DELETE",
            path="/v1/anything",
            expect_json=False,
        )
    )
    assert result is None


def test_normal_json_path_unchanged(monkeypatch, mock_transport_factory) -> None:
    transport = mock_transport_factory(
        200,
        b'{"hello":"world"}',
        {"content-type": "application/json"},
    )
    _patched_forward(monkeypatch, transport)

    result = asyncio.run(
        _forward_json(
            base_url="http://upstream.test",
            method="GET",
            path="/v1/anything",
        )
    )
    assert result == {"hello": "world"}


def test_upstream_error_still_raises(monkeypatch, mock_transport_factory) -> None:
    transport = mock_transport_factory(404, b'{"detail":"missing"}')
    _patched_forward(monkeypatch, transport)

    with pytest.raises(Exception) as exc_info:
        asyncio.run(
            _forward_json(
                base_url="http://upstream.test",
                method="GET",
                path="/v1/anything",
            )
        )
    # FastAPI HTTPException
    assert getattr(exc_info.value, "status_code", None) == 404


def test_forward_json_target_routes_to_correct_upstream(monkeypatch) -> None:
    """``target="backend"`` and ``target="ai_backend"`` resolve different base URLs."""

    from backend_facade.app import create_app
    from backend_facade.auth import AuthenticatedIdentity

    app = create_app()
    captured: list[str] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        return httpx.Response(204)

    transport = httpx.MockTransport(_handler)
    _patched_forward(monkeypatch, transport)

    identity = AuthenticatedIdentity(org_id="o", user_id="u")

    asyncio.run(
        forward_json(
            app,
            "DELETE",
            "/v1/probe",
            target="backend",
            identity=identity,
        )
    )
    asyncio.run(
        forward_json(
            app,
            "DELETE",
            "/v1/probe",
            target="ai_backend",
            identity=identity,
        )
    )
    # Two distinct base URLs are exercised — the facade routes by target.
    assert len(captured) == 2
    assert captured[0] != captured[1]
