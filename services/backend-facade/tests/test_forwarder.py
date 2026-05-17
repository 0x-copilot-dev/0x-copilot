"""W0.2 — `_forward_json` HTTP-aware no-content handling + unified `forward_json`.

Bug 2 in the W0 QA report: facade returned HTTP 500 on `DELETE
/v1/agent/conversations/{cid}` because `_forward_json` always called
`response.json()` on the upstream reply, which raises `JSONDecodeError`
on a 204-with-empty-body response. This test pins the corrected behavior:
2xx no-content → ``{}`` (or ``None`` when ``expect_json=False``).
"""

from __future__ import annotations

import asyncio

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


def _client(transport: httpx.MockTransport) -> httpx.AsyncClient:
    """Build an httpx.AsyncClient that routes through the canned transport.

    Replaces the previous ``monkeypatch.setattr("backend_facade.app.httpx.AsyncClient", ...)``
    pattern: now ``_forward_json`` takes the client as an explicit parameter
    (single source of truth lives in ``HttpClientPool``), and tests inject a
    transport-backed client directly. Cleaner — no module-internal patching,
    no ordering hazards between fixture and ``create_app()``.
    """

    return httpx.AsyncClient(transport=transport)


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

    result = asyncio.run(
        _forward_json(
            client=_client(transport),
            base_url="http://upstream.test",
            method="DELETE",
            path="/v1/anything",
            expect_json=True,
        )
    )
    assert result == {}


def test_no_content_returns_none_when_expect_json_false(
    mock_transport_factory,
) -> None:
    transport = mock_transport_factory(204, b"")

    result = asyncio.run(
        _forward_json(
            client=_client(transport),
            base_url="http://upstream.test",
            method="DELETE",
            path="/v1/anything",
            expect_json=False,
        )
    )
    assert result is None


def test_normal_json_path_unchanged(mock_transport_factory) -> None:
    transport = mock_transport_factory(
        200,
        b'{"hello":"world"}',
        {"content-type": "application/json"},
    )

    result = asyncio.run(
        _forward_json(
            client=_client(transport),
            base_url="http://upstream.test",
            method="GET",
            path="/v1/anything",
        )
    )
    assert result == {"hello": "world"}


def test_json_array_allowed_when_object_check_disabled(
    mock_transport_factory,
) -> None:
    transport = mock_transport_factory(
        200,
        b'[{"conversation_id":"conv_1","total":42}]',
        {"content-type": "application/json"},
    )

    result = asyncio.run(
        _forward_json(
            client=_client(transport),
            base_url="http://upstream.test",
            method="GET",
            path="/v1/anything",
            expect_object=False,
        )
    )

    assert result == [{"conversation_id": "conv_1", "total": 42}]


def test_upstream_error_still_raises(mock_transport_factory) -> None:
    transport = mock_transport_factory(404, b'{"detail":"missing"}')

    with pytest.raises(Exception) as exc_info:
        asyncio.run(
            _forward_json(
                client=_client(transport),
                base_url="http://upstream.test",
                method="GET",
                path="/v1/anything",
            )
        )
    # FastAPI HTTPException
    assert getattr(exc_info.value, "status_code", None) == 404


def test_forward_json_target_routes_to_correct_upstream() -> None:
    """``target="backend"`` and ``target="ai_backend"`` resolve different base URLs."""

    from backend_facade.app import create_app
    from backend_facade.auth import AuthenticatedIdentity

    app = create_app()
    captured: list[str] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        return httpx.Response(204)

    transport = httpx.MockTransport(_handler)
    # Swap the lifespan-owned pool for one that routes through the mock
    # transport. This is the production-shaped substitution path — the
    # pool field on app.state is the seam.
    app.state.http_client = _client(transport)

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
