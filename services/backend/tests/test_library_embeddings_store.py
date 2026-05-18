"""Embeddings-store tests — Phase 7.5 P7.5-A2.

Coverage:

* ``insert_embeddings`` is idempotent on the natural key.
* ``delete_embeddings_for_target`` cascades on a hard or soft delete.
* ``model_id`` pinning — re-embedding under a new model writes new rows
  while leaving rows from other models alone (library-prd §6.5).
* Tenant scoping — operations never cross tenant boundaries.
* The service-layer enqueue callback fires on create / update /
  delete with the correct ``(tenant_id, kind, target_id)`` triple.
"""

from __future__ import annotations

from backend_app.library.embeddings import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    DEFAULT_EMBEDDING_MODEL_ID,
    Chunk,
    EmbeddingRow,
    InMemoryEmbeddingsStore,
    build_embedding_rows,
    insert_embeddings,
)
from backend_app.library.service import LibraryService
from backend_app.library.store import (
    InMemoryLibraryStore,
    LibraryFileRecord,
)
from backend_app.projects.acl import InMemoryProjectMembershipAdapter


def _vector(seed: float) -> tuple[float, ...]:
    return tuple([seed] * DEFAULT_EMBEDDING_DIMENSIONS)


class TestInMemoryEmbeddingsStore:
    def test_insert_is_idempotent_on_natural_key(self) -> None:
        store = InMemoryEmbeddingsStore()
        row = EmbeddingRow(
            tenant_id="org_a",
            target_kind="page",
            target_id="libpage_abc",
            chunk_ordinal=0,
            chunk_text="hello",
            embedding=_vector(0.1),
            model_id=DEFAULT_EMBEDDING_MODEL_ID,
        )
        insert_embeddings(store, [row, row])
        snapshot = store.list_embeddings_for_target(
            tenant_id="org_a", target_kind="page", target_id="libpage_abc"
        )
        assert len(snapshot) == 1

    def test_delete_cascades_for_target(self) -> None:
        store = InMemoryEmbeddingsStore()
        rows = [
            EmbeddingRow(
                tenant_id="org_a",
                target_kind="page",
                target_id="libpage_abc",
                chunk_ordinal=i,
                chunk_text=f"chunk {i}",
                embedding=_vector(0.1 * i),
                model_id=DEFAULT_EMBEDDING_MODEL_ID,
            )
            for i in range(5)
        ]
        insert_embeddings(store, rows)
        removed = store.delete_embeddings_for_target(
            tenant_id="org_a", target_kind="page", target_id="libpage_abc"
        )
        assert removed == 5
        assert (
            store.list_embeddings_for_target(
                tenant_id="org_a", target_kind="page", target_id="libpage_abc"
            )
            == ()
        )

    def test_delete_model_id_pinning(self) -> None:
        """Re-embedding under a new model deletes only the old model's
        rows for that target. Other models stay (library-prd §6.5)."""

        store = InMemoryEmbeddingsStore()
        old = EmbeddingRow(
            tenant_id="org_a",
            target_kind="page",
            target_id="libpage_abc",
            chunk_ordinal=0,
            chunk_text="hello",
            embedding=_vector(0.1),
            model_id="text-embedding-3-small",
        )
        new = EmbeddingRow(
            tenant_id="org_a",
            target_kind="page",
            target_id="libpage_abc",
            chunk_ordinal=0,
            chunk_text="hello",
            embedding=_vector(0.2),
            model_id="text-embedding-3-large",
        )
        insert_embeddings(store, [old, new])
        store.delete_embeddings_for_target(
            tenant_id="org_a",
            target_kind="page",
            target_id="libpage_abc",
            model_id="text-embedding-3-small",
        )
        survivors = store.list_embeddings_for_target(
            tenant_id="org_a", target_kind="page", target_id="libpage_abc"
        )
        assert len(survivors) == 1
        assert survivors[0].model_id == "text-embedding-3-large"

    def test_tenant_scoping(self) -> None:
        store = InMemoryEmbeddingsStore()
        a = EmbeddingRow(
            tenant_id="org_a",
            target_kind="page",
            target_id="libpage_abc",
            chunk_ordinal=0,
            chunk_text="hello a",
            embedding=_vector(0.1),
            model_id=DEFAULT_EMBEDDING_MODEL_ID,
        )
        b = EmbeddingRow(
            tenant_id="org_b",
            target_kind="page",
            target_id="libpage_abc",  # same id, different tenant.
            chunk_ordinal=0,
            chunk_text="hello b",
            embedding=_vector(0.2),
            model_id=DEFAULT_EMBEDDING_MODEL_ID,
        )
        insert_embeddings(store, [a, b])
        # Tenant-a read sees only tenant-a.
        a_rows = store.list_embeddings_for_target(
            tenant_id="org_a", target_kind="page", target_id="libpage_abc"
        )
        assert len(a_rows) == 1
        assert a_rows[0].chunk_text == "hello a"
        # Tenant-a delete leaves tenant-b's row intact.
        store.delete_embeddings_for_target(
            tenant_id="org_a", target_kind="page", target_id="libpage_abc"
        )
        b_rows = store.list_embeddings_for_target(
            tenant_id="org_b", target_kind="page", target_id="libpage_abc"
        )
        assert len(b_rows) == 1
        assert b_rows[0].chunk_text == "hello b"

    def test_build_embedding_rows_pairs_chunks_with_vectors(self) -> None:
        chunks = [Chunk(ordinal=0, text="a"), Chunk(ordinal=1, text="b")]
        vectors = [_vector(0.1), _vector(0.2)]
        rows = build_embedding_rows(
            tenant_id="org_a",
            target_kind="page",
            target_id="libpage_abc",
            chunks=chunks,
            vectors=vectors,
            model_id="text-embedding-3-small",
        )
        assert len(rows) == 2
        assert rows[0].chunk_ordinal == 0
        assert rows[1].chunk_ordinal == 1
        assert rows[0].chunk_text == "a"
        assert rows[1].chunk_text == "b"
        assert rows[0].embedding == _vector(0.1)

    def test_build_embedding_rows_mismatch_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            build_embedding_rows(
                tenant_id="org_a",
                target_kind="page",
                target_id="libpage_abc",
                chunks=[Chunk(ordinal=0, text="a")],
                vectors=[_vector(0.1), _vector(0.2)],
                model_id="m",
            )

    def test_embedding_row_id_is_stable_under_natural_key(self) -> None:
        first = EmbeddingRow(
            tenant_id="org_a",
            target_kind="page",
            target_id="libpage_abc",
            chunk_ordinal=3,
            chunk_text="hello",
            embedding=_vector(0.1),
            model_id="m",
        )
        second = EmbeddingRow(
            tenant_id="org_a",
            target_kind="page",
            target_id="libpage_abc",
            chunk_ordinal=3,
            chunk_text="different but same key",
            embedding=_vector(0.9),
            model_id="m",
        )
        assert first.row_id == second.row_id


class TestServiceEnqueueCallback:
    """The service layer must fire the indexing-job enqueue on create /
    update / delete — never on a no-op patch."""

    def _make(
        self,
    ) -> tuple[LibraryService, InMemoryLibraryStore, list[tuple[str, str, str]]]:
        store = InMemoryLibraryStore()
        adapter = InMemoryProjectMembershipAdapter()
        calls: list[tuple[str, str, str]] = []

        def enqueue(tenant_id: str, kind: str, target_id: str) -> None:
            calls.append((tenant_id, kind, target_id))

        service = LibraryService(
            store=store, membership_port=adapter, enqueue_index_job=enqueue
        )
        return service, store, calls

    def test_page_create_enqueues_job(self) -> None:
        service, _store, calls = self._make()
        page = service.create_page(
            tenant_id="org_a",
            caller_user_id="usr_1",
            payload={"title": "Notes", "markdown": "hello"},
        )
        assert calls == [("org_a", "page", page.id)]

    def test_page_markdown_update_enqueues_job(self) -> None:
        service, _store, calls = self._make()
        page = service.create_page(
            tenant_id="org_a",
            caller_user_id="usr_1",
            payload={"title": "Notes", "markdown": "v1"},
        )
        calls.clear()
        service.update_item(
            tenant_id="org_a",
            caller_user_id="usr_1",
            caller_roles=(),
            item_id=page.id,
            patch={"markdown": "v2"},
            expected_etag=page.version_etag,
        )
        assert calls == [("org_a", "page", page.id)]

    def test_tag_only_update_does_not_enqueue(self) -> None:
        """Tags are part of the tsvector but never feed the embedding
        chunk — re-embedding on tag edits would be wasted budget."""

        service, store, calls = self._make()
        store.insert_file(
            LibraryFileRecord(
                tenant_id="org_a",
                owner_user_id="usr_1",
                file_kind="pdf",
                name="contract.pdf",
                mime="application/pdf",
                blob_ref="s3://demo/key",
                source={"kind": "user_upload"},
            )
        )
        file_id = next(iter(store.files))
        calls.clear()
        service.update_item(
            tenant_id="org_a",
            caller_user_id="usr_1",
            caller_roles=(),
            item_id=file_id,
            patch={"tags": ["finance"]},
        )
        assert calls == []

    def test_soft_delete_enqueues_cascade(self) -> None:
        service, _store, calls = self._make()
        page = service.create_page(
            tenant_id="org_a",
            caller_user_id="usr_1",
            payload={"title": "Notes", "markdown": "hello"},
        )
        calls.clear()
        service.delete_item(
            tenant_id="org_a",
            caller_user_id="usr_1",
            caller_roles=(),
            item_id=page.id,
        )
        # Soft-delete still enqueues — the indexer detects the
        # ``deleted_at`` flag and cascades to ``library_embeddings``.
        assert calls == [("org_a", "page", page.id)]

    def test_service_without_enqueue_callback_does_not_raise(self) -> None:
        store = InMemoryLibraryStore()
        adapter = InMemoryProjectMembershipAdapter()
        service = LibraryService(store=store, membership_port=adapter)
        # No callback wired — CRUD still works.
        page = service.create_page(
            tenant_id="org_a",
            caller_user_id="usr_1",
            payload={"title": "Notes", "markdown": "hello"},
        )
        assert page.title == "Notes"
