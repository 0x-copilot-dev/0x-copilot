"""ACL fan-out + quick-action fallback + rank cap.

Covers (sub-PRD §3.3 / §3.4 / §6.3):

* ACL pre-filter per kind — a project-scoped library_item is dropped
  when the caller is not a member.
* User-scope memory: dropped when ``owner_user_id`` mismatches caller.
* Tenant admin sees everything in their tenant.
* Quick-action fallback when no entity matches.
* Quick-action context bias (``/chat`` route → "Make this a routine?").
* Rank cap: ``top_k`` is enforced server-side and clamped to the max.
"""

from __future__ import annotations

from backend_app.palette.service import PaletteService, QuickActions
from backend_app.palette.store import (
    EntityKind,
    InMemoryPaletteStore,
    PaletteEntry,
)
from backend_app.projects.acl import InMemoryProjectMembershipAdapter


def _service(
    membership: InMemoryProjectMembershipAdapter | None = None,
) -> tuple[PaletteService, InMemoryPaletteStore]:
    store = InMemoryPaletteStore()
    port = membership or InMemoryProjectMembershipAdapter()
    return PaletteService(store=store, membership_port=port), store


# ---------------------------------------------------------------------------
# ACL — every palette hit pre-filtered.
# ---------------------------------------------------------------------------


class TestACLFilter:
    def test_non_member_does_not_see_project_scoped_library_item(self) -> None:
        membership = InMemoryProjectMembershipAdapter()
        # Bob is a member of proj_a; Carol is not.
        membership.add(
            tenant_id="acme", project_id="proj_a", user_id="usr_bob", role="editor"
        )
        service, store = _service(membership)
        store.upsert_entry(
            PaletteEntry(
                tenant_id="acme",
                entity_kind=EntityKind.LIBRARY_ITEM,
                entity_id="lib_42",
                title="Roadmap",
                body="Strategy doc",
                project_id="proj_a",
                owner_user_id="usr_sarah",
            )
        )

        # Carol can't see it.
        hits_carol = service.search(
            tenant_id="acme",
            caller_user_id="usr_carol",
            caller_roles=(),
            query="roadmap",
        )
        assert all(h.kind != "entity" for h in hits_carol)

        # Bob (member) can.
        hits_bob = service.search(
            tenant_id="acme",
            caller_user_id="usr_bob",
            caller_roles=(),
            query="roadmap",
        )
        assert any(
            h.kind == "entity" and (h.target or {}).get("id") == "lib_42"
            for h in hits_bob
        )

    def test_admin_sees_everything_in_tenant(self) -> None:
        service, store = _service()
        store.upsert_entry(
            PaletteEntry(
                tenant_id="acme",
                entity_kind=EntityKind.LIBRARY_ITEM,
                entity_id="lib_42",
                title="Confidential",
                project_id="proj_a",
                owner_user_id="usr_sarah",
            )
        )
        hits = service.search(
            tenant_id="acme",
            caller_user_id="usr_admin",
            caller_roles=("admin",),
            query="confidential",
        )
        assert any(
            h.kind == "entity" and (h.target or {}).get("id") == "lib_42" for h in hits
        )

    def test_user_scope_memory_owner_only(self) -> None:
        service, store = _service()
        # owner_user_id set → user-scope memory (not workspace-scope).
        store.upsert_entry(
            PaletteEntry(
                tenant_id="acme",
                entity_kind=EntityKind.MEMORY,
                entity_id="mem_1",
                title="Slack handle",
                owner_user_id="usr_sarah",
            )
        )
        # Bob is not the owner → dropped.
        hits_bob = service.search(
            tenant_id="acme",
            caller_user_id="usr_bob",
            caller_roles=(),
            query="slack",
        )
        assert all(h.kind != "entity" for h in hits_bob)

        # Sarah sees it.
        hits_sarah = service.search(
            tenant_id="acme",
            caller_user_id="usr_sarah",
            caller_roles=(),
            query="slack",
        )
        assert any(
            (h.target or {}).get("kind") == EntityKind.MEMORY for h in hits_sarah
        )

    def test_workspace_scope_memory_visible_to_tenant_member(self) -> None:
        """workspace-scope memory: owner_user_id IS NULL, project_id IS NULL."""
        service, store = _service()
        store.upsert_entry(
            PaletteEntry(
                tenant_id="acme",
                entity_kind=EntityKind.MEMORY,
                entity_id="mem_team",
                title="Team alias",
                owner_user_id=None,
            )
        )
        hits = service.search(
            tenant_id="acme",
            caller_user_id="usr_bob",
            caller_roles=(),
            query="team",
        )
        assert any(
            (h.target or {}).get("id") == "mem_team" for h in hits if h.kind == "entity"
        )

    def test_private_chat_owner_only(self) -> None:
        service, store = _service()
        store.upsert_entry(
            PaletteEntry(
                tenant_id="acme",
                entity_kind=EntityKind.CHAT,
                entity_id="conv_p",
                title="Sarah's notes",
                owner_user_id="usr_sarah",
            )
        )
        hits_bob = service.search(
            tenant_id="acme",
            caller_user_id="usr_bob",
            caller_roles=(),
            query="notes",
        )
        assert all(h.kind != "entity" for h in hits_bob)


# ---------------------------------------------------------------------------
# Quick-action fallback (sub-PRD §3.4 — "Make this a routine?")
# ---------------------------------------------------------------------------


class TestQuickActionFallback:
    def test_no_entity_match_returns_actions(self) -> None:
        service, _ = _service()
        hits = service.search(
            tenant_id="acme",
            caller_user_id="usr_sarah",
            caller_roles=(),
            query="never-matches-anything-xxxxx",
        )
        assert len(hits) > 0
        assert all(h.kind == "action" for h in hits)

    def test_chat_route_surfaces_make_this_a_routine(self) -> None:
        service, _ = _service()
        hits = service.search(
            tenant_id="acme",
            caller_user_id="usr_sarah",
            caller_roles=(),
            query="",
            current_route="/chat/conv_42",
        )
        # Empty q + no entries → fall through to actions; on /chat the
        # "Make this a routine" action MUST be present (Q10 §9.7).
        tokens = {h.action_token for h in hits if h.kind == "action"}
        assert "atlas.routine.create_from_chat" in tokens

    def test_non_chat_route_drops_chat_only_action(self) -> None:
        service, _ = _service()
        hits = service.search(
            tenant_id="acme",
            caller_user_id="usr_sarah",
            caller_roles=(),
            query="zzz-no-entity",
            current_route="/team",
        )
        tokens = {h.action_token for h in hits if h.kind == "action"}
        assert "atlas.routine.create_from_chat" not in tokens
        # Three context-free actions remain.
        assert "atlas.memory.create" in tokens
        assert "atlas.connector.onboard" in tokens
        assert "atlas.library.upload" in tokens

    def test_quick_action_count_is_four(self) -> None:
        """v1 ships exactly 4 starter actions (sub-PRD §3.4)."""
        assert len(QuickActions.ALL) == 4


# ---------------------------------------------------------------------------
# Rank cap
# ---------------------------------------------------------------------------


class TestRankCap:
    def test_top_k_is_honored(self) -> None:
        service, store = _service()
        # 8 tenant-visible workspace memory rows that all match the query.
        for idx in range(8):
            store.upsert_entry(
                PaletteEntry(
                    tenant_id="acme",
                    entity_kind=EntityKind.MEMORY,
                    entity_id=f"mem_{idx}",
                    title=f"Notes alpha {idx}",
                    owner_user_id=None,
                )
            )
        hits = service.search(
            tenant_id="acme",
            caller_user_id="usr_sarah",
            caller_roles=(),
            query="alpha",
            top_k=3,
        )
        # All entity rows; cap respected.
        assert len(hits) == 3
        assert all(h.kind == "entity" for h in hits)

    def test_top_k_clamped_at_max(self) -> None:
        """The service clamps to the documented max (50)."""
        service, store = _service()
        for idx in range(60):
            store.upsert_entry(
                PaletteEntry(
                    tenant_id="acme",
                    entity_kind=EntityKind.MEMORY,
                    entity_id=f"mem_{idx}",
                    title=f"alpha-{idx}",
                    owner_user_id=None,
                )
            )
        hits = service.search(
            tenant_id="acme",
            caller_user_id="usr_sarah",
            caller_roles=(),
            query="alpha",
            top_k=10_000,
        )
        assert len(hits) <= 50
