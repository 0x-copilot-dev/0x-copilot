"""Redaction primitives: credential-key deny set, field-tagging, and structural coercers.

Logs are the only place data is filtered. :class:`MetadataRedactor` is the single
filter used by both ``RuntimeLogEvent.metadata`` and ``HttpLogEvent.metadata`` to
drop dict keys matching :data:`DENY_KEYS`. :meth:`SafeLogDumper.dump_safe` elides
Pydantic fields annotated :class:`Sensitive`. :class:`JsonObjectCoercer` handles
``JsonObject`` shape coercion without performing any redaction. All other surfaces
(SSE, persistence, runtime context) carry data whole.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
import logging
from typing import Any, ClassVar

from pydantic import BaseModel


_redactor_debug_logger = logging.getLogger("agent_runtime")


# Exact-match deny set for dict keys carrying credentials in free-form
# ``metadata`` payloads. Uses exact match rather than a substring-regex
# to avoid over-matching observability counters like ``input_tokens``
# (any name containing ``token``). The set is intentionally small and
# stable — pattern-matching against values lives nowhere in this codebase.
# Add a key here only when a new credential-shaped field name lands in review.
DENY_KEYS: frozenset[str] = frozenset(
    {
        # Generic credentials
        "password",
        "passwd",
        "secret",
        "credential",
        "credentials",
        # API tokens
        "api_key",
        "apikey",
        "api-key",
        # OAuth / session tokens
        "authorization",
        "auth_token",
        "access_token",
        "refresh_token",
        # Asymmetric crypto material
        "private_key",
        "client_secret",
        # Generic catch-all (exact-match only — substrings don't fire)
        "token",
    }
)


# ---------------------------------------------------------------------------
# Structural coercion is separated from redaction.
#
# Pydantic ``JsonObject`` validators previously ran a combined coerce+scrub
# helper. That call did two unrelated jobs: (a) coerce ``None`` / non-mapping
# values into a dict shape, and (b) scrub credential-shaped keys / clip long
# strings. Logs keep redaction at their own boundary; everywhere else only
# coercion runs. ``JsonObjectCoercer`` is the coercion-only helper for non-log
# validators.
# ---------------------------------------------------------------------------


class MetadataRedactor:
    """Single-source ``metadata: dict`` filter for structured log models.

    Both ``RuntimeLogEvent`` and ``HttpLogEvent`` carry a free-form
    ``metadata`` dict. Consolidating here makes the deny contract a single
    thing to evolve — adding a new sensitive key name or tightening a
    value-type rule lands in one place.

    Behavior:

    - Non-dict input → ``{}``. Validators reject non-mapping metadata.
    - Non-string keys are dropped.
    - Keys in :data:`DENY_KEYS` are dropped (defense-in-depth alongside
      structural ``Sensitive`` field tagging).
    - Values must be a scalar (``str``, ``int``, ``float``, ``bool``)
      or ``None``; non-scalar values are dropped because a nested dict
      or list would defeat the "metadata is structural log content"
      invariant.
    - When any keys were dropped, a single DEBUG log line surfaces the
      names — never the values. Production runs at INFO so the line is
      silent; dev / debug runs let engineers see what was filtered.
    """

    _ALLOWED_VALUE_TYPES: ClassVar[tuple[type, ...]] = (
        str,
        int,
        float,
        bool,
        type(None),
    )

    @classmethod
    def redact(cls, value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, object] = {}
        dropped: list[str] = []
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            if key in DENY_KEYS:
                dropped.append(key)
                continue
            if not isinstance(item, cls._ALLOWED_VALUE_TYPES):
                dropped.append(key)
                continue
            result[key] = item
        if dropped:
            _redactor_debug_logger.debug(
                "Dropped metadata keys from log event: %s", dropped
            )
        return result


class JsonObjectCoercer:
    """Pydantic field-validator helper that coerces values into the
    ``dict`` shape ``JsonObject`` fields expect, without performing
    redaction.

    Behavior:

        None         → {}
        non-mapping  → {"value": value}
        mapping      → dict(value)

    No recursion. No value scanning. No deny-key scrubbing. No length
    clipping. The value flows through whole; logs filter sensitive
    content at their own validation boundary via :data:`DENY_KEYS` and
    :class:`SafeLogDumper`.
    """

    @classmethod
    def coerce(cls, value: object) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            return {"value": value}
        return dict(value)


# ---------------------------------------------------------------------------
# Structural field-tagging for log emission.
#
# The deny-key set protects free-form ``metadata: dict`` payloads but cannot
# protect typed Pydantic fields that carry sensitive content under
# benign-looking names (e.g. assistant text under ``content``). Field tagging
# solves this: mark a field ``Sensitive(...)`` at declaration time and the log
# emitter elides it. Sensitivity is a property of the field, not the value.
# ---------------------------------------------------------------------------


class SensitiveCategory(StrEnum):
    """Categories of sensitive content carried by Pydantic fields.

    Categories drive future per-buyer policy (e.g. drop ``MODEL_OUTPUT``
    in audit logs but keep in debug). Today every tagged field is
    dropped uniformly by :class:`SafeLogDumper`; the enum exists so
    that policy lands as data, not as new code.
    """

    SECRET = "secret"
    """API tokens, passwords, OAuth state, private keys."""

    PII = "pii"
    """User emails, names, addresses, phone numbers."""

    FINANCIAL = "financial"
    """Account numbers, card numbers, IBAN."""

    GOVERNMENT_ID = "government_id"
    """Emirates ID, passports, TRN."""

    MODEL_OUTPUT = "model_output"
    """LLM completions. Sensitive because they may echo user PII verbatim."""

    USER_INPUT = "user_input"
    """Raw user prompts."""


@dataclass(frozen=True)
class Sensitive:
    """Pydantic ``Annotated[]`` marker: this field is sensitive.

    Usage::

        from typing import Annotated
        from agent_runtime.observability.redactor import (
            Sensitive,
            SensitiveCategory,
        )

        class ManagedContextPayload(BaseModel):
            content: Annotated[
                str | None, Sensitive(SensitiveCategory.MODEL_OUTPUT)
            ] = None

    :class:`SafeLogDumper` elides tagged fields from ``to_log_dict()``
    output. The field is otherwise a normal Pydantic field — direct
    attribute access (``payload.content``) is unaffected, and
    ``model_dump()`` calls outside the safe dumper include it. Only the
    log-emission boundary strips it.
    """

    category: SensitiveCategory


class SafeLogDumper:
    """Pydantic-model dumper that elides ``Sensitive``-tagged fields.

    Introspection is cached per ``BaseModel`` subclass so the hot path
    is one ``frozenset`` membership check per dump. **Top-level fields
    only** — nested Pydantic models inside the dump are not inspected.
    If a nested field carries sensitive content, tag it at the
    enclosing level or call :meth:`dump_safe` at each level explicitly.
    """

    _cache: ClassVar[dict[type[BaseModel], frozenset[str]]] = {}

    @classmethod
    def sensitive_field_names(cls, model_cls: type[BaseModel]) -> frozenset[str]:
        """Return the set of field names on ``model_cls`` tagged
        :class:`Sensitive`. Cached per class — model annotations don't
        change at runtime so the cache never invalidates in production.
        """

        cached = cls._cache.get(model_cls)
        if cached is not None:
            return cached
        names: set[str] = set()
        for name, field in model_cls.model_fields.items():
            for meta in field.metadata:
                if isinstance(meta, Sensitive):
                    names.add(name)
                    break
        result = frozenset(names)
        cls._cache[model_cls] = result
        return result

    @classmethod
    def dump_safe(cls, model: BaseModel, **dump_kwargs: Any) -> dict[str, Any]:
        """Return :meth:`BaseModel.model_dump` output with any tagged
        fields removed.

        ``dump_kwargs`` passes through to ``model_dump`` so existing
        callers can request ``mode="json"``, ``exclude_none=True``, etc.
        Untagged models short-circuit to the unmodified dump — there's
        no per-dump cost beyond a ``frozenset`` lookup.
        """

        sensitive = cls.sensitive_field_names(type(model))
        dumped = model.model_dump(**dump_kwargs)
        if not sensitive:
            return dumped
        return {k: v for k, v in dumped.items() if k not in sensitive}

    @classmethod
    def reset_cache(cls) -> None:
        """Test-only hook. Production code never invalidates the cache —
        a model class's field annotations don't change at runtime."""

        cls._cache.clear()
