"""Static contracts for the legal-hold fence shared by history and artifacts."""

from __future__ import annotations

from pathlib import Path

from runtime_adapters.postgres.artifact_hold_fence import (
    active_hold_for_conversation_predicate,
    active_hold_for_org_predicate,
    hold_fence_tokens,
    hold_fence_tokens_for_rows,
)


_ROOT = Path(__file__).resolve().parents[4]
_MIGRATION = (_ROOT / "migrations" / "0008_artifact_hold_fences.sql").read_text(
    encoding="utf-8"
)
_RUNTIME_STORE = (
    _ROOT / "src" / "runtime_adapters" / "postgres" / "runtime_api_store.py"
).read_text(encoding="utf-8")
_D11_MIGRATION = (_ROOT / "migrations" / "0011_legal_hold_management.sql").read_text(
    encoding="utf-8"
)
_PHYSICAL_CLEANUP_MIGRATION = next(
    (_ROOT / "migrations").glob("*_artifact_physical_cleanup_scopes.sql")
).read_text(encoding="utf-8")
_POSTGRES_GC = (
    _ROOT / "src" / "runtime_adapters" / "postgres" / "artifact_gc.py"
).read_text(encoding="utf-8")


def test_python_fence_order_matches_direct_hold_trigger() -> None:
    expected = (
        "artifact-hold:org:org_a",
        "artifact-hold:user:org_a:user_a",
        "artifact-hold:conversation:org_a:conversation_a",
    )

    assert (
        hold_fence_tokens(
            org_id="org_a",
            user_id="user_a",
            conversation_id="conversation_a",
        )
        == expected
    )
    assert (
        hold_fence_tokens_for_rows((("org_a", "user_a", "conversation_a"),)) == expected
    )
    assert (
        _MIGRATION.index("artifact-hold:org:")
        < _MIGRATION.index("artifact-hold:user:")
        < _MIGRATION.index("artifact-hold:conversation:")
    )


def test_history_erasure_fences_and_rechecks_before_first_destructive_update() -> None:
    transaction = _RUNTIME_STORE.index("async def delete_user_history")
    fence = _RUNTIME_STORE.index("await acquire_artifact_hold_fences", transaction)
    recheck = _RUNTIME_STORE.index("if await has_active_hold_for_scope", fence)
    update = _RUNTIME_STORE.index("UPDATE agent_conversations", recheck)

    assert transaction < fence < recheck < update


def test_direct_delete_cannot_leave_legal_hold_pins_behind() -> None:
    before_function = _MIGRATION.split(
        "CREATE OR REPLACE FUNCTION runtime_artifact_hold_pin_or_release()", 1
    )[0]
    assert "TG_OP = 'DELETE'" in _MIGRATION
    assert "OR DELETE ON runtime_legal_holds" in _MIGRATION
    assert "REVOKE DELETE ON runtime_legal_holds FROM enterprise_app;" in _MIGRATION
    assert "RETURN OLD;" in before_function
    assert "COALESCE(NEW.released_at, now())" not in _MIGRATION


def test_d11_mutations_are_revisioned_idempotent_and_audited_in_transaction() -> None:
    assert "reason_code text NOT NULL DEFAULT 'legacy'" in _D11_MIGRATION
    assert "revision integer NOT NULL DEFAULT 1" in _D11_MIGRATION
    assert "runtime_legal_holds_scope_owner_check" in _D11_MIGRATION
    assert "SET user_id = resource_id" in _D11_MIGRATION
    assert "idx_runtime_legal_holds_create_idempotency" in _D11_MIGRATION
    assert "idx_runtime_legal_holds_release_idempotency" in _D11_MIGRATION

    create = _RUNTIME_STORE.index("async def create_legal_hold")
    create_insert = _RUNTIME_STORE.index("INSERT INTO runtime_legal_holds", create)
    create_audit = _RUNTIME_STORE.index("_write_audit_log_with_conn", create_insert)
    release = _RUNTIME_STORE.index("async def release_legal_hold")
    release_update = _RUNTIME_STORE.index("UPDATE runtime_legal_holds", release)
    release_audit = _RUNTIME_STORE.index("_write_audit_log_with_conn", release_update)

    assert (
        create < create_insert < create_audit < release < release_update < release_audit
    )


def test_all_retention_families_use_a_hold_predicate_inside_shared_fences() -> None:
    conversation_guard = active_hold_for_conversation_predicate(
        org_id_expression="row.org_id",
        user_id_expression="row.user_id",
        conversation_id_expression="row.conversation_id",
    )
    assert "h.released_at IS NULL" in conversation_guard
    assert "h.scope = 'conversation'" in conversation_guard
    # Checkpoints and memory currently have no trustworthy conversation owner;
    # D11 deliberately fails closed for the whole tenant while a hold is active.
    assert "SELECT 1 FROM runtime_legal_holds" in active_hold_for_org_predicate(
        org_id_expression="row.org_id"
    )

    for name in (
        "_sweep_messages",
        "_sweep_events",
        "_sweep_context_payloads",
        "_sweep_checkpoints",
        "_sweep_memory_items",
        "_sweep_messages_chunked",
        "_sweep_events_chunked",
        "_sweep_context_payloads_chunked",
        "_sweep_checkpoints_chunked",
        "_sweep_memory_items_chunked",
        "_sweep_messages_tombstoned_chunked",
        "_sweep_events_tombstoned_chunked",
        "_sweep_memory_items_tombstoned_chunked",
    ):
        start = _RUNTIME_STORE.index(f"async def {name}")
        next_function = _RUNTIME_STORE.find("\n    async def ", start + 1)
        body = _RUNTIME_STORE[start : next_function if next_function != -1 else None]
        assert "hold_predicate" in body, name

    execute = _RUNTIME_STORE.index("async def _execute_sweep")
    assert "await acquire_artifact_hold_fences" in _RUNTIME_STORE[execute:]


def test_physical_cleanup_revalidates_scoped_holds_immediately_before_unlink() -> None:
    """Late holds serialize with the second, physical-delete phase."""

    assert "runtime_artifact_gc_candidate_scopes" in _PHYSICAL_CLEANUP_MIGRATION
    assert "artifact-gc-hold:" in _PHYSICAL_CLEANUP_MIGRATION
    assert "runtime_artifact_hold_pin_or_release" in _PHYSICAL_CLEANUP_MIGRATION

    reaper = _POSTGRES_GC.index("async def _reap_one")
    first_fence = _POSTGRES_GC.index("await acquire_artifact_gc_hold_fence", reaper)
    second_fence = _POSTGRES_GC.index(
        "await acquire_artifact_gc_hold_fence", first_fence + 1
    )
    final_recheck = _POSTGRES_GC.index("await self._revalidation_state", second_fence)
    unlink = _POSTGRES_GC.index("reaping.unlink()", final_recheck)
    assert first_fence < second_fence < final_recheck < unlink
    revalidation = _POSTGRES_GC.index("async def _revalidation_state")
    state_body = _POSTGRES_GC[revalidation:]
    assert "runtime_artifact_gc_candidate_scopes" in state_body
    assert "NOT EXISTS" in state_body
    assert "reference_kind <> 'legal_hold'" in state_body
    assert "ORDER BY candidate_since ASC, provenance_org_id ASC" in _POSTGRES_GC
