"""Unit tests for the builtin curated SurfaceSpec library (generative-UI PRD-02).

Verifies the shipped library loads + validates, ``lookup`` resolves curated
tools (including seed-prefixed names), and — the load-bearing one — that a
malformed builtin file raises :class:`BuiltinSpecError` naming the file so a
bad fixture fails the suite rather than a live run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_runtime.capabilities.surfaces import builtin
from agent_runtime.capabilities.surfaces.builtin import (
    BuiltinSpecError,
    load_builtin_specs,
)
from agent_runtime.capabilities.surfaces.spec_models import SurfaceSpec

# The (server_slug, tool) pairs PRD-02 requires the builtin library to curate.
_REQUIRED_BUILTINS: tuple[tuple[str, str], ...] = (
    ("linear", "get_issue"),
    ("linear", "list_issues"),
    ("github", "get_issue"),
    ("github", "list_issues"),
    ("github", "list_pull_requests"),
    ("notion", "get_page"),
    ("asana", "list_tasks"),
    ("sentry", "list_issues"),
    ("atlassian", "get_issue"),
    ("atlassian", "search_issues"),
    ("intercom", "list_conversations"),
)


class TestBuiltinLibraryShips:
    def test_every_required_builtin_is_present(self) -> None:
        for server, tool in _REQUIRED_BUILTINS:
            spec = builtin.lookup(server, tool)
            assert spec is not None, f"missing builtin spec for {server}/{tool}"
            assert isinstance(spec, SurfaceSpec)

    def test_at_least_twelve_specs_load(self) -> None:
        assert len(builtin.all_specs()) >= 12

    def test_lookup_accepts_seed_prefixed_server(self) -> None:
        assert builtin.lookup("seed:linear", "get_issue") is not None

    def test_lookup_is_case_insensitive_on_tool(self) -> None:
        assert builtin.lookup("linear", "GET_ISSUE") is not None

    def test_uncurated_lookup_returns_none(self) -> None:
        assert builtin.lookup("linear", "nonexistent_tool") is None
        assert builtin.lookup("no_such_server", "get_issue") is None


class TestBuiltinLoaderValidation:
    def _write(self, directory: Path, name: str, content: str) -> None:
        (directory / name).write_text(content, encoding="utf-8")

    def test_valid_dir_loads(self, tmp_path: Path) -> None:
        self._write(
            tmp_path,
            "linear.get_issue.json",
            json.dumps(
                {
                    "spec_version": 1,
                    "archetype": "record",
                    "source": {"server": "seed:linear", "tool": "get_issue"},
                    "title_path": "issue.title",
                }
            ),
        )
        registry = load_builtin_specs(tmp_path)
        assert ("linear", "get_issue") in registry

    def test_malformed_json_raises_naming_the_file(self, tmp_path: Path) -> None:
        self._write(tmp_path, "broken.json", "{ this is not json ")

        with pytest.raises(BuiltinSpecError) as excinfo:
            load_builtin_specs(tmp_path)

        assert "broken.json" in str(excinfo.value)

    def test_schema_invalid_spec_raises_naming_the_file(self, tmp_path: Path) -> None:
        # Missing the required ``title_path`` — rejected by validate_surface_spec.
        self._write(
            tmp_path,
            "bad_spec.json",
            json.dumps(
                {
                    "spec_version": 1,
                    "archetype": "record",
                    "source": {"server": "seed:linear", "tool": "get_issue"},
                }
            ),
        )

        with pytest.raises(BuiltinSpecError) as excinfo:
            load_builtin_specs(tmp_path)

        assert "bad_spec.json" in str(excinfo.value)

    def test_unknown_archetype_raises_naming_the_file(self, tmp_path: Path) -> None:
        self._write(
            tmp_path,
            "weird.json",
            json.dumps(
                {
                    "spec_version": 1,
                    "archetype": "hologram",
                    "source": {"server": "seed:x", "tool": "t"},
                    "title_path": "a.b",
                }
            ),
        )

        with pytest.raises(BuiltinSpecError) as excinfo:
            load_builtin_specs(tmp_path)

        assert "weird.json" in str(excinfo.value)

    def test_duplicate_server_tool_raises(self, tmp_path: Path) -> None:
        for name in ("first.json", "second.json"):
            self._write(
                tmp_path,
                name,
                json.dumps(
                    {
                        "spec_version": 1,
                        "archetype": "record",
                        "source": {"server": "seed:dup", "tool": "get"},
                        "title_path": "a.b",
                    }
                ),
            )

        with pytest.raises(BuiltinSpecError) as excinfo:
            load_builtin_specs(tmp_path)

        assert "duplicate" in str(excinfo.value).lower()
