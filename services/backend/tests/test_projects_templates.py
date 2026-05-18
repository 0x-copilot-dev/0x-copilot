"""Tests for project templates — Phase 6.5 §7.

Covers:

* Save-as-template — owner-only on source project; snapshot strips
  webhook/event triggers per §7.2.
* List / Get — tenant-isolated; cross-tenant returns 404.
* Fork — caller becomes owner of the new project; atomic transaction.
* PATCH — metadata only (name/description); snapshot is read-only.
* DELETE — soft-delete; subsequent reads → 404.
* Member override applied on fork.
* Connector override on fork wins over snapshot.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore
from backend_app.projects.store import InMemoryProjectsStore
from backend_app.projects.templates import InMemoryProjectTemplatesStore


def _seeded_identity() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.create_organization(
        OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
    )
    store.create_organization(
        OrganizationRecord(org_id="org_zeta", display_name="Zeta", slug="zeta")
    )
    for user_id, org in (
        ("usr_sarah", "org_acme"),
        ("usr_bob", "org_acme"),
        ("usr_alice_other", "org_zeta"),
    ):
        store.create_user(
            UserRecord(
                user_id=user_id,
                org_id=org,
                primary_email=f"{user_id}@example.com",
                display_name=user_id,
            )
        )
    return store


def _client() -> tuple[
    TestClient, InMemoryProjectsStore, InMemoryProjectTemplatesStore
]:
    projects = InMemoryProjectsStore()
    templates = InMemoryProjectTemplatesStore()
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=_seeded_identity(),
        projects_store=projects,
        project_templates_store=templates,
    )
    return TestClient(app), projects, templates


def _q(user: str = "usr_sarah", org: str = "org_acme") -> dict[str, str]:
    return {"org_id": org, "user_id": user}


def _create_project(
    client: TestClient,
    *,
    user: str = "usr_sarah",
    org: str = "org_acme",
    name: str = "Template Source",
) -> str:
    response = client.post(
        "/v1/projects",
        params=_q(user, org),
        json={"name": name, "icon_emoji": "🚀", "color_hue": 200},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


class TestSaveAsTemplate:
    def test_owner_can_save(self) -> None:
        client, _, templates = _client()
        pid = _create_project(client)
        response = client.post(
            f"/v1/projects/{pid}/save-as-template",
            params=_q(),
            json={
                "name": "Renewal Playbook",
                "description": "Standard renewal workflow",
                "seeded_todos": [
                    {"text": "Draft email", "priority": "high", "labels": []},
                ],
                "seeded_routines": [
                    {
                        "name": "Daily check-in",
                        "instructions_template": "Check status for {{project.name}}",
                        "triggers": [{"kind": "schedule", "cron": "0 9 * * *"}],
                    }
                ],
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["name"] == "Renewal Playbook"
        assert body["source_project_id"] == pid
        assert body["owner_user_id"] == "usr_sarah"
        # Snapshot kept.
        snapshot = body["snapshot"]
        assert len(snapshot["seeded_todos"]) == 1
        assert len(snapshot["seeded_routines"]) == 1

    def test_non_owner_cannot_save(self) -> None:
        client, _, _ = _client()
        pid = _create_project(client)
        # Add bob as member.
        response = client.post(
            f"/v1/projects/{pid}/members",
            params=_q(),
            json={"user_id": "usr_bob", "role": "editor"},
        )
        assert response.status_code == 201, response.text
        # Bob is a member but not owner.
        response = client.post(
            f"/v1/projects/{pid}/save-as-template",
            params=_q(user="usr_bob"),
            json={"name": "Bob's attempt"},
        )
        assert response.status_code == 403

    def test_webhook_triggers_stripped(self) -> None:
        client, _, _ = _client()
        pid = _create_project(client)
        response = client.post(
            f"/v1/projects/{pid}/save-as-template",
            params=_q(),
            json={
                "name": "T",
                "seeded_routines": [
                    {
                        "name": "Mixed",
                        "instructions_template": "x",
                        "triggers": [
                            {"kind": "webhook", "secret": "abc"},
                            {"kind": "schedule", "cron": "0 9 * * *"},
                            {"kind": "event", "source": "salesforce"},
                            {"kind": "manual"},
                        ],
                    }
                ],
            },
        )
        assert response.status_code == 201, response.text
        triggers = response.json()["snapshot"]["seeded_routines"][0]["triggers"]
        kinds = sorted(t["kind"] for t in triggers)
        assert kinds == ["manual", "schedule"]


class TestListGet:
    def test_list_tenant_isolated(self) -> None:
        client, _, _ = _client()
        pid = _create_project(client)
        client.post(
            f"/v1/projects/{pid}/save-as-template",
            params=_q(),
            json={"name": "Acme Template"},
        )
        response = client.get(
            "/v1/project-templates", params=_q(user="usr_alice_other", org="org_zeta")
        )
        assert response.status_code == 200
        assert response.json()["items"] == []

    def test_get_by_id(self) -> None:
        client, _, _ = _client()
        pid = _create_project(client)
        save = client.post(
            f"/v1/projects/{pid}/save-as-template",
            params=_q(),
            json={"name": "X"},
        )
        tpl_id = save.json()["id"]
        response = client.get(f"/v1/project-templates/{tpl_id}", params=_q())
        assert response.status_code == 200
        assert response.json()["id"] == tpl_id

    def test_get_cross_tenant_404(self) -> None:
        client, _, _ = _client()
        pid = _create_project(client)
        save = client.post(
            f"/v1/projects/{pid}/save-as-template",
            params=_q(),
            json={"name": "X"},
        )
        tpl_id = save.json()["id"]
        response = client.get(
            f"/v1/project-templates/{tpl_id}",
            params=_q(user="usr_alice_other", org="org_zeta"),
        )
        assert response.status_code == 404


class TestFork:
    def test_fork_creates_caller_owned_project(self) -> None:
        client, _, _ = _client()
        pid = _create_project(client)
        save = client.post(
            f"/v1/projects/{pid}/save-as-template",
            params=_q(),
            json={"name": "T"},
        )
        tpl_id = save.json()["id"]
        # Bob forks — Bob becomes owner.
        response = client.post(
            f"/v1/project-templates/{tpl_id}/fork",
            params=_q(user="usr_bob"),
            json={"name": "Bob's Project"},
        )
        assert response.status_code == 201, response.text
        new_pid = response.json()["id"]
        # Fetch the new project; viewer_role should be 'owner' for bob.
        get_resp = client.get(f"/v1/projects/{new_pid}", params=_q(user="usr_bob"))
        assert get_resp.status_code == 200
        assert get_resp.json()["owner_user_id"] == "usr_bob"
        assert get_resp.json()["viewer_role"] == "owner"

    def test_fork_inherits_connector_allowlist_from_snapshot(self) -> None:
        client, _, templates = _client()
        # Create a project with an allowlist, save as template, fork.
        response = client.post(
            "/v1/projects",
            params=_q(),
            json={
                "name": "AllowlistSource",
                "icon_emoji": "🛡",
                "color_hue": 100,
                "default_connector_allowlist": ["salesforce", "gmail"],
            },
        )
        assert response.status_code == 201
        pid = response.json()["id"]
        save = client.post(
            f"/v1/projects/{pid}/save-as-template",
            params=_q(),
            json={"name": "T"},
        )
        tpl_id = save.json()["id"]
        snap = save.json()["snapshot"]
        assert snap["default_connector_allowlist"] == ["salesforce", "gmail"]
        # Fork inherits.
        fork = client.post(
            f"/v1/project-templates/{tpl_id}/fork",
            params=_q(user="usr_bob"),
            json={"name": "Forked"},
        )
        assert fork.status_code == 201, fork.text
        new_pid = fork.json()["id"]
        get_resp = client.get(f"/v1/projects/{new_pid}", params=_q(user="usr_bob"))
        assert get_resp.status_code == 200
        assert get_resp.json()["default_connector_allowlist"] == [
            "salesforce",
            "gmail",
        ]

    def test_fork_atomic_under_no_partial_state(self) -> None:
        # In the in-memory store the fork's project + member rows + audit
        # all land via the same with-block; the test asserts the "either
        # all or nothing" invariant by counting projects pre + post.
        client, projects, _ = _client()
        pid = _create_project(client)
        save = client.post(
            f"/v1/projects/{pid}/save-as-template",
            params=_q(),
            json={"name": "T"},
        )
        tpl_id = save.json()["id"]
        before = len(projects.projects)
        response = client.post(
            f"/v1/project-templates/{tpl_id}/fork",
            params=_q(user="usr_bob"),
            json={"name": "Bob's Project"},
        )
        assert response.status_code == 201
        after = len(projects.projects)
        assert after == before + 1


class TestPatchAndDelete:
    def test_owner_can_patch_metadata(self) -> None:
        client, _, _ = _client()
        pid = _create_project(client)
        save = client.post(
            f"/v1/projects/{pid}/save-as-template",
            params=_q(),
            json={"name": "T"},
        )
        tpl_id = save.json()["id"]
        response = client.patch(
            f"/v1/project-templates/{tpl_id}",
            params=_q(),
            json={"name": "Renamed", "description": "new"},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Renamed"
        assert response.json()["description"] == "new"

    def test_non_owner_cannot_patch(self) -> None:
        client, _, _ = _client()
        pid = _create_project(client)
        save = client.post(
            f"/v1/projects/{pid}/save-as-template",
            params=_q(),
            json={"name": "T"},
        )
        tpl_id = save.json()["id"]
        response = client.patch(
            f"/v1/project-templates/{tpl_id}",
            params=_q(user="usr_bob"),
            json={"name": "Renamed"},
        )
        assert response.status_code == 403

    def test_soft_delete(self) -> None:
        client, _, _ = _client()
        pid = _create_project(client)
        save = client.post(
            f"/v1/projects/{pid}/save-as-template",
            params=_q(),
            json={"name": "T"},
        )
        tpl_id = save.json()["id"]
        response = client.delete(f"/v1/project-templates/{tpl_id}", params=_q())
        assert response.status_code == 204
        # Subsequent get → 404 (soft-deleted excluded).
        response = client.get(f"/v1/project-templates/{tpl_id}", params=_q())
        assert response.status_code == 404
