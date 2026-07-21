"""SurfaceSpec pydantic model + validator unit tests (PRD-01, AC2 + AC4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_runtime.capabilities.surfaces.spec_models import (
    SurfaceArchetype,
    SurfaceSpec,
    SurfaceSpecError,
    validate_surface_spec,
)

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_GOLDEN_FIXTURES = (
    ("linear_get_issue.spec.json", SurfaceArchetype.RECORD),
    ("github_list_issues.spec.json", SurfaceArchetype.TABLE),
    ("gmail_message.spec.json", SurfaceArchetype.MESSAGE),
)


class GoldenFixtureMixin:
    """Loads the committed golden SurfaceSpec fixtures."""

    @staticmethod
    def load_fixture(name: str) -> dict[str, object]:
        return json.loads((_FIXTURES_DIR / name).read_text(encoding="utf-8"))

    @staticmethod
    def valid_record_spec() -> dict[str, object]:
        return {
            "spec_version": 1,
            "archetype": "record",
            "source": {"server": "seed:linear", "tool": "get_issue"},
            "title_path": "issue.title",
        }


class TestGoldenFixtures(GoldenFixtureMixin):
    """AC4 — the 3 golden fixtures round-trip through the pydantic model."""

    @pytest.mark.parametrize(("name", "archetype"), _GOLDEN_FIXTURES)
    def test_fixture_round_trips(self, name: str, archetype: SurfaceArchetype) -> None:
        raw = self.load_fixture(name)

        spec = validate_surface_spec(raw)

        assert isinstance(spec, SurfaceSpec)
        assert spec.archetype is archetype
        assert spec.spec_version == 1
        # model_dump(mode="json") reproduces the wire dict (minus unset optionals).
        dumped = spec.model_dump(mode="json", exclude_none=True)
        assert dumped["archetype"] == raw["archetype"]
        assert dumped["title_path"] == raw["title_path"]
        assert dumped["source"] == raw["source"]

    @pytest.mark.parametrize(("name", "_archetype"), _GOLDEN_FIXTURES)
    def test_fixture_is_idempotent(self, name: str, _archetype: object) -> None:
        raw = self.load_fixture(name)

        once = validate_surface_spec(raw)
        twice = validate_surface_spec(once.model_dump(mode="json", exclude_none=True))

        assert once == twice


class TestValidatorRejections(GoldenFixtureMixin):
    """AC2 — validate_surface_spec rejects malformed specs with actionable messages."""

    def test_rejects_unknown_archetype(self) -> None:
        raw = self.valid_record_spec()
        raw["archetype"] = "spaceship"

        with pytest.raises(SurfaceSpecError) as exc_info:
            validate_surface_spec(raw)

        message = str(exc_info.value)
        assert "archetype" in message
        assert "spaceship" in message

    def test_rejects_expression_path(self) -> None:
        raw = self.valid_record_spec()
        raw["title_path"] = "issue.title(0)"

        with pytest.raises(SurfaceSpecError) as exc_info:
            validate_surface_spec(raw)

        message = str(exc_info.value)
        assert "title_path" in message
        assert "dot-path" in message

    def test_rejects_bracket_path(self) -> None:
        raw = self.valid_record_spec()
        raw["fields"] = [{"label": "State", "path": "issue.items[a]"}]

        with pytest.raises(SurfaceSpecError) as exc_info:
            validate_surface_spec(raw)

        message = str(exc_info.value)
        assert "path" in message
        assert "dot-path" in message

    def test_rejects_missing_title_path(self) -> None:
        raw = self.valid_record_spec()
        del raw["title_path"]

        with pytest.raises(SurfaceSpecError) as exc_info:
            validate_surface_spec(raw)

        message = str(exc_info.value)
        assert "title_path" in message
        assert "missing" in message

    def test_rejects_non_object(self) -> None:
        with pytest.raises(SurfaceSpecError) as exc_info:
            validate_surface_spec(["not", "an", "object"])

        assert "JSON object" in str(exc_info.value)

    def test_rejects_wrong_spec_version(self) -> None:
        raw = self.valid_record_spec()
        raw["spec_version"] = 2

        with pytest.raises(SurfaceSpecError) as exc_info:
            validate_surface_spec(raw)

        assert "spec_version" in str(exc_info.value)

    def test_rejects_oversized_label(self) -> None:
        raw = self.valid_record_spec()
        raw["fields"] = [{"label": "x" * 41, "path": "issue.title"}]

        with pytest.raises(SurfaceSpecError):
            validate_surface_spec(raw)

    def test_rejects_unknown_top_level_field(self) -> None:
        raw = self.valid_record_spec()
        raw["handler"] = "onClick"

        with pytest.raises(SurfaceSpecError):
            validate_surface_spec(raw)


class TestArrayIndexPathsAccepted(GoldenFixtureMixin):
    """Dotted array-index accessors (``a.b.0.c``) are valid dot-paths."""

    def test_accepts_array_index_segment(self) -> None:
        raw = self.valid_record_spec()
        raw["title_path"] = "issues.0.title"

        spec = validate_surface_spec(raw)

        assert spec.title_path == "issues.0.title"
