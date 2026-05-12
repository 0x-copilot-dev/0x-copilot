"""Project the runtime's filesystem-shipped skills into a settings-UI shape.

Backend's `/v1/skills` only knows about its own store (user + preloaded skills).
The runtime ships infrastructure skills like `search-subagent-logs` from
`services/ai-backend/skills/`; those reach the model via Deep Agents' native
skill discovery but are invisible to the settings page until something emits
them in the `Skill` payload shape that the UI already understands.

This module is read-only: SKILL.md files on disk are the source of truth, the
endpoint just reads + projects them. No persistence.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_runtime.capabilities.skills.manifest import (
    SkillManifestParser,
    SkillManifestReader,
)
from agent_runtime.capabilities.skills.sources import (
    SkillSource,
    SkillSourceConfig,
    SkillSourceRegistry,
)
from runtime_worker.dependencies import BUILTIN_SKILLS_ROOT


_SOURCE_TYPE_SYSTEM = "system"
_SCOPE_USER = "user"
_SKILL_ID_PREFIX = "system:"
_VIRTUAL_PATH_PREFIX = "/skills/system/"


class SystemSkillResponse(BaseModel):
    """Public-shape skill record for runtime filesystem skills.

    Matches the wire shape that backend's `SkillResponse` produces for user /
    preloaded skills, so the facade can concatenate without reshaping. Fields
    not meaningful for system skills (`scope`, `metadata`, timestamps) carry
    sensible static or filesystem-derived values.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_id: str
    name: str
    display_name: str
    description: str
    markdown: str
    virtual_path: str
    enabled: bool
    scope: str
    source_type: str
    version: int
    allowed_tools: tuple[str, ...]
    compatibility: tuple[str, ...]
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class SystemSkillListResponse(BaseModel):
    """Listing payload returned by `GET /internal/v1/skills/system`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    skills: tuple[SystemSkillResponse, ...]


class SystemSkillsProjector:
    """Read filesystem skills under `BUILTIN_SKILLS_ROOT` into the UI wire shape.

    Why a class: keeps the projection helpers grouped and trivially mockable
    in tests (a fixture can substitute `root` via the constructor).
    """

    def __init__(
        self,
        root: Path = BUILTIN_SKILLS_ROOT,
    ) -> None:
        self._root = root

    def list_skills(self) -> SystemSkillListResponse:
        """Discover and project all skills under the builtin skills root."""
        if not self._root.is_dir():
            return SystemSkillListResponse(skills=())

        config = SkillSourceConfig(
            sources=(SkillSource(path=self._root, precedence=0),),
        )
        discovered = SkillSourceRegistry.discover_configured_skills(config)

        skills = tuple(self._project(item.skill_directory) for item in discovered)
        return SystemSkillListResponse(skills=skills)

    @classmethod
    def _project(cls, skill_directory: Path) -> SystemSkillResponse:
        """Read and project one skill directory into the public wire shape."""
        skill_path = skill_directory / "SKILL.md"
        markdown = SkillManifestReader.read_markdown(skill_path)
        manifest = SkillManifestParser.parse(markdown, skill_path=skill_path)
        modified_at = cls._file_mtime(skill_path)
        return SystemSkillResponse(
            skill_id=f"{_SKILL_ID_PREFIX}{manifest.name}",
            name=manifest.name,
            display_name=cls._display_name(manifest.name),
            description=manifest.description,
            markdown=markdown,
            virtual_path=f"{_VIRTUAL_PATH_PREFIX}{manifest.name}/SKILL.md",
            enabled=True,
            scope=_SCOPE_USER,
            source_type=_SOURCE_TYPE_SYSTEM,
            version=1,
            allowed_tools=tuple(sorted(manifest.allowed_tools)),
            compatibility=tuple(sorted(manifest.compatibility)),
            metadata=dict(manifest.metadata),
            created_at=modified_at,
            updated_at=modified_at,
        )

    @staticmethod
    def _display_name(slug: str) -> str:
        """Convert a kebab/snake-case skill slug into a title-cased display name."""
        return " ".join(part.capitalize() for part in slug.replace("_", "-").split("-"))

    @staticmethod
    def _file_mtime(path: Path) -> datetime:
        """Return the file's modification time as a UTC datetime; falls back to now on OSError."""
        try:
            stat = path.stat()
        except OSError:
            return datetime.now(timezone.utc)
        return datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
