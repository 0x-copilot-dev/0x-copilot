"""Unit tests for the packaged spec-authoring skill bundle (generative-UI PRD-07).

The skill is data that steers a nano model; a broken example or a spec that does
not lint against its own sample would silently degrade generation quality, so we
gate the whole bundle here.
"""

from __future__ import annotations

from agent_runtime.capabilities.surfaces.generator import (
    SpecAuthoringSkill,
    SurfaceSpecLinter,
)
from agent_runtime.capabilities.surfaces.spec_models import validate_surface_spec

_REQUIRED_ARCHETYPES = {"record", "table", "message", "doc", "board"}


class TestSpecAuthoringSkill:
    def test_manifest_fields(self) -> None:
        skill = SpecAuthoringSkill.load()
        assert skill.skill_version == 2
        assert skill.model_hint == "nano"
        assert skill.max_retries == 1

    def test_has_at_least_eight_examples(self) -> None:
        assert len(SpecAuthoringSkill.load().examples) >= 8

    def test_examples_cover_the_core_archetypes(self) -> None:
        archetypes = {
            validate_surface_spec(ex["spec"]).archetype.value
            for ex in SpecAuthoringSkill.load().examples
        }
        assert _REQUIRED_ARCHETYPES.issubset(archetypes)

    def test_every_example_spec_validates_and_lints(self) -> None:
        for ex in SpecAuthoringSkill.load().examples:
            spec = validate_surface_spec(ex["spec"])
            lint = SurfaceSpecLinter.lint(spec, ex["sample_output"])
            assert lint.ok, f"{spec.source.tool}: {lint.reason}"

    def test_system_prompt_includes_doctrine_and_examples(self) -> None:
        prompt = SpecAuthoringSkill.load().system_prompt()
        normalized = " ".join(prompt.lower().split())
        assert "archetype" in prompt.lower()
        assert "untrusted" in prompt.lower()
        assert "connector identity does not choose the archetype" in normalized
        assert "renderer owns visual fidelity" in normalized
        assert "local filesystem path is text" in normalized
        # Few-shot examples are serialized into the prompt.
        assert "title_path" in prompt

    def test_includes_a_sparse_minimal_spec_example(self) -> None:
        # At least one example is a minimal record (title + a lone field, no link).
        minimal = [
            validate_surface_spec(ex["spec"])
            for ex in SpecAuthoringSkill.load().examples
        ]
        assert any(
            spec.archetype.value == "record"
            and spec.link is None
            and (spec.fields is None or len(spec.fields) <= 1)
            for spec in minimal
        )

    def test_examples_cover_local_filesystem_shapes(self) -> None:
        tools = {
            ex["tool_descriptor"]["name"] for ex in SpecAuthoringSkill.load().examples
        }
        assert {"read_markdown", "list_directory"}.issubset(tools)
