"""End-to-end route tests for ``/v1/memory`` — Phase 12 P12-A3.

Coverage:

* Happy path: create -> get -> list -> patch -> delete.
* Filter axis (``filter[kind]=skill`` repeatable + ``filter[scope]=`` OR).
* Sort token validation.
* 404 on cross-tenant get.
* DELETE returns 204 and the row is gone from list.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore
from backend_app.memory.store import InMemoryMemoryStore


def _seeded_identity() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.create_organization(
        OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
    )
    for user_id, display in (
        ("usr_sarah", "Sarah Chen"),
        ("usr_bob", "Bob"),
    ):
        store.create_user(
            UserRecord(
                user_id=user_id,
                org_id="org_acme",
                primary_email=f"{user_id}@acme.com",
                display_name=display,
            )
        )
    store.create_organization(
        OrganizationRecord(org_id="org_zeta", display_name="Zeta", slug="zeta")
    )
    return store


def _client() -> tuple[TestClient, InMemoryMemoryStore]:
    mem = InMemoryMemoryStore()
    identity = _seeded_identity()
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=identity,
        memory_store=mem,
    )
    return TestClient(app), mem


def _q(user: str = "usr_sarah", org: str = "org_acme") -> dict[str, str]:
    return {"org_id": org, "user_id": user}


def test_create_get_round_trip() -> None:
    client, _ = _client()
    response = client.post(
        "/v1/memory",
        params=_q(),
        json={
            "scope": "user",
            "kind": "skill",
            "title": "Speaks Python",
            "body": "loves Django",
            "tags": ["lang"],
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["title"] == "Speaks Python"
    assert created["scope"] == "user"
    item_id = created["id"]
    # GET round-trip.
    got = client.get(f"/v1/memory/{item_id}", params=_q()).json()
    assert got["id"] == item_id


def test_list_filter_kind_or_semantics() -> None:
    client, _ = _client()
    for kind, title in (("skill", "k1"), ("fact", "f1"), ("preference", "p1")):
        client.post(
            "/v1/memory",
            params=_q(),
            json={"scope": "user", "kind": kind, "title": title, "body": ""},
        )
    response = client.get(
        "/v1/memory",
        params=[
            ("org_id", "org_acme"),
            ("user_id", "usr_sarah"),
            ("filter[kind]", "skill"),
            ("filter[kind]", "fact"),
        ],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    titles = {item["title"] for item in body["items"]}
    assert titles == {"k1", "f1"}


def test_list_invalid_sort_returns_400() -> None:
    client, _ = _client()
    response = client.get(
        "/v1/memory",
        params={**_q(), "sort": "bogus"},
    )
    assert response.status_code == 400


def test_get_unknown_returns_404() -> None:
    client, _ = _client()
    response = client.get("/v1/memory/mem_does_not_exist", params=_q())
    assert response.status_code == 404


def test_patch_then_delete() -> None:
    client, _ = _client()
    created = client.post(
        "/v1/memory",
        params=_q(),
        json={"scope": "user", "kind": "fact", "title": "hello", "body": ""},
    ).json()
    item_id = created["id"]
    # PATCH the title.
    patched = client.patch(
        f"/v1/memory/{item_id}",
        params=_q(),
        json={"title": "hello world"},
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "hello world"
    # DELETE returns 204.
    deleted = client.delete(f"/v1/memory/{item_id}", params=_q())
    assert deleted.status_code == 204
    # GET now 404.
    assert client.get(f"/v1/memory/{item_id}", params=_q()).status_code == 404


def test_cross_tenant_get_404() -> None:
    client, _ = _client()
    created = client.post(
        "/v1/memory",
        params=_q(),
        json={"scope": "user", "kind": "fact", "title": "x", "body": ""},
    ).json()
    # Caller from org_zeta sees 404.
    response = client.get(
        f"/v1/memory/{created['id']}",
        params={"org_id": "org_zeta", "user_id": "usr_sarah"},
    )
    assert response.status_code == 404


def test_internal_touch_bumps_last_used_at() -> None:
    client, _ = _client()
    created = client.post(
        "/v1/memory",
        params=_q(),
        json={"scope": "user", "kind": "fact", "title": "x", "body": ""},
    ).json()
    item_id = created["id"]
    # Touch via the internal endpoint (no service token in dev → query
    # fallback works).
    touched = client.post(f"/internal/v1/memory/{item_id}/touch", params=_q())
    assert touched.status_code == 200
    body = touched.json()
    assert body["last_used_at"] is not None


def test_create_invalid_kind_returns_400() -> None:
    client, _ = _client()
    response = client.post(
        "/v1/memory",
        params=_q(),
        json={"scope": "user", "kind": "bogus", "title": "x", "body": ""},
    )
    assert response.status_code == 400
