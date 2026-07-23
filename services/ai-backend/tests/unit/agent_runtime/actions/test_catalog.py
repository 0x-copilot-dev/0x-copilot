"""ActionCatalog loader behaviour (PRD-C1 rung 1).

The launch catalog loads once at import; every builtin-spec tool is a READ;
duplicate ops and malformed files raise at load; slug normalization matches the
builtin surface index.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_runtime.capabilities.actions.catalog import (
    ACTION_CATALOG,
    ActionCatalog,
    ActionCatalogError,
)
from agent_runtime.capabilities.actions.contracts import CatalogActionKind

# The twelve builtin-spec (server, tool) pairs — all reads — that MUST be in
# the catalog (mirrors the builtin_specs/ directory).
_BUILTIN_SPEC_TOOLS = [
    ("asana", "list_tasks"),
    ("atlassian", "get_issue"),
    ("atlassian", "search_issues"),
    ("github", "get_issue"),
    ("github", "get_pull_request"),
    ("github", "list_issues"),
    ("github", "list_pull_requests"),
    ("intercom", "list_conversations"),
    ("linear", "get_issue"),
    ("linear", "list_issues"),
    ("notion", "get_page"),
    ("sentry", "list_issues"),
]


class TestLaunchCatalog:
    def test_all_twelve_builtin_spec_tools_marked_read(self) -> None:
        for connector, op in _BUILTIN_SPEC_TOOLS:
            kind = ACTION_CATALOG.lookup(connector, op)
            assert kind is CatalogActionKind.READ, f"{connector}.{op} -> {kind}"

    def test_seven_launch_connectors_present(self) -> None:
        connectors = {c for (c, _op) in ACTION_CATALOG.all_entries()}
        assert connectors == {
            "asana",
            "atlassian",
            "github",
            "intercom",
            "linear",
            "notion",
            "sentry",
        }

    def test_lookup_uses_slug_normalization(self) -> None:
        # seed:linear -> linear; GET_ISSUE -> get_issue.
        assert (
            ACTION_CATALOG.lookup("seed:linear", "GET_ISSUE") is CatalogActionKind.READ
        )

    def test_unknown_op_returns_none(self) -> None:
        assert ACTION_CATALOG.lookup("linear", "frobnicate") is None
        assert ACTION_CATALOG.lookup("nonexistent", "whatever") is None

    def test_destructive_ops_classified_destructive(self) -> None:
        assert (
            ACTION_CATALOG.lookup("github", "delete_repository")
            is CatalogActionKind.DESTRUCTIVE
        )


class TestLoaderFailClosed:
    def _write(self, directory: Path, name: str, body: object) -> None:
        (directory / name).write_text(
            json.dumps(body) if not isinstance(body, str) else body,
            encoding="utf-8",
        )

    def test_duplicate_op_raises(self, tmp_path: Path) -> None:
        # Two files declaring the same (connector, op) after normalization.
        self._write(
            tmp_path,
            "a.json",
            {
                "catalog_version": 1,
                "connector": "linear",
                "operations": {"get": "read"},
            },
        )
        self._write(
            tmp_path,
            "b.json",
            {
                "catalog_version": 1,
                "connector": "seed:linear",
                "operations": {"GET": "write"},
            },
        )
        with pytest.raises(ActionCatalogError) as exc:
            ActionCatalog.from_directory(tmp_path)
        assert "duplicate" in str(exc.value)

    def test_malformed_json_breaks_load(self, tmp_path: Path) -> None:
        self._write(tmp_path, "bad.json", "{ not json")
        with pytest.raises(ActionCatalogError) as exc:
            ActionCatalog.from_directory(tmp_path)
        assert "bad.json" in str(exc.value)

    def test_unknown_kind_value_breaks_load(self, tmp_path: Path) -> None:
        self._write(
            tmp_path,
            "x.json",
            {
                "catalog_version": 1,
                "connector": "linear",
                "operations": {"get": "sideways"},
            },
        )
        with pytest.raises(ActionCatalogError) as exc:
            ActionCatalog.from_directory(tmp_path)
        assert "x.json" in str(exc.value)

    def test_extra_top_level_key_breaks_load(self, tmp_path: Path) -> None:
        # extra="forbid" on the file model: an unexpected top-level key raises.
        self._write(
            tmp_path,
            "x.json",
            {
                "catalog_version": 1,
                "connector": "linear",
                "operations": {"get": "read"},
                "surprise": True,
            },
        )
        with pytest.raises(ActionCatalogError):
            ActionCatalog.from_directory(tmp_path)

    def test_empty_dir_loads_empty_catalog(self, tmp_path: Path) -> None:
        catalog = ActionCatalog.from_directory(tmp_path)
        assert catalog.all_entries() == {}
        assert catalog.lookup("linear", "get_issue") is None
