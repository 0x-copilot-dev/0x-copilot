"""Shared keys, limits, patterns, and public messages for skills middleware."""

from __future__ import annotations

import re


class Keys:
    """Stable string keys used by the skills middleware package."""

    class Characters:
        """Single-character string constants used in frontmatter parsing."""

        COLON = ":"
        COMMA = ","
        HASH = "#"
        LEFT_BRACKET = "["
        LEFT_PAREN = "("
        QUOTE_DOUBLE = '"'
        QUOTE_SINGLE = "'"
        RIGHT_BRACKET = "]"
        SLASH_DOT_DOT = ".."
        SPACE = " "
        TAB = "\t"

    class DeepAgents:
        """Key names used in the deep-agents skill bundle interface."""

        SKILLS = "skills"

    class Encoding:
        """Encoding name constants."""

        UTF_8 = "utf-8"

    class Fields:
        """Field name constants for skill manifests and source configs."""

        ALLOWED_SOURCES = "allowed_sources"
        ALLOWED_TOOLS = "allowed_tools"
        COMPATIBILITY = "compatibility"
        DENIED_SKILL_NAMES = "denied_skill_names"
        DESCRIPTION = "description"
        LICENSE = "license"
        METADATA = "metadata"
        NAME = "name"
        PATH = "path"
        ROOTS = "roots"
        SAFE_MESSAGE = "safe_message"
        SCOPE = "scope"
        SKILL_ROOT = "skill root"
        SKILL_DIRECTORY = "skill_directory"

    class Files:
        """Well-known file name constants."""

        SKILL_MD = "SKILL.md"

    class Frontmatter:
        """Marker strings for YAML frontmatter parsing."""

        BOUNDARY = "---"
        COMMENT_PREFIX = "#"
        LIST_PREFIX = "- "

    class Links:
        """URI prefix strings used to detect non-relative link schemes."""

        DATA = "data:"
        FRAGMENT = "#"
        MAILTO = "mailto:"
        SCHEME_SEPARATOR = "://"

    class Pydantic:
        """Pydantic configuration constant strings."""

        BEFORE = "before"
        ERROR_TYPE = "type"
        FORBID = "forbid"
        MISSING = "missing"


Keys.Frontmatter.KNOWN_KEYS = frozenset(
    {
        Keys.Fields.ALLOWED_TOOLS,
        Keys.Fields.COMPATIBILITY,
        Keys.Fields.DESCRIPTION,
        Keys.Fields.LICENSE,
        Keys.Fields.METADATA,
        Keys.Fields.NAME,
    }
)


class Limits:
    """Validation limits for Agent Skills manifests."""

    PUBLIC_ERROR_MAX_LENGTH = 500
    SKILL_DESCRIPTION_MAX_LENGTH = 240
    SKILL_FILE_MAX_BYTES = 10 * 1024 * 1024
    SOURCE_PRECEDENCE_MAX = 1_000_000


class Patterns:
    """Compiled patterns for normalized skill identifiers and references."""

    MARKDOWN_LINK = re.compile(r"!?\[[^\]]*]\(([^)]+)\)")
    SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class Messages:
    """Centralized public and validation messages for skills middleware."""

    class Errors:
        """Safe public error message strings for skills middleware."""

        ASSET_MISSING = "Skill asset reference does not exist."
        ASSET_UNSAFE = "Skill asset references must stay inside the skill directory."
        FRONTMATTER_EMPTY = "Skill frontmatter must not be empty."
        FRONTMATTER_INVALID = "Skill manifest frontmatter is invalid."
        FRONTMATTER_MALFORMED = "Skill frontmatter contains malformed YAML."
        FRONTMATTER_MISSING = "Skill file must start with YAML frontmatter."
        FRONTMATTER_UNCLOSED = "Skill file must close its YAML frontmatter block."
        KEY_EMPTY = "Skill frontmatter contains an empty key."
        SKILL_EMPTY = "Skill file must not be empty."
        SKILL_READ_FAILED = "Skill directory must contain a readable SKILL.md file."
        SKILL_SOURCE_UNREADABLE = "Skill source directory is not readable."
        SKILL_TOO_LARGE = "Skill file exceeds the configured size limit."
        UNSUPPORTED_NESTED_VALUE = (
            "Skill frontmatter contains an unsupported nested value."
        )

    class Validation:
        """Validation error message constants and factory methods."""

        METADATA_MAPPING_REQUIRED = "metadata must be a mapping"
        METADATA_VALUES_SCALAR = "metadata values must be JSON scalar values"
        METADATA_KEY_STRING = "metadata keys must be strings"
        METADATA_KEY_NONEMPTY = "metadata keys must not be empty"
        POLICY_VALUES_ITERABLE = "policy values must be iterable"
        SOURCE_PATH_STRING = "allowed source paths must be strings or Paths"
        STABLE_POLICY_SLUG = "skill names and tool names must be stable slugs"
        STRING_POLICY_SLUG = "skill names and tool names must be strings"

        @classmethod
        def iterable_not_string(cls, field_name: str) -> str:
            """Return a message for when a string is passed where an iterable is required."""
            return f"{field_name} must be an iterable, not a string"

        @classmethod
        def iterable_required(cls, field_name: str) -> str:
            """Return a message for when a non-iterable value is passed."""
            return f"{field_name} must be an iterable"

        @classmethod
        def nonempty_string(cls, field_name: str) -> str:
            """Return a message for when an empty string is passed."""
            return f"{field_name} must not be empty"

        @classmethod
        def path_string(cls, field_name: str) -> str:
            """Return a message for when a value is not a string or Path."""
            return f"{field_name} must be a string or Path"

        @classmethod
        def stable_slug(cls, field_name: str) -> str:
            """Return a message for when a value is not a stable slug."""
            return f"{field_name} must be a stable slug"

        @classmethod
        def string_or_iterable(cls, field_name: str) -> str:
            """Return a message for when a value is neither a string nor an iterable."""
            return f"{field_name} must be a string or iterable of strings"

        @classmethod
        def string_required(cls, field_name: str) -> str:
            """Return a message for when a non-string value is passed."""
            return f"{field_name} must be a string"
