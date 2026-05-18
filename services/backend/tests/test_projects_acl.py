"""Tests for the canonical project-scoped ACL — Phase 6 P6-A1.

The :mod:`backend_app.projects.acl` module is the **single source of
truth** for the cross-audit §1.3 master rule. Every destination
(Todos / Inbox / Routines / Library / Memory / Chats) consumes the
predicate via in-process import; ``ai-backend`` consumes it via the
internal HTTP endpoint.

Coverage:

* :func:`is_member` returns True / False correctly.
* :func:`member_role` returns the role string or None.
* :func:`list_projects_for_user` enumerates correctly.
* Stand-in port alias ``is_project_member`` delegates to the canonical
  predicate (P6-A2 will rewire callers; for now we ship the alias).
* The Postgres adapter skeleton raises NotImplementedError until the
  connection pool is wired (production deploy concern).
"""

from __future__ import annotations

import pytest

from backend_app.projects.acl import (
    InMemoryProjectMembershipAdapter,
    PostgresProjectMembershipAdapter,
    is_member,
    member_role,
)


class TestInMemoryAdapter:
    def test_unknown_project_returns_none(self) -> None:
        adapter = InMemoryProjectMembershipAdapter()
        assert (
            adapter.is_member(
                tenant_id="org_acme", project_id="prj_missing", user_id="usr_sarah"
            )
            is False
        )
        assert (
            adapter.member_role(
                tenant_id="org_acme", project_id="prj_missing", user_id="usr_sarah"
            )
            is None
        )

    def test_non_member_returns_none(self) -> None:
        adapter = InMemoryProjectMembershipAdapter()
        adapter.add(
            tenant_id="org_acme",
            project_id="prj_renewal",
            user_id="usr_sarah",
            role="owner",
        )
        # A different user → not a member.
        assert (
            adapter.is_member(
                tenant_id="org_acme", project_id="prj_renewal", user_id="usr_bob"
            )
            is False
        )
        assert (
            adapter.member_role(
                tenant_id="org_acme",
                project_id="prj_renewal",
                user_id="usr_bob",
            )
            is None
        )

    def test_member_returns_role(self) -> None:
        adapter = InMemoryProjectMembershipAdapter()
        adapter.add(
            tenant_id="org_acme",
            project_id="prj_renewal",
            user_id="usr_sarah",
            role="owner",
        )
        adapter.add(
            tenant_id="org_acme",
            project_id="prj_renewal",
            user_id="usr_bob",
            role="editor",
        )
        adapter.add(
            tenant_id="org_acme",
            project_id="prj_renewal",
            user_id="usr_carol",
            role="viewer",
        )
        assert adapter.is_member(
            tenant_id="org_acme",
            project_id="prj_renewal",
            user_id="usr_sarah",
        )
        assert (
            adapter.member_role(
                tenant_id="org_acme",
                project_id="prj_renewal",
                user_id="usr_sarah",
            )
            == "owner"
        )
        assert (
            adapter.member_role(
                tenant_id="org_acme",
                project_id="prj_renewal",
                user_id="usr_bob",
            )
            == "editor"
        )
        assert (
            adapter.member_role(
                tenant_id="org_acme",
                project_id="prj_renewal",
                user_id="usr_carol",
            )
            == "viewer"
        )

    def test_tenant_isolation(self) -> None:
        """Same user id in a different tenant must NOT match."""

        adapter = InMemoryProjectMembershipAdapter()
        adapter.add(
            tenant_id="org_acme",
            project_id="prj_renewal",
            user_id="usr_sarah",
            role="owner",
        )
        assert (
            adapter.is_member(
                tenant_id="org_zeta",
                project_id="prj_renewal",
                user_id="usr_sarah",
            )
            is False
        )

    def test_list_projects_for_user(self) -> None:
        adapter = InMemoryProjectMembershipAdapter()
        adapter.add(
            tenant_id="org_acme",
            project_id="prj_a",
            user_id="usr_sarah",
            role="owner",
        )
        adapter.add(
            tenant_id="org_acme",
            project_id="prj_b",
            user_id="usr_sarah",
            role="editor",
        )
        # Sarah-in-zeta → unrelated.
        adapter.add(
            tenant_id="org_zeta",
            project_id="prj_other",
            user_id="usr_sarah",
            role="owner",
        )
        ids = adapter.list_projects_for_user(tenant_id="org_acme", user_id="usr_sarah")
        assert set(ids) == {"prj_a", "prj_b"}

    def test_invalid_role_rejected(self) -> None:
        adapter = InMemoryProjectMembershipAdapter()
        with pytest.raises(ValueError):
            adapter.add(
                tenant_id="org_acme",
                project_id="prj_x",
                user_id="usr_sarah",
                role="member",  # type: ignore[arg-type]
            )

    def test_remove_drops_membership(self) -> None:
        adapter = InMemoryProjectMembershipAdapter()
        adapter.add(
            tenant_id="org_acme",
            project_id="prj_x",
            user_id="usr_sarah",
            role="editor",
        )
        adapter.remove(tenant_id="org_acme", project_id="prj_x", user_id="usr_sarah")
        assert (
            adapter.is_member(
                tenant_id="org_acme",
                project_id="prj_x",
                user_id="usr_sarah",
            )
            is False
        )

    def test_remove_idempotent(self) -> None:
        """Removing a non-member is a no-op."""

        adapter = InMemoryProjectMembershipAdapter()
        adapter.remove(tenant_id="org_acme", project_id="prj_x", user_id="usr_ghost")

    def test_stand_in_port_alias_delegates(self) -> None:
        """``is_project_member`` is the legacy stand-in name; it MUST
        delegate to :meth:`is_member` so the canonical predicate is the
        only place membership is decided."""

        adapter = InMemoryProjectMembershipAdapter()
        adapter.add(
            tenant_id="org_acme",
            project_id="prj_renewal",
            user_id="usr_sarah",
            role="editor",
        )
        assert adapter.is_project_member(
            tenant_id="org_acme",
            project_id="prj_renewal",
            user_id="usr_sarah",
        )
        assert (
            adapter.is_project_member(
                tenant_id="org_acme",
                project_id="prj_renewal",
                user_id="usr_bob",
            )
            is False
        )


class TestModuleHelpers:
    def test_is_member_wrapper(self) -> None:
        adapter = InMemoryProjectMembershipAdapter()
        adapter.add(
            tenant_id="org_acme",
            project_id="prj_renewal",
            user_id="usr_sarah",
            role="owner",
        )
        assert is_member(
            adapter,
            tenant_id="org_acme",
            project_id="prj_renewal",
            user_id="usr_sarah",
        )

    def test_member_role_wrapper(self) -> None:
        adapter = InMemoryProjectMembershipAdapter()
        adapter.add(
            tenant_id="org_acme",
            project_id="prj_renewal",
            user_id="usr_sarah",
            role="viewer",
        )
        assert (
            member_role(
                adapter,
                tenant_id="org_acme",
                project_id="prj_renewal",
                user_id="usr_sarah",
            )
            == "viewer"
        )
        # Non-member → None.
        assert (
            member_role(
                adapter,
                tenant_id="org_acme",
                project_id="prj_renewal",
                user_id="usr_bob",
            )
            is None
        )


class TestPostgresSkeleton:
    """The Postgres adapter is a skeleton at P6-A1 — production deploys
    inject a wired connection pool. Until then the methods raise
    NotImplementedError so a deploy that omits the wiring fails loudly
    rather than silently denying every membership read."""

    def test_member_role_raises_without_pool(self) -> None:
        adapter = PostgresProjectMembershipAdapter()
        with pytest.raises(NotImplementedError):
            adapter.member_role(
                tenant_id="org_acme",
                project_id="prj_x",
                user_id="usr_sarah",
            )

    def test_list_projects_raises_without_pool(self) -> None:
        adapter = PostgresProjectMembershipAdapter()
        with pytest.raises(NotImplementedError):
            adapter.list_projects_for_user(tenant_id="org_acme", user_id="usr_sarah")
