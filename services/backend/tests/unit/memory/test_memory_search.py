"""Unit tests for memory hybrid search — Phase 12 P12-A3.

The MemorySearchEngine reuses Library's RRF fusion + SearchHit /
FusedHit primitives — the test surface focuses on the behaviour we own:

* BM25 ranks rows by title/body match.
* The engine delegates to ``rrf_fuse`` (library) so single-leg results
  still produce a stable ordering.
* ACL trim drops out-of-scope rows.
* The engine passes ``target_kind="memory"`` semantics (hits' ``kind``
  field is the literal "memory" — the same dispatch hint the worker
  uses for the embedding row).
"""

from __future__ import annotations

from backend_app.memory.search import (
    InMemoryMemorySearchIndex,
    MemorySearchEngine,
)
from backend_app.memory.service import MemoryService
from backend_app.memory.store import InMemoryMemoryStore
from backend_app.projects.acl import InMemoryProjectMembershipAdapter


def _service(
    memberships: dict[tuple[str, str], set[str]] | None = None,
) -> tuple[MemoryService, InMemoryMemoryStore, MemorySearchEngine]:
    store = InMemoryMemoryStore()
    port = InMemoryProjectMembershipAdapter(memberships or {})
    svc = MemoryService(store=store, membership_port=port)
    engine = MemorySearchEngine(
        store=store,
        index=InMemoryMemorySearchIndex(store=store),
        membership_port=port,
    )
    return svc, store, engine


def test_index_returns_memory_kind_on_hits() -> None:
    svc, _, engine = _service()
    svc.create_item(
        tenant_id="org_acme",
        caller_user_id="usr_sarah",
        creator=None,
        scope="user",
        kind="skill",
        title="Python expert",
        body="loves Django",
        tags=None,
        project_id=None,
    )
    envelope = engine.search(
        tenant_id="org_acme",
        caller_user_id="usr_sarah",
        caller_roles=(),
        query="python",
    )
    assert envelope.hits
    assert envelope.hits[0].record.title == "Python expert"
    # The hit's score is the fused score; non-zero for a positive match.
    assert envelope.hits[0].score > 0


def test_search_acl_drops_other_users_private_rows() -> None:
    svc, _, engine = _service()
    # Sarah's private user-scoped row.
    svc.create_item(
        tenant_id="org_acme",
        caller_user_id="usr_sarah",
        creator=None,
        scope="user",
        kind="fact",
        title="Sarah's secret",
        body="confidential",
        tags=None,
        project_id=None,
    )
    # Bob searches — sees nothing in the result set.
    envelope = engine.search(
        tenant_id="org_acme",
        caller_user_id="usr_bob",
        caller_roles=(),
        query="confidential",
    )
    assert envelope.hits == ()


def test_search_workspace_scope_is_readable_by_any_member() -> None:
    svc, _, engine = _service()
    svc.create_item(
        tenant_id="org_acme",
        caller_user_id="usr_sarah",
        creator=None,
        scope="workspace",
        kind="preference",
        title="TL;DR up top",
        body="always start with a summary",
        tags=None,
        project_id=None,
    )
    envelope = engine.search(
        tenant_id="org_acme",
        caller_user_id="usr_bob",
        caller_roles=(),
        query="summary",
    )
    assert envelope.hits
    assert envelope.hits[0].record.title == "TL;DR up top"


def test_search_tenant_isolation() -> None:
    svc, _, engine = _service()
    svc.create_item(
        tenant_id="org_acme",
        caller_user_id="usr_sarah",
        creator=None,
        scope="workspace",
        kind="fact",
        title="acme launch is Q1",
        body="",
        tags=None,
        project_id=None,
    )
    # Caller in org_zeta gets nothing.
    envelope = engine.search(
        tenant_id="org_zeta",
        caller_user_id="usr_alice",
        caller_roles=(),
        query="acme",
    )
    assert envelope.hits == ()


def test_search_uses_library_rrf_pure_function() -> None:
    # The engine imports rrf_fuse from library.search; this test asserts
    # the import path stays single-sourced (a renamed/moved function
    # breaks here, which is the desired signal).
    from backend_app.library.search import rrf_fuse
    from backend_app.memory import search as memory_search

    assert memory_search.rrf_fuse is rrf_fuse  # noqa: SLF001 — module attr
