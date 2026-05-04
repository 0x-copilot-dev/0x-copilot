"""Unit tests for the audit-in-transaction static checker (C3)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent

sys.path.insert(0, str(HERE))

from check_audit_in_transaction import _check_file  # noqa: E402


def _write(tmp_path: Path, name: str, body: str) -> Path:
    target = tmp_path / name
    target.write_text(body)
    return target


def test_pass_when_audit_inside_transaction_block(tmp_path: Path) -> None:
    target = _write(
        tmp_path,
        "service_ok.py",
        """\
class S:
    def create(self, record):
        with self.store.transaction() as conn:
            self.store.create_skill(record, conn=conn)
            self.store.append_skill_audit(record, conn=conn)
""",
    )
    assert _check_file(target) == []


def test_fail_when_audit_outside_transaction_block(tmp_path: Path) -> None:
    target = _write(
        tmp_path,
        "service_bad.py",
        """\
class S:
    def create(self, record):
        self.store.create_skill(record)
        self.store.append_skill_audit(record)
""",
    )
    violations = _check_file(target)
    assert len(violations) == 1
    assert violations[0].function == "create"
    # Pointer to the audit append line.
    assert violations[0].lineno >= 4


def test_helper_passing_conn_through_is_not_a_violation(tmp_path: Path) -> None:
    """The ``_audit`` helper itself doesn't open a txn — it delegates."""

    target = _write(
        tmp_path,
        "service_helper.py",
        """\
class S:
    def _audit(self, record, *, conn=None):
        self.store.append_audit(record, conn=conn)
""",
    )
    assert _check_file(target) == []


def test_async_with_transaction_recognized(tmp_path: Path) -> None:
    target = _write(
        tmp_path,
        "service_async.py",
        """\
class S:
    async def create(self, record):
        async with self.store.transaction() as conn:
            await self.store.create(record, conn=conn)
            await self.store.append_audit(record, conn=conn)
""",
    )
    assert _check_file(target) == []


def test_missing_file_reported_as_violation(tmp_path: Path) -> None:
    violations = _check_file(tmp_path / "does_not_exist.py")
    assert len(violations) == 1
    assert violations[0].function == "<file>"


def test_real_backend_service_passes(tmp_path: Path) -> None:
    """End-to-end: the actual production service module passes."""

    target = HERE.parent / "services" / "backend" / "src" / "backend_app" / "service.py"
    if not target.exists():
        pytest.skip("backend service.py not present in this checkout")
    violations = _check_file(target)
    assert violations == []
