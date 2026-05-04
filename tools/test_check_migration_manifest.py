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
