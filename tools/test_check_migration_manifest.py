"""Smoke tests for the manifest CI guard.

Run via:
    .venv/bin/python -m pytest tools/test_check_migration_manifest.py
from any service venv that has pytest installed.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


_HERE = Path(__file__).resolve().parent
_MODULE_PATH = _HERE / "check_migration_manifest.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "check_migration_manifest", _MODULE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _setup_fixture(tmp_path: Path) -> Path:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_alpha.sql").write_text("CREATE TABLE alpha (id INT);")
    (migrations / "0001_alpha.rollback.sql").write_text("DROP TABLE alpha;")
    return migrations


class TestManifestChecker:
    def test_write_then_check_succeeds(self, tmp_path: Path) -> None:
        migrations = _setup_fixture(tmp_path)
        module = _load_module()

        assert module.check_or_write(migrations, write=True) == 0
        assert module.check_or_write(migrations, write=False) == 0

    def test_modifying_a_migration_after_lock_fails_check(self, tmp_path: Path) -> None:
        migrations = _setup_fixture(tmp_path)
        module = _load_module()
        module.check_or_write(migrations, write=True)

        # Mutate the migration content; manifest should diverge.
        (migrations / "0001_alpha.sql").write_text(
            "CREATE TABLE alpha (id INT, name TEXT);"
        )

        assert module.check_or_write(migrations, write=False) == 1

    def test_adding_a_migration_without_writing_lock_fails(
        self, tmp_path: Path
    ) -> None:
        migrations = _setup_fixture(tmp_path)
        module = _load_module()
        module.check_or_write(migrations, write=True)

        (migrations / "0002_beta.sql").write_text("CREATE TABLE beta (id INT);")
        (migrations / "0002_beta.rollback.sql").write_text("DROP TABLE beta;")

        assert module.check_or_write(migrations, write=False) == 1

    def test_missing_manifest_fails_with_clear_message(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        migrations = _setup_fixture(tmp_path)
        module = _load_module()

        rc = module.check_or_write(migrations, write=False)

        assert rc == 1
        captured = capsys.readouterr()
        assert "missing manifest" in captured.err.lower()


class TestDeclaredServices:
    """The tuple of services that own migrations, and the CLI derived from it."""

    def test_every_declared_migrations_dir_exists(self) -> None:
        """A stale entry here is invisible until something runs the gate.

        `services/ai-backend/migrations` sat in this tuple after the Postgres
        storage backend (and its 54 migrations) were deleted, which made an
        unscoped run exit 2 on a repo that was otherwise fine.
        """
        module = _load_module()

        missing = [
            str(path) for path in module.SERVICE_MIGRATION_DIRS if not path.is_dir()
        ]

        assert missing == []

    def test_service_choices_are_derived_from_declared_dirs(self) -> None:
        module = _load_module()

        assert module.SERVICE_CHOICES == tuple(
            path.parents[0].name for path in module.SERVICE_MIGRATION_DIRS
        )

    def test_unknown_service_is_rejected(self) -> None:
        module = _load_module()

        with pytest.raises(SystemExit):
            module.main(["--service", "no-such-service"])


class TestMainExitCodes:
    """`main` distinguishes drift (1) from a broken declaration (2)."""

    def test_missing_declared_dir_exits_2_not_1(self, tmp_path: Path) -> None:
        module = _load_module()
        module.SERVICE_MIGRATION_DIRS = (tmp_path / "absent",)

        assert module.main([]) == 2

    def test_drift_exits_1(self, tmp_path: Path) -> None:
        migrations = _setup_fixture(tmp_path)
        module = _load_module()
        module.check_or_write(migrations, write=True)
        (migrations / "0002_beta.sql").write_text("CREATE TABLE beta (id INT);")
        module.SERVICE_MIGRATION_DIRS = (migrations,)

        assert module.main([]) == 1

    def test_structural_error_outranks_drift(self, tmp_path: Path) -> None:
        """Severity ordering, not first-failure-wins: 2 must survive a 1."""
        migrations = _setup_fixture(tmp_path)
        module = _load_module()
        module.check_or_write(migrations, write=True)
        (migrations / "0002_beta.sql").write_text("CREATE TABLE beta (id INT);")
        # Drifting dir first, so a first-failure-wins bug would report 1.
        module.SERVICE_MIGRATION_DIRS = (migrations, tmp_path / "absent")

        assert module.main([]) == 2

    def test_clean_tree_exits_0(self, tmp_path: Path) -> None:
        migrations = _setup_fixture(tmp_path)
        module = _load_module()
        module.check_or_write(migrations, write=True)
        module.SERVICE_MIGRATION_DIRS = (migrations,)

        assert module.main([]) == 0
