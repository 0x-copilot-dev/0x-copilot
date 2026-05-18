"""Tests for ``/v1/library/search`` — P7.5-A4.

Coverage:

* Happy path: BM25-only strategy (no embeddings client wired).
* Kind filter — only the requested kind comes back.
* Project ACL — non-member, non-admin's results omit project-scoped
  rows (cross-audit §1.3, ``404-not-403`` shape applies; here it means
  "row silently dropped from the result set").
* Tenant isolation — caller in tenant_a never sees tenant_b items.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore
from backend_app.library.store import InMemoryLibraryStore
from backend_app.projects.store import InMemoryProjectsStore


def _seeded_identity() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.create_organization(
        OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
    )
    for user_id, display in (
        ("usr_sarah", "Sarah Chen"),
        ("usr_bob", "Bob"),
        ("usr_carol", "Carol"),
        ("usr_dave_admin", "Dave (admin)"),
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
    store.create_user(
        UserRecord(
            user_id="usr_alice_other",
            org_id="org_zeta",
            primary_email="alice@zeta.com",
            display_name="Alice",
        )
    )
    return store


def _client() -> tuple[TestClient, InMemoryLibraryStore, InMemoryProjectsStore]:
    lib = InMemoryLibraryStore()
    proj = InMemoryProjectsStore()
    identity = _seeded_identity()
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=identity,
        projects_store=proj,
        library_store=lib,
    )
    return TestClient(app), lib, proj


def _q(user: str = "usr_sarah", org: str = "org_acme") -> dict[str, str]:
    return {"org_id": org, "user_id": user}


def _seed_page(
    client: TestClient,
    *,
    title: str,
    markdown: str,
    project_id: str | None = None,
    user: str = "usr_sarah",
    org: str = "org_acme",
) -> str:
    payload = {"title": title, "markdown": markdown}
    if project_id is not None:
        payload["project_id"] = project_id
    resp = client.post("/v1/library/pages", params=_q(user, org), json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_bm25_only_strategy_when_no_embeddings(self) -> None:
        """Default wiring uses NoopEmbeddingsClient → strategy is
        ``bm25_only``. A query that matches one row's title returns
        that row at rank 1 with a positive score."""

        client, _, _ = _client()
        rocket_id = _seed_page(
            client,
            title="Rocket launch checklist",
            markdown="approvals, demo, comms",
        )
        _seed_page(
            client,
            title="Quarterly review",
            markdown="finance recap and projections",
        )

        resp = client.get(
            "/v1/library/search",
            params={**_q(), "q": "rocket launch"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["strategy"] == "bm25_only"
        assert body["total"] >= 1
        assert body["hits"][0]["ref"]["id"] == rocket_id
        assert body["hits"][0]["ref"]["kind"] == "library_page"
        assert "<mark>" in body["hits"][0]["excerpt"].lower()

    def test_empty_query_400(self) -> None:
        client, _, _ = _client()
        # FastAPI's Query(min_length=1) → 422.
        resp = client.get("/v1/library/search", params={**_q(), "q": ""})
        assert resp.status_code in (400, 422)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


class TestKindFilter:
    def test_kind_filter_returns_only_matching_kind(self) -> None:
        client, store, _ = _client()
        page_id = _seed_page(
            client,
            title="Alpha launch",
            markdown="page body referencing alpha",
        )
        # Drop a file that also contains "alpha" in its name.
        from backend_app.library.store import LibraryFileRecord

        file_record = store.insert_file(
            LibraryFileRecord(
                tenant_id="org_acme",
                owner_user_id="usr_sarah",
                file_kind="pdf",
                name="alpha-strategy.pdf",
                mime="application/pdf",
                blob_ref="opaque/blob/alpha",
                source={"kind": "user_upload", "uploaded_by": "usr_sarah"},
            )
        )
        # No filter — both kinds appear.
        resp = client.get("/v1/library/search", params={**_q(), "q": "alpha"})
        kinds = {h["ref"]["kind"] for h in resp.json()["hits"]}
        assert kinds == {"library_page", "library_file"}

        # kind=page only.
        resp = client.get(
            "/v1/library/search",
            params={**_q(), "q": "alpha", "kind": "page"},
        )
        body = resp.json()
        kinds = {h["ref"]["kind"] for h in body["hits"]}
        assert kinds == {"library_page"}
        assert body["hits"][0]["ref"]["id"] == page_id

        # kind=file only.
        resp = client.get(
            "/v1/library/search",
            params={**_q(), "q": "alpha", "kind": "file"},
        )
        body = resp.json()
        kinds = {h["ref"]["kind"] for h in body["hits"]}
        assert kinds == {"library_file"}
        assert body["hits"][0]["ref"]["id"] == file_record.id


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    def test_cross_tenant_query_returns_no_hits(self) -> None:
        client, _, _ = _client()
        _seed_page(
            client,
            title="Acme secret rocket plan",
            markdown="confidential alpha",
        )
        # Alice in org_zeta queries — no hits.
        resp = client.get(
            "/v1/library/search",
            params={
                "org_id": "org_zeta",
                "user_id": "usr_alice_other",
                "q": "rocket",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["hits"] == []
        assert body["total"] == 0


# ---------------------------------------------------------------------------
# Project ACL
# ---------------------------------------------------------------------------


class TestProjectAcl:
    def _seed_project_page(
        self,
        client: TestClient,
    ) -> tuple[str, str]:
        proj = client.post(
            "/v1/projects",
            params=_q(),
            json={"name": "Acme launch", "icon_emoji": "🚀", "color_hue": 210},
        ).json()
        project_id = proj["id"]
        client.post(
            f"/v1/projects/{project_id}/members",
            params=_q(),
            json={"user_id": "usr_bob", "role": "editor"},
        )
        item_id = _seed_page(
            client,
            title="Project alpha rocket",
            markdown="project body content",
            project_id=project_id,
        )
        return project_id, item_id

    def test_owner_sees_their_project_item(self) -> None:
        client, _, _ = _client()
        _, item_id = self._seed_project_page(client)
        resp = client.get(
            "/v1/library/search", params={**_q("usr_sarah"), "q": "rocket"}
        )
        hits = resp.json()["hits"]
        assert any(h["ref"]["id"] == item_id for h in hits)

    def test_project_member_sees_project_item(self) -> None:
        client, _, _ = _client()
        _, item_id = self._seed_project_page(client)
        # Bob is a project member → can read it via the ACL.
        resp = client.get("/v1/library/search", params={**_q("usr_bob"), "q": "rocket"})
        hits = resp.json()["hits"]
        assert any(h["ref"]["id"] == item_id for h in hits)

    def test_non_member_non_admin_does_not_see_project_item(self) -> None:
        client, _, _ = _client()
        _, item_id = self._seed_project_page(client)
        # Carol is neither owner, member, nor admin → row silently
        # filtered (cross-audit §1.3, search-result equivalent of the
        # 404-not-403 single-item rule).
        resp = client.get(
            "/v1/library/search", params={**_q("usr_carol"), "q": "rocket"}
        )
        hits = resp.json()["hits"]
        assert all(h["ref"]["id"] != item_id for h in hits)
