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
    ShapeTemplate,
    ShapeTemplateIndex,
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


class TestShapeTemplateDerivation:
    """A curated spec read as the set of paths it binds (floor PRD §3.4)."""

    def test_table_column_paths_expand_under_the_items_marker(self) -> None:
        spec = builtin.lookup("linear", "list_issues")
        assert spec is not None

        template = ShapeTemplate.from_spec(spec)

        assert template.anchor == "issues[]"
        assert "issues[].state.name" in template.paths
        assert "team.name" in template.paths  # title_path stays absolute

    def test_record_field_paths_stay_absolute(self) -> None:
        spec = builtin.lookup("github", "get_issue")
        assert spec is not None

        template = ShapeTemplate.from_spec(spec)

        assert template.anchor is None
        assert "issue.user.login" in template.paths

    def test_the_items_anchor_is_a_precondition_not_evidence(self) -> None:
        # Scoring the anchor would let a payload buy back the very miss that
        # makes the template unusable.
        spec = builtin.lookup("linear", "list_issues")
        assert spec is not None

        template = ShapeTemplate.from_spec(spec)

        assert template.anchor not in template.paths

    def test_link_url_paths_are_excluded_from_the_evidence(self) -> None:
        # Decoration, and its base differs by archetype (row-relative for a
        # table, absolute for a record) — admitting it would put a guess inside
        # the evidence.
        spec = builtin.lookup("github", "list_issues")
        assert spec is not None
        assert spec.link is not None

        template = ShapeTemplate.from_spec(spec)

        assert spec.link.url_path not in template.paths
        assert f"issues[].{spec.link.url_path}" not in template.paths

    def test_every_shipped_template_is_usable(self) -> None:
        # A curated spec binding fewer than three paths would be silently
        # dropped from the index; none should be.
        assert len(ShapeTemplateIndex(builtin.all_specs()).templates) == len(
            builtin.all_specs()
        )


class ShapeMatchFixtureMixin:
    @staticmethod
    def linear_list_payload() -> dict[str, object]:
        return {
            "team": {"name": "Core"},
            "issues": [
                {
                    "identifier": "ENG-1",
                    "title": "Fix login",
                    "state": {"name": "In Progress"},
                    "assignee": {"displayName": "Sarah Chen"},
                    "updatedAt": "2026-08-01T10:00:00Z",
                }
            ],
        }


class TestMatchByShape(ShapeMatchFixtureMixin):
    def test_ac8_a_list_issues_shaped_payload_finds_the_curated_table(self) -> None:
        assert builtin.match_by_shape(self.linear_list_payload()) == builtin.lookup(
            "linear", "list_issues"
        )

    def test_matching_reads_the_payload_not_any_name(self) -> None:
        # No server or tool name is passed at all — that is the whole point.
        payload = self.linear_list_payload()
        del payload["team"]

        assert builtin.match_by_shape(payload) == builtin.lookup(
            "linear", "list_issues"
        )

    def test_ac10_a_superficially_similar_payload_does_not_match(self) -> None:
        # THE NEGATIVE TEST, at the matcher. Same `team` header, same `issues`
        # array of objects — every coarse heuristic says Linear. Structurally
        # it is an audit log and not one column path resolves.
        audit_log = {
            "team": {"name": "Core"},
            "issues": [
                {"event_id": "ev-1", "action": "deleted", "actor": {"email": "a@b.c"}}
            ],
        }

        assert builtin.match_by_shape(audit_log) is None

    def test_an_unrelated_payload_matches_nothing(self) -> None:
        assert builtin.match_by_shape({"deployment": {"id": "d-1", "ok": True}}) is None

    def test_matching_is_total_over_junk(self) -> None:
        for payload in ("a string", 7, None, [], {}):
            assert builtin.match_by_shape(payload) is None

    def test_matching_is_deterministic(self) -> None:
        # Ties must resolve by a fixed order, never by whatever order the spec
        # directory happened to iterate in.
        payload = self.linear_list_payload()
        results = [builtin.match_by_shape(payload) for _ in range(20)]

        assert all(result == results[0] for result in results)

    def test_a_degenerate_template_is_kept_out_of_the_index(self) -> None:
        # Two shared paths out of two is a coverage of 1.0 over a coincidence.
        degenerate = SurfaceSpec.model_validate(
            {
                "spec_version": 1,
                "archetype": "record",
                "source": {"server": "seed:tiny", "tool": "get_thing"},
                "title_path": "name",
            }
        )

        index = ShapeTemplateIndex((degenerate,))

        assert index.templates == ()
        assert index.match({"name": "anything at all"}) is None
