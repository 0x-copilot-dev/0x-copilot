from __future__ import annotations

from pathlib import Path

import pytest

from agent_runtime.capabilities.skills.manifest import (
    MAX_SKILL_DESCRIPTION_LENGTH,
    SkillErrorCode,
    SkillManifestError,
)
from agent_runtime.capabilities.skills.constants import Messages
from tests.unit.agent_runtime.skills.helpers import SkillManifestTestMixin


class TestSkillManifest(SkillManifestTestMixin):
    def test_parse_skill_manifest_accepts_valid_frontmatter(self) -> None:
        manifest = self.parse(self.Samples.VALID)

        assert manifest.name == self.Expected.NAME
        assert manifest.description == self.Expected.DESCRIPTION
        assert manifest.license == self.Expected.LICENSE
        assert manifest.compatibility == self.Expected.COMPATIBILITY
        assert manifest.allowed_tools == self.Expected.ALLOWED_TOOLS
        assert manifest.metadata == self.Expected.METADATA

    def test_parse_skill_manifest_rejects_missing_required_fields(self) -> None:
        with pytest.raises(SkillManifestError) as exc_info:
            self.parse(self.Samples.MISSING_DESCRIPTION)

        self.assert_skill_error(exc_info, SkillErrorCode.MISSING_REQUIRED_FIELD)
        assert exc_info.value.safe_message == Messages.Errors.FRONTMATTER_INVALID

    def test_parse_skill_manifest_rejects_empty_or_malformed_frontmatter(self) -> None:
        with pytest.raises(SkillManifestError) as exc_info:
            self.parse("")

        self.assert_skill_error(exc_info, SkillErrorCode.EMPTY_SKILL)

        with pytest.raises(SkillManifestError) as malformed_exc:
            self.parse(self.Samples.MALFORMED)

        self.assert_skill_error(malformed_exc, SkillErrorCode.MALFORMED_FRONTMATTER)

    def test_parse_skill_manifest_enforces_description_limit(self) -> None:
        too_long_description = "a" * (MAX_SKILL_DESCRIPTION_LENGTH + 1)
        with pytest.raises(SkillManifestError) as exc_info:
            self.parse(
                f"""---
name: research-plan
description: {too_long_description}
---
# Research Plan
"""
            )

        self.assert_skill_error(exc_info, SkillErrorCode.INVALID_MANIFEST)

    def test_read_skill_manifest_rejects_unsafe_asset_paths(
        self,
        tmp_path: Path,
    ) -> None:
        skill_dir = tmp_path / "unsafe-skill"
        self.write_skill(skill_dir, self.Samples.UNSAFE_ASSET)

        with pytest.raises(SkillManifestError) as exc_info:
            self.read(skill_dir)

        self.assert_skill_error(exc_info, SkillErrorCode.UNSAFE_ASSET_PATH)

    def test_read_skill_manifest_rejects_missing_asset(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "missing-asset-skill"
        self.write_skill(skill_dir, self.Samples.MISSING_ASSET)

        with pytest.raises(SkillManifestError) as exc_info:
            self.read(skill_dir)

        self.assert_skill_error(exc_info, SkillErrorCode.MISSING_ASSET)
