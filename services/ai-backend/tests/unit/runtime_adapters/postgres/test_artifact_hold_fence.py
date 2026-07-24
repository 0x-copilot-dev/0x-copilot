"""Static contracts for the legal-hold fence shared by history and artifacts."""

from __future__ import annotations

from pathlib import Path

from runtime_adapters.postgres.artifact_hold_fence import (
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
    assert "TG_OP = 'DELETE'" in _MIGRATION
    assert "OR DELETE ON runtime_legal_holds" in _MIGRATION
    assert "REVOKE DELETE ON runtime_legal_holds FROM enterprise_app;" in _MIGRATION
