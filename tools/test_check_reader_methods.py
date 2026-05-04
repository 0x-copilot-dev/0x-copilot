"""Tests for tools/check_reader_methods.py.

The check fails when a ``@reader``-marked method contains a write-SQL
keyword as a string literal, and passes when the method body is read-only.
"""

from __future__ import annotations

import textwrap
from pathlib import Path


import importlib.util


def _load_module():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "check_reader_methods", Path(__file__).parent / "check_reader_methods.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(tmp_path: Path, body: str) -> Path:
    target = tmp_path / "fixture.py"
    target.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return target


class TestCheckReaderMethods:
    def test_reader_method_with_select_passes(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            """
            from agent_runtime.persistence._reader import reader

            class Store:
                @reader
                async def list_things(self):
                    return await self.execute("SELECT * FROM things WHERE org_id = %s")
            """,
        )
        module = _load_module()
        violations = module._find_violations(path)
        assert violations == []

    def test_reader_method_with_update_flags(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            """
            from agent_runtime.persistence._reader import reader

            class Store:
                @reader
                async def list_things(self):
                    await self.execute("UPDATE things SET status = 'x'")
            """,
        )
        module = _load_module()
        violations = module._find_violations(path)
        assert len(violations) == 1
        assert "list_things" in violations[0]

    def test_non_reader_method_with_update_passes(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            """
            class Store:
                async def write_thing(self):
                    await self.execute("UPDATE things SET status = 'x'")
            """,
        )
        module = _load_module()
        violations = module._find_violations(path)
        assert violations == []

    def test_column_named_update_in_select_does_not_flag(self, tmp_path: Path) -> None:
        # 'last_updated_at' contains 'update' but the regex matches whole
        # words only, so this select stays clean.
        path = _write(
            tmp_path,
            """
            from agent_runtime.persistence._reader import reader

            class Store:
                @reader
                async def list_things(self):
                    return await self.execute(
                        "SELECT id, last_updated_at FROM things"
                    )
            """,
        )
        module = _load_module()
        violations = module._find_violations(path)
        assert violations == []


class TestCheckScansAllSources:
    def test_main_returns_zero_on_clean_repo(self) -> None:
        # The current ai-backend/src has 4 @reader methods and none should
        # contain a write keyword. If this test fails, somebody added a
        # write to a @reader method — fix that, don't relax this assertion.
        module = _load_module()
        rc = module.main([])
        assert rc == 0
