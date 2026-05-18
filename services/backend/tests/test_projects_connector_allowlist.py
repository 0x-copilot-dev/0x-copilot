"""Tests for ``Project.default_connector_allowlist`` (Phase 6.5 §5).

Covers:

* Create with allowlist → field round-trips.
* Create without allowlist → ``null`` (inherit owner default semantics).
* Create with empty list → explicit deny.
* PATCH owner-only.
* PATCH to ``null`` clears.
* Allowlist values normalized to lowercase + deduped.
* Slug too long / non-string entries → 400.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore
from backend_app.projects.store import InMemoryProjectsStore


def _seeded_identity() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.create_organization(
        OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
    )
    for user_id in ("usr_sarah", "usr_bob"):
        store.create_user(
            UserRecord(
                user_id=user_id,
                org_id="org_acme",
                primary_email=f"{user_id}@acme.com",
                display_name=user_id,
            )
        )
    return store


def _client() -> tuple[TestClient, InMemoryProjectsStore]:
    store = InMemoryProjectsStore()
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=_seeded_identity(),
        projects_store=store,
    )
    return TestClient(app), store


def _q(user: str = "usr_sarah") -> dict[str, str]:
    return {"org_id": "org_acme", "user_id": user}


class TestCreate:
    def test_null_by_default(self) -> None:
        client, _ = _client()
        response = client.post(
            "/v1/projects",
            params=_q(),
            json={"name": "P1", "icon_emoji": "🚀", "color_hue": 200},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["default_connector_allowlist"] is None

    def test_with_allowlist(self) -> None:
        client, store = _client()
        response = client.post(
            "/v1/projects",
            params=_q(),
            json={
                "name": "P2",
                "icon_emoji": "🚀",
                "color_hue": 200,
                "default_connector_allowlist": ["salesforce", "gmail"],
            },
        )
        assert response.status_code == 201
        assert response.json()["default_connector_allowlist"] == [
            "salesforce",
            "gmail",
        ]
        # Underlying store has the same value.
        pid = response.json()["id"]
        record = store.get_project(tenant_id="org_acme", project_id=pid)
        assert record is not None
        assert record.default_connector_allowlist == ["salesforce", "gmail"]

    def test_empty_list_explicit_deny(self) -> None:
        client, _ = _client()
        response = client.post(
            "/v1/projects",
            params=_q(),
            json={
                "name": "P3",
                "icon_emoji": "🚀",
                "color_hue": 200,
                "default_connector_allowlist": [],
            },
        )
        assert response.status_code == 201
        assert response.json()["default_connector_allowlist"] == []

    def test_normalization_lowercase_and_dedup(self) -> None:
        client, _ = _client()
        response = client.post(
            "/v1/projects",
            params=_q(),
            json={
                "name": "P4",
                "icon_emoji": "🚀",
                "color_hue": 200,
                "default_connector_allowlist": [
                    "Salesforce",
                    "salesforce",
                    "GMAIL",
                ],
            },
        )
        assert response.status_code == 201
        assert response.json()["default_connector_allowlist"] == [
            "salesforce",
            "gmail",
        ]

    def test_invalid_entry_400(self) -> None:
        client, _ = _client()
        response = client.post(
            "/v1/projects",
            params=_q(),
            json={
                "name": "P5",
                "icon_emoji": "🚀",
                "color_hue": 200,
                "default_connector_allowlist": ["a" * 65],
            },
        )
        assert response.status_code == 400


class TestPatch:
    def test_owner_can_update(self) -> None:
        client, _ = _client()
        post = client.post(
            "/v1/projects",
            params=_q(),
            json={"name": "P", "icon_emoji": "🚀", "color_hue": 200},
        )
        pid = post.json()["id"]
        response = client.patch(
            f"/v1/projects/{pid}",
            params=_q(),
            json={"default_connector_allowlist": ["jira"]},
        )
        assert response.status_code == 200
        assert response.json()["default_connector_allowlist"] == ["jira"]

    def test_non_owner_cannot_update(self) -> None:
        client, _ = _client()
        post = client.post(
            "/v1/projects",
            params=_q(),
            json={"name": "P", "icon_emoji": "🚀", "color_hue": 200},
        )
        pid = post.json()["id"]
        # Add bob as editor.
        client.post(
            f"/v1/projects/{pid}/members",
            params=_q(),
            json={"user_id": "usr_bob", "role": "editor"},
        )
        # Bob tries to mutate.
        response = client.patch(
            f"/v1/projects/{pid}",
            params=_q(user="usr_bob"),
            json={"default_connector_allowlist": ["jira"]},
        )
        assert response.status_code == 403

    def test_patch_to_null_clears(self) -> None:
        client, _ = _client()
        post = client.post(
            "/v1/projects",
            params=_q(),
            json={
                "name": "P",
                "icon_emoji": "🚀",
                "color_hue": 200,
                "default_connector_allowlist": ["jira"],
            },
        )
        pid = post.json()["id"]
        response = client.patch(
            f"/v1/projects/{pid}",
            params=_q(),
            json={"default_connector_allowlist": None},
        )
        assert response.status_code == 200
        assert response.json()["default_connector_allowlist"] is None
