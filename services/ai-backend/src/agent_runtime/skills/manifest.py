"""Agent Skills-compatible SKILL.md manifest parsing."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from agent_runtime.skills.constants import Keys, Limits, Messages, Patterns

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
        return SkillTextNormalizer.normalize_slug(value, Keys.Fields.NAME)

    @field_validator(Keys.Fields.DESCRIPTION)
    @classmethod
    def _normalize_description(cls, value: object) -> str:
        return SkillTextNormalizer.normalize_nonempty_string(
            value,
            Keys.Fields.DESCRIPTION,
        )

    @field_validator(Keys.Fields.LICENSE)
    @classmethod
    def _normalize_license(cls, value: object) -> str | None:
        if value is None:
            return None
        return SkillTextNormalizer.normalize_nonempty_string(value, Keys.Fields.LICENSE)

    @field_validator(Keys.Fields.COMPATIBILITY, mode=Keys.Pydantic.BEFORE)
    @classmethod
    def _normalize_compatibility(cls, value: object) -> frozenset[str]:
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
        parsed: dict[str, object] = {}
        lines = frontmatter.splitlines()
        index = 0

        while index < len(lines):
            line = lines[index]
            if cls.should_skip_frontmatter_line(line):
                index += 1
                continue
            if cls.is_malformed_top_level_line(line):
                raise SkillManifestError(
                    SkillErrorCode.MALFORMED_FRONTMATTER,
                    Messages.Errors.FRONTMATTER_MALFORMED,
                    skill_path=skill_path,
                )

            key, raw_value = line.split(Keys.Characters.COLON, maxsplit=1)
            key = key.strip()
            if not key:
                raise SkillManifestError(
                    SkillErrorCode.MALFORMED_FRONTMATTER,
                    Messages.Errors.KEY_EMPTY,
                    skill_path=skill_path,
                )

            inline_value = raw_value.strip()
            if inline_value:
                parsed[key] = cls.parse_scalar_or_inline_list(inline_value)
                index += 1
                continue

            child_lines: list[str] = []
            index += 1
            while index < len(lines):
                child = lines[index]
                if child and not child.startswith(
                    (Keys.Characters.SPACE, Keys.Characters.TAB)
                ):
                    break
                child_lines.append(child)
                index += 1

            parsed[key] = cls.parse_block_value(child_lines, skill_path=skill_path)

        return parsed

    @classmethod
    def parse_block_value(
        cls,
        lines: list[str],
        *,
        skill_path: Path | None,
    ) -> object:
        meaningful = [line.strip() for line in lines if line.strip()]
        if not meaningful:
            return None

        if all(line.startswith(Keys.Frontmatter.LIST_PREFIX) for line in meaningful):
            return [
                cls.parse_scalar(line[len(Keys.Frontmatter.LIST_PREFIX) :].strip())
                for line in meaningful
            ]

        parsed_mapping: dict[str, JsonScalar] = {}
        for line in meaningful:
            if (
                Keys.Characters.COLON not in line
                or line.startswith(Keys.Frontmatter.LIST_PREFIX)
            ):
                raise SkillManifestError(
                    SkillErrorCode.MALFORMED_FRONTMATTER,
                    Messages.Errors.UNSUPPORTED_NESTED_VALUE,
                    skill_path=skill_path,
                )
            key, raw_value = line.split(Keys.Characters.COLON, maxsplit=1)
            parsed_mapping[SkillTextNormalizer.normalize_metadata_key(key)] = (
                cls.parse_scalar(raw_value.strip())
            )
        return parsed_mapping

    @classmethod
    def parse_scalar_or_inline_list(cls, value: str) -> object:
        if value.startswith(Keys.Characters.LEFT_BRACKET) and value.endswith(
            Keys.Characters.RIGHT_BRACKET
        ):
            inner = value[1:-1].strip()
            if not inner:
                return []
            return [
                cls.parse_scalar(part.strip())
                for part in inner.split(Keys.Characters.COMMA)
            ]
        return cls.parse_scalar(value)

    @classmethod
    def parse_scalar(cls, value: str) -> JsonScalar:
        stripped = value.strip()
        if cls.is_quoted_scalar(stripped):
            return stripped[1:-1]

        lowered = stripped.lower()
        if lowered in SkillScalarValues.NULL:
            return None
        if lowered == SkillScalarValues.TRUE:
            return True
        if lowered == SkillScalarValues.FALSE:
            return False

        try:
            return int(stripped)
        except ValueError:
            pass

        try:
            return float(stripped)
        except ValueError:
            return stripped

    @classmethod
    def move_unknown_scalars_to_metadata(
        cls,
        raw_manifest: dict[str, object],
    ) -> dict[str, object]:
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
        missing_required = any(
            error[Keys.Pydantic.ERROR_TYPE] == Keys.Pydantic.MISSING
            for error in exc.errors()
        )
        if missing_required:
            return SkillErrorCode.MISSING_REQUIRED_FIELD
        return SkillErrorCode.INVALID_MANIFEST

    @classmethod
    def should_skip_frontmatter_line(cls, line: str) -> bool:
        return not line.strip() or line.lstrip().startswith(
            Keys.Frontmatter.COMMENT_PREFIX
        )

    @classmethod
    def is_malformed_top_level_line(cls, line: str) -> bool:
        return line.startswith((Keys.Characters.SPACE, Keys.Characters.TAB)) or (
            Keys.Characters.COLON not in line
        )

    @classmethod
    def is_quoted_scalar(cls, value: str) -> bool:
        if len(value) < 2:
            return False
        return value[0] == value[-1] and value[0] in {
            Keys.Characters.QUOTE_DOUBLE,
            Keys.Characters.QUOTE_SINGLE,
        }


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
        return not reference or cls.is_external_reference(reference)

    @classmethod
    def is_external_reference(cls, reference: str) -> bool:
        lowered = reference.lower()
        return (
            Keys.Links.SCHEME_SEPARATOR in lowered
            or lowered.startswith(Keys.Links.MAILTO)
            or lowered.startswith(Keys.Links.DATA)
            or lowered.startswith(Keys.Links.FRAGMENT)
        )


class SkillTextNormalizer:
    """Reusable normalizers for skills Pydantic boundaries."""

    @classmethod
    def normalize_nonempty_string(cls, value: object, field_name: str) -> str:
        if not isinstance(value, str):
            raise ValueError(Messages.Validation.string_required(field_name))
        normalized = value.strip()
        if not normalized:
            raise ValueError(Messages.Validation.nonempty_string(field_name))
        return normalized

    @classmethod
    def normalize_slug(cls, value: object, field_name: str) -> str:
        normalized = cls.normalize_nonempty_string(value, field_name).lower()
        if not Patterns.SLUG.fullmatch(normalized):
            raise ValueError(Messages.Validation.stable_slug(field_name))
        return normalized

    @classmethod
    def normalize_metadata_key(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError(Messages.Validation.METADATA_KEY_STRING)
        normalized = value.strip()
        if not normalized:
            raise ValueError(Messages.Validation.METADATA_KEY_NONEMPTY)
        return normalized

    @classmethod
    def coerce_iterable(cls, value: object, field_name: str) -> tuple[object, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            raise ValueError(Messages.Validation.iterable_not_string(field_name))
        try:
            return tuple(value)  # type: ignore[arg-type]
        except TypeError as exc:
            raise ValueError(Messages.Validation.iterable_required(field_name)) from exc

    @classmethod
    def is_json_scalar(cls, value: object) -> bool:
        return value is None or isinstance(value, str | int | float | bool)


class SkillScalarValues:
    """Scalar values recognized by the lightweight frontmatter parser."""

    FALSE = "false"
    NULL = frozenset({"null", "none", "~"})
    TRUE = "true"
