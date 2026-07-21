"""Tests for the ai-backend yoyo-migrations runner.

The Postgres-only DDL in production migrations cannot run against SQLite
(JSONB, partial indexes, DO blocks), so we exercise the runner against fixture
migrations checked in under ``tests/fixtures/migrations/``. Production
migrations are exercised end-to-end by the integration suite that boots a
real Postgres.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_runtime.persistence.schema.migrate import (
    MIGRATIONS_DIR,
    MigrationManifestError,
    MigrationRunner,
    render_manifest,
)


_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sqlite_migrations"


class _SqliteRunner(MigrationRunner):
    """Variant of the runner that reads fixture migrations under sqlite."""

    @classmethod
    def migrations_dir(cls) -> Path:
        return _FIXTURE_DIR

    @classmethod
    def apply(cls, database_url: str) -> list[str]:
        import yoyo

        backend = yoyo.get_backend(database_url)
        migrations = yoyo.read_migrations(str(_FIXTURE_DIR))
        with backend.lock():
            to_apply = backend.to_apply(migrations)
            applied_ids = [m.id for m in to_apply]
            backend.apply_migrations(to_apply)
        return applied_ids

    @classmethod
    def rollback(cls, database_url: str, *, to: str | None = None) -> list[str]:
        import yoyo

        backend = yoyo.get_backend(database_url)
        migrations = yoyo.read_migrations(str(_FIXTURE_DIR))
        applied = backend.to_rollback(migrations)
        if to is not None:
            applied = applied.__class__([m for m in applied if m.id > to])
        with backend.lock():
            rolled_back_ids = [m.id for m in applied]
            backend.rollback_migrations(applied)
        return rolled_back_ids

    @classmethod
    def status(cls, database_url: str) -> tuple[list[str], list[str]]:
        import yoyo

        backend = yoyo.get_backend(database_url)
        migrations = yoyo.read_migrations(str(_FIXTURE_DIR))
        applied = [m.id for m in backend.to_rollback(migrations)]
        pending = [m.id for m in backend.to_apply(migrations)]
        return applied, pending


class TestMigrationRunner:
    def test_apply_then_rollback_roundtrip_against_sqlite(self, tmp_path: Path) -> None:
        url = f"sqlite:///{tmp_path / 'test.db'}"

        applied = _SqliteRunner.apply(url)
        assert applied == ["0001_create_widgets", "0002_add_widget_color"]

        applied_after, pending_after = _SqliteRunner.status(url)
        assert set(applied_after) == {
            "0001_create_widgets",
            "0002_add_widget_color",
        }
        assert pending_after == []

        rolled = _SqliteRunner.rollback(url, to="0001_create_widgets")
        assert rolled == ["0002_add_widget_color"]

        applied_final, pending_final = _SqliteRunner.status(url)
        assert applied_final == ["0001_create_widgets"]
        assert pending_final == ["0002_add_widget_color"]

    def test_apply_is_idempotent(self, tmp_path: Path) -> None:
        url = f"sqlite:///{tmp_path / 'test.db'}"
        _SqliteRunner.apply(url)
        applied_second = _SqliteRunner.apply(url)
        assert applied_second == [], "Re-running apply must be a no-op"

    def test_auto_apply_default_true_in_dev(self) -> None:
        assert MigrationRunner.auto_apply_enabled(env={}) is True

    def test_auto_apply_false_when_explicitly_disabled(self) -> None:
        assert (
            MigrationRunner.auto_apply_enabled(
                env={"RUNTIME_MIGRATIONS_AUTO_APPLY": "false"}
            )
            is False
        )


class TestProductionManifestMatchesDirectory:
    def test_actual_manifest_matches_lock_file(self) -> None:
        actual = MigrationRunner.actual_manifest()
        expected = MigrationRunner.expected_manifest()

        assert actual == expected, (
            "MANIFEST.lock drifts from migrations directory; "
            "run python tools/check_migration_manifest.py --write"
        )

    def test_manifest_includes_initial_runtime_persistence(self) -> None:
        actual = MigrationRunner.actual_manifest()
        assert "0001_initial_runtime_persistence" in actual
        assert "0002_runtime_events_presentation" in actual

    def test_missing_manifest_raises_typed_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from agent_runtime.persistence.schema import migrate as migrate_module

        monkeypatch.setattr(migrate_module, "MANIFEST_FILE", tmp_path / "missing.lock")
        with pytest.raises(MigrationManifestError):
            MigrationRunner.expected_manifest()


class TestRenderManifest:
    def test_renders_one_line_per_entry_with_header(self) -> None:
        text = render_manifest([("0001_a", "deadbeef"), ("0002_b", "cafef00d")])
        assert text.startswith("# Auto-generated")
        assert "0001_a sha256=deadbeef" in text
        assert "0002_b sha256=cafef00d" in text
        assert text.endswith("\n")


def test_production_migrations_dir_exists() -> None:
    assert MIGRATIONS_DIR.is_dir(), MIGRATIONS_DIR


class TestYoyoUrlDriverPinning:
    """`_yoyo_url` must force psycopg3 so yoyo never imports psycopg2 (which
    this repo does not install). Regression guard for the live-Postgres
    gate + every production migration that receives a bare postgresql:// URL."""

    def test_bare_postgresql_scheme_is_pinned_to_psycopg3(self) -> None:
        assert (
            MigrationRunner._yoyo_url("postgresql://u:p@h:5432/db")
            == "postgresql+psycopg://u:p@h:5432/db"
        )

    def test_short_postgres_scheme_is_pinned_too(self) -> None:
        assert (
            MigrationRunner._yoyo_url("postgres://u@h/db")
            == "postgresql+psycopg://u@h/db"
        )

    def test_already_pinned_url_is_unchanged(self) -> None:
        url = "postgresql+psycopg://u@h/db"
        assert MigrationRunner._yoyo_url(url) == url

    def test_non_postgres_scheme_is_left_alone(self) -> None:
        assert MigrationRunner._yoyo_url("sqlite:///tmp/x.db") == "sqlite:///tmp/x.db"
