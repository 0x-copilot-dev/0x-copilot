"""LIVE-Postgres tests for :class:`PostgresProjectsStore` (PRD-J FR-J2.1a).

The projects adapter (PR #175/#182) has unit-tested Python paths but its
SQL had never executed against a real Postgres before this suite. Three
things the fake-conn tests cannot prove:

1. migration ``0043_projects`` really applies (DDL is valid, the tables
   exist, the FK edges to ``organizations`` / ``users`` hold, the yoyo
   chain records it),
2. the adapter's SQL round-trips real rows — CRUD + membership + stars +
   counts against genuine JSONB / timestamptz columns, and
3. the hardening is real: ``project_audit_events`` rows are chain-signed
   (seq / prev_hash / signature verify through the shared
   :class:`AuditChainSigner`) and the RLS policies in the migration
   actually block cross-tenant reads for a non-superuser role.

Gated on ``BACKEND_MERGE_TEST_DATABASE_URL`` (shares the merge gate's
disposable cluster + CI job — same convention as
``test_principals_live.py``). Destructive — use a throwaway database.
The store fixture connects as the gate's NON-superuser app role when
``BACKEND_MERGE_TEST_APP_DATABASE_URL`` is set, so every CRUD assertion
also proves the adapter's own RLS session-var stamping satisfies the
policies (superusers would bypass RLS by design). Identity rows (orgs /
users, which the 0043 FKs point at) are provisioned through the real
:class:`PostgresIdentityStore` on the superuser connection.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("BACKEND_MERGE_TEST_DATABASE_URL"),
    reason="Set BACKEND_MERGE_TEST_DATABASE_URL to a disposable Postgres database.",
)


@pytest.fixture(scope="module")
def database_url() -> str:
    return os.environ["BACKEND_MERGE_TEST_DATABASE_URL"]


@pytest.fixture(scope="module")
def app_database_url(database_url: str) -> str:
    """Non-superuser URL when the gate provides one (RLS is then real)."""

    return os.environ.get("BACKEND_MERGE_TEST_APP_DATABASE_URL", database_url)


@pytest.fixture(scope="module")
def migrated(database_url: str) -> list[str]:
    pytest.importorskip("psycopg")
    from backend_app.db.migrate import MigrationRunner

    MigrationRunner.apply(database_url)  # real runner (psycopg3), idempotent
    applied, _pending = MigrationRunner.status(database_url)
    return applied


@pytest.fixture(scope="module")
def admin_pool(migrated: list[str], database_url: str) -> Iterator[Any]:
    """Superuser pool — identity provisioning for the 0043 FK targets."""

    from backend_app.store import PostgresConnectionPool

    resolved = PostgresConnectionPool(database_url)
    try:
        yield resolved
    finally:
        resolved.close()


@pytest.fixture(scope="module")
def pool(migrated: list[str], app_database_url: str) -> Iterator[Any]:
    from backend_app.store import PostgresConnectionPool

    resolved = PostgresConnectionPool(app_database_url)
    try:
        yield resolved
    finally:
        resolved.close()


@pytest.fixture(scope="module")
def store(pool: Any) -> Any:
    from backend_app.projects.store import PostgresProjectsStore

    return PostgresProjectsStore(pool)


@pytest.fixture
def admin_conn(database_url: str, migrated: list[str]) -> Iterator[Any]:
    """Superuser dict-row connection for raw pre/post-condition checks.

    Session timezone pinned to UTC: chain payloads were signed over
    ``datetime.now(timezone.utc)`` values, so a verifier reading ``ts``
    back from timestamptz columns must observe the same UTC offset for
    ``isoformat()`` to recompute the canonical bytes.
    """

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        yield conn


@dataclass(frozen=True)
class Tenant:
    """One provisioned org + the user ids the FK-carrying rows reference."""

    org_id: str
    owner: str
    member: str
    fan: str
    alt_owner: str


@pytest.fixture(scope="module")
def mk_tenant(admin_pool: Any) -> Any:
    """Factory: a real org + users through the production identity store
    (the 0043 FKs point at ``organizations`` / ``users``)."""

    from backend_app.contracts import OrganizationRecord, UserRecord
    from backend_app.identity.store import PostgresIdentityStore

    identity = PostgresIdentityStore(admin_pool)

    def _make(tag: str) -> Tenant:
        suffix = uuid.uuid4().hex[:8]
        org_id = f"org_{tag}_{suffix}"
        identity.create_organization(
            OrganizationRecord(org_id=org_id, display_name=tag, slug=org_id)
        )
        users: dict[str, str] = {}
        for name in ("owner", "member", "fan", "alt"):
            user_id = f"usr_{name}_{suffix}"
            identity.create_user(
                UserRecord(
                    user_id=user_id,
                    org_id=org_id,
                    primary_email=f"{user_id}@x.io",
                    display_name=name,
                )
            )
            users[name] = user_id
        return Tenant(
            org_id=org_id,
            owner=users["owner"],
            member=users["member"],
            fan=users["fan"],
            alt_owner=users["alt"],
        )

    return _make


def _mk_project(tenant: Tenant, **overrides: Any) -> Any:
    from backend_app.projects.store import ProjectRecord

    defaults: dict[str, Any] = {
        "tenant_id": tenant.org_id,
        "owner_user_id": tenant.owner,
        "name": f"Apollo {uuid.uuid4().hex[:6]}",
        "description": "live-gate project — café Δ ünïcode",
        "default_connector_allowlist": ["salesforce", "gmail"],
    }
    defaults.update(overrides)
    return ProjectRecord(**defaults)


class TestMigrationApplies:
    def test_0043_projects_is_applied(self, migrated: list[str]) -> None:
        assert "0043_projects" in migrated

    def test_projects_tables_exist(self, admin_conn: Any) -> None:
        with admin_conn.cursor() as cur:
            cur.execute(
                """
                SELECT tablename FROM pg_tables
                WHERE tablename IN (
                    'projects', 'project_memberships', 'project_stars',
                    'project_activity', 'project_activity_counts',
                    'project_audit_events'
                )
                """
            )
            names = {row["tablename"] for row in cur.fetchall()}
        assert names == {
            "projects",
            "project_memberships",
            "project_stars",
            "project_activity",
            "project_activity_counts",
            "project_audit_events",
        }


class TestProjectCrud:
    def test_insert_get_round_trip(self, store: Any, mk_tenant: Any) -> None:
        tenant = mk_tenant("crud")
        record = _mk_project(tenant)
        store.insert_project(record)
        fetched = store.get_project(tenant_id=tenant.org_id, project_id=record.id)
        assert fetched == record

    def test_get_by_name_is_case_insensitive(self, store: Any, mk_tenant: Any) -> None:
        tenant = mk_tenant("name")
        record = _mk_project(tenant, name="Zeus Orbit")
        store.insert_project(record)
        fetched = store.get_project_by_name(
            tenant_id=tenant.org_id, name="  zeus orbit "
        )
        assert fetched is not None and fetched.id == record.id

    def test_update_round_trip(self, store: Any, mk_tenant: Any) -> None:
        from backend_app.projects.store import _now

        tenant = mk_tenant("upd")
        record = _mk_project(tenant)
        store.insert_project(record)
        updated = record.model_copy(
            update={
                "name": "Renamed",
                "status": "archived",
                "archived_at": _now(),
                "default_connector_allowlist": [],
                "updated_at": _now(),
            }
        )
        store.update_project(updated)
        fetched = store.get_project(tenant_id=tenant.org_id, project_id=record.id)
        assert fetched == updated
        # Explicit-deny allowlist ([]) survives distinctly from NULL.
        assert fetched.default_connector_allowlist == []

    def test_soft_delete_hides_from_default_reads(
        self, store: Any, mk_tenant: Any
    ) -> None:
        tenant = mk_tenant("del")
        record = _mk_project(tenant)
        store.insert_project(record)
        assert store.soft_delete_project(tenant_id=tenant.org_id, project_id=record.id)
        assert store.get_project(tenant_id=tenant.org_id, project_id=record.id) is None
        compliance = store.get_project(
            tenant_id=tenant.org_id, project_id=record.id, include_deleted=True
        )
        assert compliance is not None and compliance.deleted_at is not None
        page, _ = store.list_projects(tenant_id=tenant.org_id)
        assert record.id not in {p.id for p in page}

    def test_list_filters_owner_status_and_q(self, store: Any, mk_tenant: Any) -> None:
        from backend_app.projects.store import _now

        tenant = mk_tenant("list")
        kept = _mk_project(tenant, name="Falcon Search", status="active")
        other_owner = _mk_project(
            tenant, owner_user_id=tenant.alt_owner, name="Falcon B"
        )
        # The 0043 check constraint requires archived_at whenever
        # status='archived' (projects_archived_at_invariant).
        archived = _mk_project(
            tenant, name="Falcon Old", status="archived", archived_at=_now()
        )
        for record in (kept, other_owner, archived):
            store.insert_project(record)

        page, _ = store.list_projects(
            tenant_id=tenant.org_id,
            owner_user_id=tenant.owner,
            statuses=("active",),
            q="falcon",
        )
        assert [p.id for p in page] == [kept.id]

    def test_list_pagination_cursor(self, store: Any, mk_tenant: Any) -> None:
        tenant = mk_tenant("page")
        for i in range(3):
            store.insert_project(_mk_project(tenant, name=f"Page {i}"))
        first, cursor = store.list_projects(
            tenant_id=tenant.org_id, limit=2, sort="name:asc"
        )
        assert len(first) == 2 and cursor is not None
        second, done = store.list_projects(
            tenant_id=tenant.org_id, limit=2, sort="name:asc", cursor=cursor
        )
        assert len(second) == 1 and done is None
        assert {p.id for p in first}.isdisjoint({p.id for p in second})


class TestMembershipsAndStars:
    def test_membership_lifecycle(self, store: Any, mk_tenant: Any) -> None:
        from backend_app.projects.store import ProjectMembershipRecord

        tenant = mk_tenant("mem")
        project = _mk_project(tenant)
        store.insert_project(project)
        membership = ProjectMembershipRecord(
            project_id=project.id,
            user_id=tenant.member,
            tenant_id=tenant.org_id,
            role="editor",
            added_by=tenant.owner,
        )
        store.insert_membership(membership)
        fetched = store.get_membership(
            tenant_id=tenant.org_id, project_id=project.id, user_id=tenant.member
        )
        assert fetched == membership

        updated = store.update_membership_role(
            tenant_id=tenant.org_id,
            project_id=project.id,
            user_id=tenant.member,
            role="viewer",
        )
        assert updated is not None and updated.role == "viewer"

        listed, _ = store.list_memberships_for_project(
            tenant_id=tenant.org_id, project_id=project.id
        )
        assert [m.user_id for m in listed] == [tenant.member]
        for_user = store.list_memberships_for_user(
            tenant_id=tenant.org_id, user_id=tenant.member
        )
        assert [m.project_id for m in for_user] == [project.id]

        # member_user_id filter on list_projects goes through the EXISTS join.
        page, _ = store.list_projects(
            tenant_id=tenant.org_id, member_user_id=tenant.member
        )
        assert [p.id for p in page] == [project.id]

        assert store.delete_membership(
            tenant_id=tenant.org_id, project_id=project.id, user_id=tenant.member
        )
        assert (
            store.get_membership(
                tenant_id=tenant.org_id, project_id=project.id, user_id=tenant.member
            )
            is None
        )

    def test_star_lifecycle_and_filter(self, store: Any, mk_tenant: Any) -> None:
        from backend_app.projects.store import ProjectStarRecord

        tenant = mk_tenant("star")
        project = _mk_project(tenant)
        store.insert_project(project)
        star = ProjectStarRecord(
            tenant_id=tenant.org_id, user_id=tenant.fan, project_id=project.id
        )
        store.upsert_star(star)
        store.upsert_star(star)  # idempotent ON CONFLICT DO NOTHING
        assert store.is_starred(
            tenant_id=tenant.org_id, project_id=project.id, user_id=tenant.fan
        )
        page, _ = store.list_projects(
            tenant_id=tenant.org_id, starred_by_user_id=tenant.fan
        )
        assert [p.id for p in page] == [project.id]
        assert store.delete_star(
            tenant_id=tenant.org_id, project_id=project.id, user_id=tenant.fan
        )
        assert not store.is_starred(
            tenant_id=tenant.org_id, project_id=project.id, user_id=tenant.fan
        )


class TestActivityAndCounts:
    def test_activity_idempotent_on_audit_id(self, store: Any, mk_tenant: Any) -> None:
        from backend_app.projects.store import ProjectActivityRecord

        tenant = mk_tenant("act")
        project = _mk_project(tenant)
        store.insert_project(project)
        activity = ProjectActivityRecord(
            tenant_id=tenant.org_id,
            project_id=project.id,
            audit_id=f"audprj_{uuid.uuid4().hex}",
            actor_user_id=tenant.owner,
            action="project.created",
            kind="project",
            ref_kind="project",
            ref_id=project.id,
            preview="created",
        )
        assert store.append_activity(activity) is not None
        replay = activity.model_copy(update={"id": f"pact_{uuid.uuid4().hex}"})
        assert store.append_activity(replay) is None  # UNIQUE (tenant, audit_id)
        rows, _ = store.list_activity(tenant_id=tenant.org_id, project_id=project.id)
        assert [a.id for a in rows] == [activity.id]

    def test_counts_upsert_round_trip(self, store: Any, mk_tenant: Any) -> None:
        from backend_app.projects.store import ProjectActivityCounts

        tenant = mk_tenant("cnt")
        project = _mk_project(tenant)
        store.insert_project(project)
        assert store.get_counts(tenant_id=tenant.org_id, project_id=project.id) is None
        counts = ProjectActivityCounts(
            tenant_id=tenant.org_id,
            project_id=project.id,
            chats=3,
            todos_open=2,
            todos_done=5,
            inbox_items=1,
            library_items=4,
            routines_active=1,
            members=2,
        )
        store.upsert_counts(counts)
        store.upsert_counts(counts.model_copy(update={"chats": 7}))
        fetched = store.get_counts(tenant_id=tenant.org_id, project_id=project.id)
        assert fetched is not None
        assert fetched.chats == 7 and fetched.members == 2


class TestAuditChainSigned:
    def test_chain_verifies_over_multiple_rows(
        self, store: Any, mk_tenant: Any, admin_conn: Any
    ) -> None:
        from copilot_audit_chain import AuditChainRow, AuditChainSigner

        from backend_app.projects.store import (
            ProjectAuditRecord,
            _project_audit_payload,
        )

        tenant = mk_tenant("chain")
        project = _mk_project(tenant)
        store.insert_project(project)
        records = [
            ProjectAuditRecord(
                tenant_id=tenant.org_id,
                actor_user_id=tenant.owner,
                action=action,
                target_id=project.id,
                after_state={"status": action, "note": "café Δ"},
            )
            for action in ("project.created", "project.updated", "project.archived")
        ]
        for record in records:
            store.append_audit(record)

        with admin_conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM project_audit_events
                WHERE tenant_id = %s ORDER BY seq ASC
                """,
                (tenant.org_id,),
            )
            rows = cur.fetchall()
        assert [row["seq"] for row in rows] == [1, 2, 3]
        assert rows[0]["prev_hash"] is None
        assert bytes(rows[1]["prev_hash"]) == bytes(rows[0]["signature"])
        assert bytes(rows[2]["prev_hash"]) == bytes(rows[1]["signature"])

        by_audit_id = {record.audit_id: record for record in records}
        chain = [
            AuditChainRow(
                seq=int(row["seq"]),
                payload=_project_audit_payload(by_audit_id[row["audit_id"]]),
                prev_hash=(
                    bytes(row["prev_hash"]) if row["prev_hash"] is not None else None
                ),
                signature=bytes(row["signature"]),
                key_version=int(row["key_version"]),
            )
            for row in rows
        ]
        signer = AuditChainSigner.from_env(environment_env_var="BACKEND_ENVIRONMENT")
        assert signer.verify_chain(chain).ok is True

        # Tampering with any signed field breaks verification (no DB write —
        # the in-memory copy proves the signature really covers the payload).
        tampered = list(chain)
        tampered[1] = AuditChainRow(
            seq=tampered[1].seq,
            payload={**tampered[1].payload, "actor_user_id": "usr_attacker"},
            prev_hash=tampered[1].prev_hash,
            signature=tampered[1].signature,
            key_version=tampered[1].key_version,
        )
        result = signer.verify_chain(tampered)
        assert result.ok is False and result.broken_at_seq == 2

    def test_stored_payload_recomputes_from_db_row(
        self, store: Any, mk_tenant: Any, admin_conn: Any
    ) -> None:
        """The DB row alone (no in-memory record) re-verifies — proving the
        JSONB round-trip preserves the signed payload byte-for-byte."""

        from copilot_audit_chain import AuditChainRow, AuditChainSigner

        from backend_app.projects.store import (
            ProjectAuditRecord,
            _project_audit_payload,
            _row_to_audit,
        )

        tenant = mk_tenant("chaindb")
        project = _mk_project(tenant)
        store.insert_project(project)
        store.append_audit(
            ProjectAuditRecord(
                tenant_id=tenant.org_id,
                actor_user_id=tenant.owner,
                action="project.created",
                target_id=project.id,
                before_state=None,
                after_state={"name": project.name, "hue": 210},
                context={"source": "live-gate"},
                correlation_id=f"corr_{uuid.uuid4().hex[:8]}",
            )
        )
        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM project_audit_events WHERE tenant_id = %s",
                (tenant.org_id,),
            )
            row = cur.fetchone()
        rebuilt = _project_audit_payload(_row_to_audit(row))
        signer = AuditChainSigner.from_env(environment_env_var="BACKEND_ENVIRONMENT")
        chain = [
            AuditChainRow(
                seq=int(row["seq"]),
                payload=rebuilt,
                prev_hash=None,
                signature=bytes(row["signature"]),
                key_version=int(row["key_version"]),
            )
        ]
        assert signer.verify_chain(chain).ok is True


class TestRlsEnforced:
    """Cross-tenant reads are blocked by the 0043 policies themselves.

    Uses the raw-connection pattern from ``test_rls_isolation.py``: the
    app (non-superuser) role stamps ``app.current_org_id`` for another
    org and must not see — or delete — the first org's rows. Skipped
    when the gate did not provide a non-superuser URL (RLS is bypassed
    by superusers by design, so the assertion would be vacuous).
    """

    @pytest.fixture
    def app_conn(self, app_database_url: str, database_url: str) -> Iterator[Any]:
        if app_database_url == database_url:
            pytest.skip(
                "BACKEND_MERGE_TEST_APP_DATABASE_URL unset — no non-superuser "
                "role available, RLS enforcement cannot be observed."
            )
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(
            app_database_url, autocommit=True, row_factory=dict_row
        ) as conn:
            yield conn

    @staticmethod
    def _set_org(conn: Any, org_id: str) -> None:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org_id', %s, false)", (org_id,))

    def test_cross_tenant_select_and_delete_blocked(
        self, store: Any, mk_tenant: Any, app_conn: Any
    ) -> None:
        tenant_a = mk_tenant("rlsa")
        tenant_b = mk_tenant("rlsb")
        project = _mk_project(tenant_a)
        store.insert_project(project)

        self._set_org(app_conn, tenant_b.org_id)
        with app_conn.cursor() as cur:
            cur.execute("SELECT 1 FROM projects WHERE id = %s", (project.id,))
            assert cur.fetchone() is None
            cur.execute("DELETE FROM projects WHERE id = %s", (project.id,))
            assert cur.rowcount == 0

        self._set_org(app_conn, tenant_a.org_id)
        with app_conn.cursor() as cur:
            cur.execute("SELECT 1 FROM projects WHERE id = %s", (project.id,))
            assert cur.fetchone() is not None

    def test_store_reads_are_tenant_scoped(self, store: Any, mk_tenant: Any) -> None:
        tenant_a = mk_tenant("scoa")
        tenant_b = mk_tenant("scob")
        project = _mk_project(tenant_a)
        store.insert_project(project)
        assert (
            store.get_project(tenant_id=tenant_b.org_id, project_id=project.id) is None
        )
        page, _ = store.list_projects(tenant_id=tenant_b.org_id)
        assert project.id not in {p.id for p in page}
