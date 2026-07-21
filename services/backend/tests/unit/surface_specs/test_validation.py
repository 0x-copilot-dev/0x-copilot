"""Unit tests for the backend's SurfaceSpec schema re-validation (PRD-08).

The backend re-validates every spec on write against the shared
``surface_spec.schema.json`` (service-contracts). These tests pin the accepted /
rejected shapes so a schema edit that changes acceptance is a deliberate,
reviewed change.
"""

from __future__ import annotations

import pytest

from backend_app.surface_specs.validation import (
    SurfaceSpecSchemaError,
    validate_surface_spec_dict,
)


def _record_spec() -> dict[str, object]:
    return {
        "spec_version": 1,
        "archetype": "record",
        "source": {"server": "seed:linear", "tool": "get_issue"},
        "title_path": "issue.title",
        "subtitle_path": "issue.identifier",
        "fields": [
            {"label": "State", "path": "issue.state.name"},
            {"label": "Updated", "path": "issue.updatedAt", "format": "datetime"},
        ],
        "link": {"label": "Open", "url_path": "issue.url"},
    }


def _table_spec() -> dict[str, object]:
    return {
        "spec_version": 1,
        "archetype": "table",
        "source": {"server": "github", "tool": "list_issues"},
        "title_path": "title",
        "items_path": "issues",
        "columns": [
            {"label": "Number", "path": "number", "format": "number", "align": "end"},
            {"label": "Title", "path": "title"},
        ],
    }


class TestValid:
    def test_accepts_minimal_record(self) -> None:
        validate_surface_spec_dict(
            {
                "spec_version": 1,
                "archetype": "record",
                "source": {"server": "x", "tool": "y"},
                "title_path": "a.b.0.c",
            }
        )

    def test_accepts_full_record(self) -> None:
        validate_surface_spec_dict(_record_spec())

    def test_accepts_table_with_columns(self) -> None:
        validate_surface_spec_dict(_table_spec())


class TestInvalid:
    def test_rejects_non_object(self) -> None:
        with pytest.raises(SurfaceSpecSchemaError):
            validate_surface_spec_dict(["not", "an", "object"])

    def test_rejects_wrong_spec_version(self) -> None:
        spec = _record_spec()
        spec["spec_version"] = 2
        with pytest.raises(SurfaceSpecSchemaError, match="spec_version"):
            validate_surface_spec_dict(spec)

    def test_rejects_unknown_archetype(self) -> None:
        spec = _record_spec()
        spec["archetype"] = "carousel"
        with pytest.raises(SurfaceSpecSchemaError, match="archetype"):
            validate_surface_spec_dict(spec)

    def test_rejects_missing_required_title_path(self) -> None:
        spec = _record_spec()
        del spec["title_path"]
        with pytest.raises(SurfaceSpecSchemaError, match="title_path"):
            validate_surface_spec_dict(spec)

    def test_rejects_missing_required_source_field(self) -> None:
        spec = _record_spec()
        spec["source"] = {"server": "x"}
        with pytest.raises(SurfaceSpecSchemaError, match="tool"):
            validate_surface_spec_dict(spec)

    def test_rejects_additional_top_level_property(self) -> None:
        spec = _record_spec()
        spec["onClick"] = "alert(1)"
        with pytest.raises(SurfaceSpecSchemaError, match="onClick"):
            validate_surface_spec_dict(spec)

    def test_rejects_additional_nested_property(self) -> None:
        spec = _record_spec()
        spec["fields"] = [{"label": "X", "path": "a", "href": "http://evil"}]
        with pytest.raises(SurfaceSpecSchemaError, match="href"):
            validate_surface_spec_dict(spec)

    def test_rejects_bad_dot_path(self) -> None:
        spec = _record_spec()
        spec["title_path"] = "issue.title; drop table"
        with pytest.raises(SurfaceSpecSchemaError, match="pattern"):
            validate_surface_spec_dict(spec)

    def test_rejects_bad_field_path(self) -> None:
        spec = _record_spec()
        spec["fields"] = [{"label": "X", "path": "a[b]"}]
        with pytest.raises(SurfaceSpecSchemaError, match="pattern"):
            validate_surface_spec_dict(spec)

    def test_rejects_over_long_label(self) -> None:
        spec = _record_spec()
        spec["fields"] = [{"label": "z" * 41, "path": "a"}]
        with pytest.raises(SurfaceSpecSchemaError, match="maxLength"):
            validate_surface_spec_dict(spec)

    def test_rejects_illegal_format_enum(self) -> None:
        spec = _record_spec()
        spec["fields"] = [{"label": "X", "path": "a", "format": "html"}]
        with pytest.raises(SurfaceSpecSchemaError, match="format|one of"):
            validate_surface_spec_dict(spec)

    def test_rejects_non_integer_spec_version(self) -> None:
        spec = _record_spec()
        spec["spec_version"] = "1"
        with pytest.raises(SurfaceSpecSchemaError):
            validate_surface_spec_dict(spec)
