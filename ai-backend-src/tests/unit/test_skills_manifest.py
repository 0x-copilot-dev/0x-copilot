from __future__ import annotations

from pathlib import Path

import pytest

from enterprise_search_ai.skills.constants import Keys, Messages
from enterprise_search_ai.skills.manifest import (
    MAX_SKILL_DESCRIPTION_LENGTH,
    SkillErrorCode,
    SkillManifestError,
    SkillManifestParser,
    SkillManifestReader,
)


class SkillManifestTestMixin:
    class Samples:
        VALID = """---
name: Research-Plan
description: Use when creating source-backed executive research plans.
license: MIT
compatibility:
  - deepagents
allowed_tools: [doc_search]
metadata:
  owner: ai-platform
---
# Research Plan
"""
        MISSING_DESCRIPTION = """---
name: research-plan
---
# Research Plan
"""
        MALFORMED = """---
name research-plan
---
# Research Plan
"""
        UNSAFE_ASSET = """---
name: unsafe-skill
description: Use when testing unsafe asset references.
---
Read [outside](../secret.txt).
"""
        MISSING_ASSET = """---
name: missing-asset-skill
description: Use when testing missing asset references.
---
Read [template](assets/template.md).
"""

    class Expected:
        NAME = "research-plan"
        DESCRIPTION = "Use when creating source-backed executive research plans."
        LICENSE = "MIT"
        COMPATIBILITY = frozenset({"deepagents"})
        ALLOWED_TOOLS = frozenset({"doc_search"})
        METADATA = {"owner": "ai-platform"}

    def parse(self, markdown: str):
        return SkillManifestParser.parse(markdown)

    def read(self, skill_dir: Path):
        return SkillManifestReader.read(skill_dir)

    def write_skill(self, skill_dir: Path, markdown: str) -> None:
        skill_dir.mkdir()
        (skill_dir / Keys.Files.SKILL_MD).write_text(
            markdown,
            encoding=Keys.Encoding.UTF_8,
        )

    def assert_skill_error(
        self,
        exc_info: pytest.ExceptionInfo[SkillManifestError],
        code: SkillErrorCode,
    ) -> None:
        assert exc_info.value.code == code


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
