"""Static contract for the Context Occupancy Ledger's migration (design §5).

Four properties are worth pinning in SQL rather than only in the adapter,
because they are the ones an adapter cannot restore once a row exists: the
attempt-level uniqueness that makes the append idempotent, the cascade that
keeps a row from outliving either parent, the immutability of an observation
row, and the fact that ``unattributed_delta`` is allowed to be negative.

A fifth property is pinned on the migration's *comment*, which is unusual and
deliberate: the comment is the artifact a compliance reviewer reads, and this
file previously asserted the DDL while the prose beside it claimed a retention
control the schema does not deliver. A cascade that no path can currently
trigger is not a retention policy, and the test named for that claim is what
made the overstatement look verified.
"""

from __future__ import annotations

from pathlib import Path

from agent_runtime.persistence.records import RetentionKind, RuntimeContextGraphScope
from agent_runtime.persistence.schema.migrate import MigrationRunner
from agent_runtime.persistence.schema.postgres import (
    POSTGRES_AGENT_RUNTIME_MIGRATION_SQL,
)

_MIGRATIONS = Path(__file__).resolve().parents[4] / "migrations"
_FORWARD = (_MIGRATIONS / "0026_context_occupancy_ledger.sql").read_text()
_ROLLBACK = (_MIGRATIONS / "0026_context_occupancy_ledger.rollback.sql").read_text()
_TABLE = "runtime_context_occupancy"


class MigrationStatementsMixin:
    """Reads the migration as executable SQL, with commentary stripped.

    The forward file documents *why* the money tracker stays untouched, so a
    naive substring assertion on the whole file would match the explanation
    rather than a statement. These tests assert on what Postgres will run.
    """

    def statements(self, sql: str) -> str:
        return "\n".join(
            line for line in sql.splitlines() if not line.lstrip().startswith("--")
        )


class TestContextOccupancyMigration(MigrationStatementsMixin):
    def test_identity_is_the_measured_attempt_not_the_transport_id(self) -> None:
        assert f"CREATE TABLE {_TABLE} (" in _FORWARD
        assert (
            f"CONSTRAINT {_TABLE}_attempt_key\n"
            "        UNIQUE (model_call_id, attempt_ordinal)" in _FORWARD
        )
        assert f"CONSTRAINT {_TABLE}_pkey PRIMARY KEY (id)" in _FORWARD

    def test_a_row_can_never_outlive_either_of_its_parents(self) -> None:
        # The composite run key carries tenancy AND the account-merge re-key,
        # exactly as runtime_usage_attribution_edges does for its parent.
        statements = self.statements(_FORWARD)
        assert "FOREIGN KEY (org_id, run_id)" in statements
        assert "REFERENCES agent_runs (org_id, id)" in statements
        assert "FOREIGN KEY (conversation_id)" in statements
        assert "REFERENCES agent_conversations (id)" in statements
        # Both parents, so a row can never outlive the run or the conversation
        # it describes.
        assert statements.count("ON DELETE CASCADE") == 2
        assert "ON UPDATE CASCADE" in statements

    def test_the_cascade_is_not_documented_as_a_retention_control(self) -> None:
        """The cascade is correct; calling it retention was the defect.

        This assertion is about the comment block on purpose, because the comment
        is what a compliance reviewer reads. The earlier text said retention
        "needs no new class" since occupancy "is erased by exactly the deletion
        paths that already erase events and usage" — and neither half held:
        nothing hard-deletes an ``agent_conversations`` or ``agent_runs`` row on
        Postgres (``PersistencePort`` exposes only ``soft_delete_conversation``,
        and ``agent_messages`` / ``agent_runs`` / ``runtime_events`` /
        ``runtime_run_usage`` / ``runtime_model_call_usage`` all hold NO ACTION
        references to the conversation, so a hard delete would raise before any
        cascade fired), and ``runtime_events`` is erased by an explicit
        ``RetentionKind``, not by a cascade. A schema that documents an inherited
        control it does not have is worse than one that documents the gap.
        """

        assert "needs no new class" not in _FORWARD
        assert "currently unreachable" in _FORWARD
        assert "RetentionKind.EVENTS" in _FORWARD
        # ... and the file-native store, which does have a hard purge, is named
        # so the two backends' different obligations are traceable from here.
        assert "_erase_context_occupancy" in _FORWARD

    def test_no_retention_kind_claims_to_sweep_this_relation(self) -> None:
        """Pins the honest posture: erasure is by cascade/purge, never by sweep.

        The day someone adds an occupancy retention kind, this test fails and
        points at the migration comment and the port docstring that both have to
        stop saying "no sweep" — which is exactly the review prompt that should
        accompany a new retention class.
        """

        assert not [kind for kind in RetentionKind if "occupancy" in kind.value]

    def test_the_conversation_cascade_can_use_an_index(self) -> None:
        # A cascading delete without a usable index sequentially scans a table
        # that grows by one row per model call.
        assert (
            "CREATE INDEX idx_runtime_context_occupancy_conversation\n"
            f"    ON {_TABLE} (conversation_id, org_id, created_at DESC);" in _FORWARD
        )

    def test_the_row_is_immutable_and_tenant_isolated(self) -> None:
        assert f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY" in _FORWARD
        assert f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY" in _FORWARD
        assert f"CREATE POLICY tenant_isolation ON {_TABLE}" in _FORWARD
        assert f"GRANT SELECT, INSERT ON {_TABLE} TO enterprise_app;" in _FORWARD
        grants = [line for line in _FORWARD.splitlines() if line.startswith("GRANT")]
        assert grants == [f"GRANT SELECT, INSERT ON {_TABLE} TO enterprise_app;"]

    def test_nullable_where_the_answer_is_genuinely_unknown(self) -> None:
        # Absent from the pricing catalog / no reported usage / no typed
        # assembly. NULL is the honest record; a NOT NULL default would assert
        # a measurement nobody made.
        for column in (
            "assembly_record_id          text,",
            "context_window_tokens       integer,",
            "provider_input_tokens       integer,",
        ):
            assert column in _FORWARD

    def test_unattributed_delta_carries_no_non_negative_check(self) -> None:
        counts_check = _FORWARD.split(f"CONSTRAINT {_TABLE}_counts_check", 1)[1]
        counts_check = counts_check.split("),", 1)[0]
        assert "unattributed_delta" not in counts_check
        assert "estimated_input_tokens >= 0" in counts_check

    def test_graph_scope_check_matches_the_record_vocabulary(self) -> None:
        scope_check = _FORWARD.split(f"CONSTRAINT {_TABLE}_graph_scope_check", 1)[1]
        for scope in RuntimeContextGraphScope:
            assert f"'{scope.value}'" in scope_check

    def test_the_money_tracker_is_untouched(self) -> None:
        # Design §6.1 / §5: occupancy never becomes a column on the usage row.
        statements = self.statements(_FORWARD)
        assert "runtime_model_call_usage" not in statements
        assert "runtime_run_usage" not in statements

    def test_the_baseline_schema_does_not_already_own_this_table(self) -> None:
        assert f"CREATE TABLE {_TABLE} (" not in POSTGRES_AGENT_RUNTIME_MIGRATION_SQL

    def test_rollback_drops_only_what_this_migration_created(self) -> None:
        statements = self.statements(_ROLLBACK)
        assert f"DROP TABLE IF EXISTS {_TABLE}" in statements
        # agent_runs_org_id_id_key belongs to 0014 and is still referenced by
        # runtime_workspace_overlay_manifests, so dropping it here would break
        # a table this migration never created.
        assert "agent_runs_org_id_id_key" not in statements
        assert "ALTER TABLE" not in statements

    def test_migration_pair_is_manifested(self) -> None:
        assert "0026_context_occupancy_ledger" in MigrationRunner.actual_manifest()
        assert MigrationRunner.expected_manifest() == MigrationRunner.actual_manifest()
