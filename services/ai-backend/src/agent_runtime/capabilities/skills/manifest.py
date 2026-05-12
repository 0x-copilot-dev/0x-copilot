"""Agent Skills-compatible SKILL.md manifest parsing."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import TypeAlias

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from agent_runtime.capabilities.skills.constants import Keys, Limits, Messages, Patterns

JsonScalar: TypeAlias = str | int | float | bool | None

MAX_SKILL_DESCRIPTION_LENGTH = Limits.SKILL_DESCRIPTION_MAX_LENGTH
MAX_SKILL_FILE_BYTES = Limits.SKILL_FILE_MAX_BYTES


class SkillErrorCode(StrEnum):
    """Typed skills middleware failures safe for public surfaces."""

    EMPTY_SKILL = "empty_skill"
    MISSING_FRONTMATTER = "missing_frontmatter"
    MALFORMED_FRONTMATTER = "malformed_frontmatter"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    INVALID_MANIFEST = "invalid_manifest"
    SKILL_TOO_LARGE = "skill_too_large"
    SOURCE_NOT_READABLE = "source_not_readable"
    UNSAFE_ASSET_PATH = "unsafe_asset_path"
    MISSING_ASSET = "missing_asset"


class SkillManifestError(Exception):
    """Safe skills middleware error with a stable code."""

    def __init__(
        self,
        code: SkillErrorCode,
        safe_message: str,
        *,
        skill_path: Path | None = None,
    ) -> None:
        """Initialise with a typed error code, a safe public message, and optional skill path."""
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.skill_path = skill_path


class SkillManifest(BaseModel):
    """Validated product-side view of a SKILL.md frontmatter block."""

    model_config = ConfigDict(
        extra=Keys.Pydantic.FORBID,
        frozen=True,
        validate_assignment=True,
    )

    name: str
    description: str = Field(
        min_length=1,
        max_length=Limits.SKILL_DESCRIPTION_MAX_LENGTH,
    )
    license: str | None = None
    compatibility: frozenset[str] = Field(default_factory=frozenset)
    allowed_tools: frozenset[str] = Field(default_factory=frozenset)
    metadata: dict[str, JsonScalar] = Field(default_factory=dict)

    @field_validator(Keys.Fields.NAME)
    @classmethod
    def _normalize_name(cls, value: object) -> str:
        """Coerce the skill name to a stable slug identifier."""
        return SkillTextNormalizer.normalize_slug(value, Keys.Fields.NAME)

    @field_validator(Keys.Fields.DESCRIPTION)
    @classmethod
    def _normalize_description(cls, value: object) -> str:
        """Strip and validate the skill description."""
        return SkillTextNormalizer.normalize_nonempty_string(
            value,
            Keys.Fields.DESCRIPTION,
        )

    @field_validator(Keys.Fields.LICENSE)
    @classmethod
    def _normalize_license(cls, value: object) -> str | None:
        """Strip and validate the optional license string."""
        if value is None:
            return None
        return SkillTextNormalizer.normalize_nonempty_string(value, Keys.Fields.LICENSE)

    @field_validator(Keys.Fields.COMPATIBILITY, mode=Keys.Pydantic.BEFORE)
    @classmethod
    def _normalize_compatibility(cls, value: object) -> frozenset[str]:
        """Coerce compatibility values to a frozenset of non-empty strings."""
        return frozenset(
            SkillTextNormalizer.normalize_nonempty_string(
                item,
                Keys.Fields.COMPATIBILITY,
            )
            for item in SkillTextNormalizer.coerce_iterable(
                value,
                Keys.Fields.COMPATIBILITY,
            )
        )

    @field_validator(Keys.Fields.ALLOWED_TOOLS, mode=Keys.Pydantic.BEFORE)
    @classmethod
    def _normalize_allowed_tools(cls, value: object) -> frozenset[str]:
        """Coerce allowed-tools values to a frozenset of slug strings."""
        return frozenset(
            SkillTextNormalizer.normalize_slug(item, Keys.Fields.ALLOWED_TOOLS)
            for item in SkillTextNormalizer.coerce_iterable(
                value,
                Keys.Fields.ALLOWED_TOOLS,
            )
        )

    @field_validator(Keys.Fields.METADATA, mode=Keys.Pydantic.BEFORE)
    @classmethod
    def _normalize_metadata(cls, value: object) -> dict[str, JsonScalar]:
        """Coerce the metadata dict; fold unknown top-level scalar keys into it."""
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError(Messages.Validation.METADATA_MAPPING_REQUIRED)

        normalized: dict[str, JsonScalar] = {}
        for key, metadata_value in value.items():
            metadata_key = SkillTextNormalizer.normalize_metadata_key(key)
            if not SkillTextNormalizer.is_json_scalar(metadata_value):
                raise ValueError(Messages.Validation.METADATA_VALUES_SCALAR)
            normalized[metadata_key] = metadata_value
        return normalized


class SkillManifestParser:
    """Parser for YAML-like Agent Skills frontmatter."""

    @classmethod
    def parse(cls, markdown: str, *, skill_path: Path | None = None) -> SkillManifest:
        """Parse and validate the frontmatter from a SKILL.md file."""

        frontmatter, _body = cls.split_frontmatter(markdown, skill_path=skill_path)
        raw_manifest = cls.parse_frontmatter(frontmatter, skill_path=skill_path)
        normalized_manifest = cls.move_unknown_scalars_to_metadata(raw_manifest)

        try:
            return SkillManifest.model_validate(normalized_manifest)
        except ValidationError as exc:
            raise SkillManifestError(
                cls.error_code_from_validation(exc),
                Messages.Errors.FRONTMATTER_INVALID,
                skill_path=skill_path,
            ) from exc

    @classmethod
    def split_frontmatter(
        cls,
        markdown: str,
        *,
        skill_path: Path | None,
    ) -> tuple[str, str]:
        """Split ``markdown`` into ``(frontmatter, body)`` at the ``---`` delimiters."""
        if not markdown.strip():
            raise SkillManifestError(
                SkillErrorCode.EMPTY_SKILL,
                Messages.Errors.SKILL_EMPTY,
                skill_path=skill_path,
            )

        lines = markdown.splitlines()
        if not lines or lines[0].strip() != Keys.Frontmatter.BOUNDARY:
            raise SkillManifestError(
                SkillErrorCode.MISSING_FRONTMATTER,
                Messages.Errors.FRONTMATTER_MISSING,
                skill_path=skill_path,
            )

        for end_index, line in enumerate(lines[1:], start=1):
            if line.strip() == Keys.Frontmatter.BOUNDARY:
                frontmatter = "\n".join(lines[1:end_index])
                body = "\n".join(lines[end_index + 1 :])
                if not frontmatter.strip():
                    raise SkillManifestError(
                        SkillErrorCode.MALFORMED_FRONTMATTER,
                        Messages.Errors.FRONTMATTER_EMPTY,
                        skill_path=skill_path,
                    )
                return frontmatter, body

        raise SkillManifestError(
            SkillErrorCode.MISSING_FRONTMATTER,
            Messages.Errors.FRONTMATTER_UNCLOSED,
            skill_path=skill_path,
        )

    @classmethod
    def parse_frontmatter(
        cls,
        frontmatter: str,
        *,
        skill_path: Path | None,
    ) -> dict[str, object]:
        """Parse YAML from a raw frontmatter string; raise ``SkillManifestError`` on failure."""
        try:
            parsed = yaml.safe_load(frontmatter)
        except yaml.YAMLError as exc:
            raise SkillManifestError(
                SkillErrorCode.MALFORMED_FRONTMATTER,
                Messages.Errors.FRONTMATTER_MALFORMED,
                skill_path=skill_path,
            ) from exc
        if not isinstance(parsed, dict):
            raise SkillManifestError(
                SkillErrorCode.MALFORMED_FRONTMATTER,
                Messages.Errors.FRONTMATTER_MALFORMED,
                skill_path=skill_path,
            )
        return parsed

    @classmethod
    def move_unknown_scalars_to_metadata(
        cls,
        raw_manifest: dict[str, object],
    ) -> dict[str, object]:
        """Fold any unknown scalar top-level keys into the ``metadata`` dict."""
        metadata = dict(raw_manifest.get(Keys.Fields.METADATA) or {})
        normalized: dict[str, object] = {}

        for key, value in raw_manifest.items():
            if key in Keys.Frontmatter.KNOWN_KEYS:
                normalized[key] = value
                continue
            if SkillTextNormalizer.is_json_scalar(value):
                metadata[SkillTextNormalizer.normalize_metadata_key(key)] = value

        if metadata:
            normalized[Keys.Fields.METADATA] = metadata
        return normalized

    @classmethod
    def error_code_from_validation(cls, exc: ValidationError) -> SkillErrorCode:
        """Map a Pydantic ``ValidationError`` to the most specific ``SkillErrorCode``."""
        missing_required = any(
            error[Keys.Pydantic.ERROR_TYPE] == Keys.Pydantic.MISSING
            for error in exc.errors()
        )
        if missing_required:
            return SkillErrorCode.MISSING_REQUIRED_FIELD
        return SkillErrorCode.INVALID_MANIFEST


class SkillManifestReader:
    """Reader for skill directories containing a SKILL.md manifest."""

    @classmethod
    def read(
        cls,
        skill_directory: str | Path,
        *,
        max_bytes: int = Limits.SKILL_FILE_MAX_BYTES,
        validate_assets: bool = True,
    ) -> SkillManifest:
        """Read and validate a skill directory's SKILL.md manifest."""

        directory = Path(skill_directory)
        skill_path = directory / Keys.Files.SKILL_MD
        cls.validate_file_size(skill_path, max_bytes=max_bytes)
        markdown = cls.read_markdown(skill_path)

        manifest = SkillManifestParser.parse(markdown, skill_path=skill_path)
        if validate_assets:
            _frontmatter, body = SkillManifestParser.split_frontmatter(
                markdown,
                skill_path=skill_path,
            )
            SkillAssetReferenceValidator.validate(
                body,
                directory,
                skill_path=skill_path,
            )
        return manifest

    @classmethod
    def validate_file_size(cls, skill_path: Path, *, max_bytes: int) -> None:
        """Raise ``SkillManifestError`` when ``skill_path`` exceeds ``max_bytes``."""
        try:
            file_size = skill_path.stat().st_size
        except OSError as exc:
            raise SkillManifestError(
                SkillErrorCode.MISSING_FRONTMATTER,
                Messages.Errors.SKILL_READ_FAILED,
                skill_path=skill_path,
            ) from exc

        if file_size > max_bytes:
            raise SkillManifestError(
                SkillErrorCode.SKILL_TOO_LARGE,
                Messages.Errors.SKILL_TOO_LARGE,
                skill_path=skill_path,
            )

    @classmethod
    def read_markdown(cls, skill_path: Path) -> str:
        """Read UTF-8 text from ``skill_path``; raise ``SkillManifestError`` on OS errors."""
        try:
            return skill_path.read_text(encoding=Keys.Encoding.UTF_8)
        except OSError as exc:
            raise SkillManifestError(
                SkillErrorCode.MISSING_FRONTMATTER,
                Messages.Errors.SKILL_READ_FAILED,
                skill_path=skill_path,
            ) from exc


class SkillAssetReferenceValidator:
    """Validator for local Markdown asset references in SKILL.md content."""

    @classmethod
    def validate(
        cls,
        markdown_body: str,
        skill_directory: str | Path,
        *,
        skill_path: Path | None = None,
    ) -> None:
        """Validate all local Markdown link targets in ``markdown_body`` against ``skill_directory``."""
        directory = Path(skill_directory)
        for raw_reference in Patterns.MARKDOWN_LINK.findall(markdown_body):
            reference = raw_reference.strip().split(Keys.Characters.HASH, maxsplit=1)[0]
            if cls.should_skip_reference(reference):
                continue
            cls.validate_local_reference(reference, directory, skill_path=skill_path)

    @classmethod
    def validate_local_reference(
        cls,
        reference: str,
        directory: Path,
        *,
        skill_path: Path | None,
    ) -> None:
        """Validate a single local reference path; raise on traversal or missing file."""
        reference_path = Path(reference)
        if reference_path.is_absolute() or Keys.Characters.SLASH_DOT_DOT in (
            reference_path.parts
        ):
            raise SkillManifestError(
                SkillErrorCode.UNSAFE_ASSET_PATH,
                Messages.Errors.ASSET_UNSAFE,
                skill_path=skill_path,
            )

        resolved = (directory / reference_path).resolve(strict=False)
        try:
            resolved.relative_to(directory.resolve(strict=False))
        except ValueError as exc:
            raise SkillManifestError(
                SkillErrorCode.UNSAFE_ASSET_PATH,
                Messages.Errors.ASSET_UNSAFE,
                skill_path=skill_path,
            ) from exc

        if not resolved.exists():
            raise SkillManifestError(
                SkillErrorCode.MISSING_ASSET,
                Messages.Errors.ASSET_MISSING,
                skill_path=skill_path,
            )

    @classmethod
    def should_skip_reference(cls, reference: str) -> bool:
        """Return ``True`` when ``reference`` is empty or an external URL."""
        return not reference or cls.is_external_reference(reference)

    @classmethod
    def is_external_reference(cls, reference: str) -> bool:
        """Return ``True`` when ``reference`` has an external URI scheme."""
        lowered = reference.lower()
        return (
            Keys.Links.SCHEME_SEPARATOR in lowered
            or lowered.startswith(Keys.Links.MAILTO)
            or lowered.startswith(Keys.Links.DATA)
            or lowered.startswith(Keys.Links.FRAGMENT)
        )


class SkillTextNormalizer:
    """Reusable normalizers for skills Pydantic boundaries.

    Common methods delegate to the shared ``ValueNormalizer``;
    skills-specific helpers (metadata keys, scalar checks) remain here.
    """

    from agent_runtime.validation import ValueNormalizer as _V

    normalize_nonempty_string = _V.normalize_nonempty_string
    normalize_slug = _V.normalize_slug
    coerce_iterable = _V.coerce_iterable

    del _V

    @classmethod
    def normalize_metadata_key(cls, value: object) -> str:
        """Validate and strip a metadata key; raise on non-string or empty."""
        if not isinstance(value, str):
            raise ValueError(Messages.Validation.METADATA_KEY_STRING)
        normalized = value.strip()
        if not normalized:
            raise ValueError(Messages.Validation.METADATA_KEY_NONEMPTY)
        return normalized

    @classmethod
    def is_json_scalar(cls, value: object) -> bool:
        """Return ``True`` when ``value`` is a JSON-serialisable scalar."""
        return value is None or isinstance(value, str | int | float | bool)
