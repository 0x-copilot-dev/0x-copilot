"""Configured skill source paths and deterministic source precedence."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from enterprise_search_ai.skills.constants import Keys, Limits, Messages
from enterprise_search_ai.skills.manifest import (
    SkillErrorCode,
    SkillManifest,
    SkillManifestError,
    SkillManifestReader,
    SkillTextNormalizer,
)


class SkillContract(BaseModel):
    """Base model for typed skills middleware contracts."""

    model_config = ConfigDict(
        extra=Keys.Pydantic.FORBID,
        frozen=True,
        validate_assignment=True,
    )


class SkillSourceScope(StrEnum):
    """Agent scopes that may receive a configured skill source."""

    MAIN_AGENT = "main_agent"
    SUBAGENT = "subagent"
    SHARED = "shared"


class SkillSource(SkillContract):
    """Local skill root configured for Deep Agents skill discovery."""

    path: Path
    precedence: int = Field(default=0, ge=0, le=Limits.SOURCE_PRECEDENCE_MAX)
    scope: frozenset[SkillSourceScope] = Field(
        default_factory=lambda: frozenset({SkillSourceScope.SHARED})
    )
    writable: bool = False

    @field_validator(Keys.Fields.PATH, mode=Keys.Pydantic.BEFORE)
    @classmethod
    def _normalize_path(cls, value: object) -> Path:
        return SkillSourceNormalizer.normalize_path(value, Keys.Fields.PATH)

    @field_validator(Keys.Fields.SCOPE, mode=Keys.Pydantic.BEFORE)
    @classmethod
    def _normalize_scope(cls, value: object) -> frozenset[SkillSourceScope]:
        return SkillSourceNormalizer.normalize_scope(value)


class SkillSourceConfig(SkillContract):
    """Request-scoped skills configuration consumed by the runtime factory."""

    roots: tuple[str, ...] = Field(default_factory=tuple)
    sources: tuple[SkillSource, ...] = Field(default_factory=tuple)
    enabled: bool = True

    @field_validator(Keys.Fields.ROOTS, mode=Keys.Pydantic.BEFORE)
    @classmethod
    def _normalize_roots(cls, value: object) -> tuple[str, ...]:
        return SkillSourceNormalizer.normalize_roots(value)

    def as_sources(self) -> tuple[SkillSource, ...]:
        """Return legacy roots and explicit sources as one precedence-ordered list."""

        legacy_sources = tuple(
            SkillSource(path=root, precedence=index)
            for index, root in enumerate(self.roots)
        )
        return tuple(
            sorted(
                (*legacy_sources, *self.sources),
                key=SkillSourceRegistry.source_sort_key,
            )
        )

    def skill_directories_for_deep_agent(self) -> tuple[str, ...]:
        """Return source roots ordered for Deep Agents' last-source-wins behavior."""

        return SkillSourceRegistry.skill_directories_for_deep_agent(self)


class ConfiguredSkill(SkillContract):
    """A discovered skill manifest plus its configured source metadata."""

    manifest: SkillManifest
    source: SkillSource
    skill_directory: Path

    @field_validator(Keys.Fields.SKILL_DIRECTORY, mode=Keys.Pydantic.BEFORE)
    @classmethod
    def _normalize_skill_directory(cls, value: object) -> Path:
        return SkillSourceNormalizer.normalize_path(value, Keys.Fields.SKILL_DIRECTORY)


class SkillSourceRegistry:
    """Discovery and source precedence behavior for configured skill roots."""

    @classmethod
    def discover_configured_skills(
        cls,
        config: SkillSourceConfig,
    ) -> tuple[ConfiguredSkill, ...]:
        """Read configured sources and apply deterministic duplicate-name precedence."""

        if not config.enabled:
            return ()

        selected_by_name: dict[str, ConfiguredSkill] = {}
        for source in config.as_sources():
            for skill_directory in cls.iter_skill_directories(source):
                manifest = SkillManifestReader.read(skill_directory)
                selected_by_name[manifest.name] = ConfiguredSkill(
                    manifest=manifest,
                    source=source,
                    skill_directory=skill_directory,
                )

        return tuple(selected_by_name[name] for name in sorted(selected_by_name))

    @classmethod
    def skill_directories_for_deep_agent(
        cls,
        config: SkillSourceConfig,
    ) -> tuple[str, ...]:
        """Return source roots ordered for Deep Agents' last-source-wins precedence."""

        if not config.enabled:
            return ()
        return tuple(str(source.path) for source in config.as_sources())

    @classmethod
    def iter_skill_directories(cls, source: SkillSource) -> tuple[Path, ...]:
        try:
            if not source.path.exists() or not source.path.is_dir():
                raise OSError(Messages.Errors.SKILL_SOURCE_UNREADABLE)
            if (source.path / Keys.Files.SKILL_MD).is_file():
                return (source.path,)
            return tuple(
                child
                for child in sorted(source.path.iterdir(), key=cls.path_name)
                if child.is_dir() and (child / Keys.Files.SKILL_MD).is_file()
            )
        except OSError as exc:
            raise SkillManifestError(
                SkillErrorCode.SOURCE_NOT_READABLE,
                Messages.Errors.SKILL_SOURCE_UNREADABLE,
                skill_path=source.path,
            ) from exc

    @classmethod
    def source_sort_key(cls, source: SkillSource) -> tuple[int, str]:
        return source.precedence, str(source.path)

    @classmethod
    def path_name(cls, path: Path) -> str:
        return path.name


class SkillSourceNormalizer:
    """Normalizers for skill source contracts."""

    @classmethod
    def normalize_path(cls, value: object, field_name: str) -> Path:
        if not isinstance(value, str | Path):
            raise ValueError(Messages.Validation.path_string(field_name))
        raw_path = Path(value).expanduser()
        if not str(raw_path).strip():
            raise ValueError(Messages.Validation.nonempty_string(field_name))
        return raw_path.resolve(strict=False)

    @classmethod
    def normalize_scope(cls, value: object) -> frozenset[SkillSourceScope]:
        if value is None:
            return frozenset({SkillSourceScope.SHARED})
        if isinstance(value, str):
            values = (value,)
        else:
            try:
                values = tuple(value)  # type: ignore[arg-type]
            except TypeError as exc:
                raise ValueError(
                    Messages.Validation.string_or_iterable(Keys.Fields.SCOPE)
                ) from exc
        return frozenset(SkillSourceScope(item) for item in values)

    @classmethod
    def normalize_roots(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            values = (value,)
        else:
            try:
                values = tuple(value)  # type: ignore[arg-type]
            except TypeError as exc:
                raise ValueError(
                    Messages.Validation.string_or_iterable(Keys.Fields.ROOTS)
                ) from exc
        return tuple(
            SkillTextNormalizer.normalize_nonempty_string(root, Keys.Fields.SKILL_ROOT)
            for root in values
        )
