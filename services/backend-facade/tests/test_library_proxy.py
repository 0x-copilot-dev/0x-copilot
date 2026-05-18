"""Tests for the public ``/v1/library`` facade proxy — Phase 7 P7-A1.

Mirrors ``test_projects_proxy.py`` / ``test_inbox_proxy.py`` setup.
Asserts:

* Unauthenticated request rejected (401).
* Authenticated request proxies to backend with the verified identity.
* Multi-value ``filter[kind]`` query params survive the proxy
  (cross-audit §1.5 OR semantics).
* Upstream 4xx propagates through.
* CRUD (list / get / create-page / patch / delete) all reach the right
  backend route.
* PATCH forwards ``If-Match`` so the page body-edit optimistic-
  concurrency contract survives the proxy.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend_facade.app import create_app
from backend_facade.auth import FacadeAuthenticator
from backend_facade.settings import FacadeSettings


_TEST_SECRET = "test-auth-secret"


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


_PAGE_BODY = {
    "id": "libpage_1",
    "tenant_id": "org_acme",
    "owner_user_id": "usr_sarah",
    "project_id": None,
    "kind": "page",
    "title": "Launch checklist",
    "markdown": "# Launch checklist\n",
    "version": 1,
    "version_etag": "deadbeef",
    "source": {"kind": "user_upload", "uploaded_by": "usr_sarah"},
    "tags": [],
    "index_status": "pending",
    "index_error": None,
    "created_at": "2026-05-18T00:00:00+00:00",
    "updated_at": "2026-05-18T00:00:00+00:00",
    "last_accessed_at": None,
}

_LIST_BODY = {
    "items": [_PAGE_BODY],
    "next_cursor": None,
    "counts_by_kind": {"file": 0, "page": 1, "dataset": 0},
}


def _touch_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "session_id": "sid_test",
            "org_id": "org_acme",
            "user_id": "usr_sarah",
            "roles": ["employee"],
            "permission_scopes": ["runtime:use"],
            "connector_scopes": {},
            "mfa_satisfied": False,
            "expires_at": "2099-01-01T00:00:00+00:00",
        },
    )


class TestLibraryProxy:
    def test_unauthenticated_rejected(self) -> None:
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.get("/v1/library")
        assert resp.status_code == 401

    def test_list_proxies_multi_value_filter_kind(self, monkeypatch) -> None:
        captured: list[dict[str, object]] = []

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args, **kwargs):
                return None

            async def post(self, url, *, json=None, headers=None, timeout=None):
                return _touch_response()

            async def get(self, url, *, params, headers, timeout=None):
                captured.append(
                    {
                        "method": "GET",
                        "url": url,
                        "params": list(params),
                        "headers": dict(headers),
                    }
                )
                return httpx.Response(200, json=_LIST_BODY)

        monkeypatch.setattr(
            "backend_facade.http_client.httpx.AsyncClient", _FakeAsyncClient
        )

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.get(
            "/v1/library?filter[kind]=page&filter[kind]=file",
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 200
        assert resp.json() == _LIST_BODY

        get_call = next(c for c in captured if c["method"] == "GET")
        assert get_call["url"].endswith("/v1/library")
        pairs = get_call["params"]
        assert ("org_id", "org_acme") in pairs
        assert ("user_id", "usr_sarah") in pairs
        assert pairs.count(("filter[kind]", "page")) == 1
        assert pairs.count(("filter[kind]", "file")) == 1

        downstream = {k.lower(): v for k, v in get_call["headers"].items()}
        assert downstream["x-enterprise-service-token"] == "test-service-token"
        assert downstream["x-enterprise-org-id"] == "org_acme"
        assert downstream["x-enterprise-user-id"] == "usr_sarah"

    def test_get_single_item_proxies(self, monkeypatch) -> None:
        captured: list[dict[str, object]] = []

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args, **kwargs):
                return None

            async def post(self, url, *, json=None, headers=None, timeout=None):
                return _touch_response()

            async def get(self, url, *, params, headers, timeout=None):
                if url.endswith("/v1/identity/touch"):
                    return _touch_response()
                captured.append({"url": url, "params": dict(params)})
                return httpx.Response(200, json=_PAGE_BODY)

        monkeypatch.setattr(
            "backend_facade.http_client.httpx.AsyncClient", _FakeAsyncClient
        )

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.get("/v1/library/libpage_1", headers=_bearer_headers(monkeypatch))
        assert resp.status_code == 200
        call = captured[0]
        assert call["url"].endswith("/v1/library/libpage_1")
        assert call["params"]["org_id"] == "org_acme"

    def test_create_page_proxies_body(self, monkeypatch) -> None:
        captured: list[dict[str, object]] = []

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args, **kwargs):
                return None

            async def get(self, url, *, params, headers, timeout=None):
                return _touch_response()

            async def post(
                self, url, *, params=None, json=None, headers=None, timeout=None
            ):
                if url.endswith("/v1/identity/touch") or json is None:
                    return _touch_response()
                captured.append(
                    {"url": url, "json": json, "params": dict(params or {})}
                )
                return httpx.Response(201, json=_PAGE_BODY)

        monkeypatch.setattr(
            "backend_facade.http_client.httpx.AsyncClient", _FakeAsyncClient
        )

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.post(
            "/v1/library/pages",
            json={"title": "Launch checklist", "markdown": "# yo"},
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 201, resp.text
        call = captured[0]
        assert call["url"].endswith("/v1/library/pages")
        assert call["json"] == {"title": "Launch checklist", "markdown": "# yo"}
        assert call["params"]["org_id"] == "org_acme"
        assert call["params"]["user_id"] == "usr_sarah"

    def test_patch_forwards_if_match_header(self, monkeypatch) -> None:
        captured: list[dict[str, object]] = []

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args, **kwargs):
                return None

            async def post(self, url, *, json=None, headers=None, timeout=None):
                return _touch_response()

            async def get(self, url, *, params, headers, timeout=None):
                return _touch_response()

            async def patch(
                self, url, *, params, json=None, headers=None, timeout=None
            ):
                captured.append(
                    {
                        "url": url,
                        "json": json,
                        "params": dict(params),
                        "headers": dict(headers),
                    }
                )
                return httpx.Response(
                    200, json={**_PAGE_BODY, "version": 2, "version_etag": "newetag"}
                )

        monkeypatch.setattr(
            "backend_facade.http_client.httpx.AsyncClient", _FakeAsyncClient
        )

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.patch(
            "/v1/library/libpage_1",
            json={"markdown": "# v2"},
            headers={
                **_bearer_headers(monkeypatch),
                "If-Match": "deadbeef",
            },
        )
        assert resp.status_code == 200, resp.text
        call = captured[0]
        assert call["url"].endswith("/v1/library/libpage_1")
        assert call["json"] == {"markdown": "# v2"}
        # If-Match header survives the proxy verbatim.
        forwarded = {k.lower(): v for k, v in call["headers"].items()}
        assert forwarded["if-match"] == "deadbeef"

    def test_delete_proxies(self, monkeypatch) -> None:
        captured: list[dict[str, object]] = []

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args, **kwargs):
                return None

            async def post(self, url, *, json=None, headers=None, timeout=None):
                return _touch_response()

            async def get(self, url, *, params, headers, timeout=None):
                return _touch_response()

            async def delete(self, url, *, params, headers=None, timeout=None):
                captured.append({"url": url, "params": dict(params)})
                return httpx.Response(204)

        monkeypatch.setattr(
            "backend_facade.http_client.httpx.AsyncClient", _FakeAsyncClient
        )

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.delete(
            "/v1/library/libpage_1", headers=_bearer_headers(monkeypatch)
        )
        assert resp.status_code == 204
        assert captured[0]["url"].endswith("/v1/library/libpage_1")

    def test_upstream_404_propagates(self, monkeypatch) -> None:
        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args, **kwargs):
                return None

            async def post(self, url, *, json=None, headers=None, timeout=None):
                return _touch_response()

            async def get(self, url, *, params, headers, timeout=None):
                if url.endswith("/v1/identity/touch"):
                    return _touch_response()
                return httpx.Response(404, json={"detail": "library_item_not_found"})

        monkeypatch.setattr(
            "backend_facade.http_client.httpx.AsyncClient", _FakeAsyncClient
        )

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.get(
            "/v1/library/libpage_ghost",
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 404
        assert "library_item_not_found" in resp.json()["detail"]
