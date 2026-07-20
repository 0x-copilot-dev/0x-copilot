"""Schema-consistency guard for the Postgres merge registry (PRD FR-M3).

The account-merge re-key executor runs raw SQL over a declared table
registry. A wrong column name or a table missing from every strategy would
abort a live merge mid-transaction — so this test derives the AUTHORITATIVE
schema from the migrations DDL and enforces two invariants:

1. Every ``_SPECS`` entry references real tables/columns (org/user/key).
2. Every org-scoped table in the schema is accounted for: in ``_SPECS`` or
   in the documented ``_LEAVE_IN_PLACE`` set. New migrations that add a
   tenant table without classifying it for merge fail here, not in prod.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend_app.identity.account_merge import PostgresMergeData

_MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"

_CREATE_RE = re.compile(
    r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)\s*\((.*?)^\);",
    re.IGNORECASE | re.DOTALL | re.MULTILINE,
)
_ALTER_ADD_RE = re.compile(r"ALTER TABLE (\w+)\s+(.*?);", re.IGNORECASE | re.DOTALL)
_COLUMN_RE = re.compile(r"^\s*(\w+)\s+\w", re.MULTILINE)
_SQL_KEYWORDS = {
    "primary",
    "unique",
    "check",
    "constraint",
    "foreign",
    "references",
    "like",
    "exclude",
}
# Org-scoped column names used across the schema (several tables predate the
# org_id convention).
_ORG_COLUMNS = {"org_id", "tenant_id", "reviewer_org_id"}


def _schema() -> dict[str, set[str]]:
    tables: dict[str, set[str]] = {}
    for sql_file in sorted(_MIGRATIONS.glob("*.sql")):
        if ".rollback." in sql_file.name:
            continue
        text = sql_file.read_text()
        for match in _CREATE_RE.finditer(text):
            name = match.group(1).lower()
            body = match.group(2)
            columns = {
                col.lower()
                for col in _COLUMN_RE.findall(body)
                if col.lower() not in _SQL_KEYWORDS
            }
            tables.setdefault(name, set()).update(columns)
        for match in _ALTER_ADD_RE.finditer(text):
            name = match.group(1).lower()
            body = match.group(2)
            for add in re.finditer(
                r"ADD COLUMN (?:IF NOT EXISTS )?(\w+)", body, re.IGNORECASE
            ):
                tables.setdefault(name, set()).add(add.group(1).lower())
            for drop in re.finditer(
                r"DROP COLUMN (?:IF EXISTS )?(\w+)", body, re.IGNORECASE
            ):
                tables.get(name, set()).discard(drop.group(1).lower())
    return tables


class TestMergeRegistrySchemaConsistency:
    def test_every_spec_references_real_tables_and_columns(self) -> None:
        schema = _schema()
        problems: list[str] = []
        for table, _strategy, org_col, user_col, key_cols in (
            PostgresMergeData._SPECS  # noqa: SLF001 - the registry under test
        ):
            columns = schema.get(table)
            if columns is None:
                problems.append(f"{table}: table not found in migrations DDL")
                continue
            for col in filter(None, (org_col, user_col, *key_cols)):
                if col.lower() not in columns:
                    problems.append(
                        f"{table}: column {col!r} not in schema "
                        f"(has: {sorted(columns)})"
                    )
        assert not problems, "\n".join(problems)

    def test_every_org_scoped_table_is_accounted_for(self) -> None:
        schema = _schema()
        spec_tables = {
            spec[0]
            for spec in PostgresMergeData._SPECS  # noqa: SLF001
        }
        leave = PostgresMergeData._LEAVE_IN_PLACE  # noqa: SLF001
        unaccounted = sorted(
            table
            for table, columns in schema.items()
            if columns & _ORG_COLUMNS
            and table not in spec_tables
            and table not in leave
        )
        assert not unaccounted, (
            "Org-scoped tables with NO merge classification (add to _SPECS "
            f"or to the documented _LEAVE_IN_PLACE set): {unaccounted}"
        )

    def test_mfa_children_are_join_dropped_not_spec_listed(self) -> None:
        # totp_secrets / webauthn_credentials carry only factor_id — they are
        # dropped via the join through mfa_factors (FK order), never via a
        # tenancy-column spec that would reference nonexistent columns.
        spec_tables = {
            spec[0]
            for spec in PostgresMergeData._SPECS  # noqa: SLF001
        }
        assert "totp_secrets" not in spec_tables
        assert "webauthn_credentials" not in spec_tables
