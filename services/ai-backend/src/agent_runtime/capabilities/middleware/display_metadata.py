"""Display-metadata synthesis for MCP tool descriptors (polish-removal Phase 2.A).

Replaces the per-tool-call presentation polish LLM with a deterministic
:class:`ToolDisplayTemplate` produced from the vendor's MCP descriptor at
build time. The output is pure: same ``(tool_name, connector, input_schema,
output_shape)`` always yields the same template, so the descriptor is
cacheable for the lifetime of the load.

Resolution order at event time (for context — this module only produces
the Tier-2 template):

1. Deterministic event templates (approval / auth / error / delta).
2. Tool template from registration / synthesis (this module).
3. Agent-supplied ``_display_*`` from tool args (Phase 3) — wins when the
   matched template has ``synthetic=True``.
4. Minimal envelope fallback.

See ``docs/refactor/01-presentation-polish-removal.md`` §4 Phase 2.A for
the design and §6.1 for the ``synthetic`` flag semantics.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_runtime.capabilities.tools.cards import ToolDisplayTemplate


class DisplayMetadataMiddleware:
    """Deterministic synthesis for MCP tool descriptors. Pure, side-effect-free."""

    # Mapping verb prefixes to a (verb_form, primary_entity_hint) pair.
    # ``verb_form`` is folded into the title template; ``primary_entity_hint``
    # is the input-schema property the synthesiser looks for first when
    # picking a placeholder. The mapping is intentionally short — names
    # outside it fall through to a noun-phrase humanisation, with the
    # ``synthetic=True`` flag signalling that the agent's ``_display_*``
    # is welcome to override (Phase 3).
    _VERB_FORMS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
        ("list_", "List", ("query", "filter", "type")),
        ("search_", "Search", ("query", "q", "keyword")),
        ("get_", "Get", ("id", "name", "key")),
        ("read_", "Read", ("path", "file", "id")),
        ("fetch_", "Fetch", ("id", "url", "name")),
        ("post_", "Post to", ("channel", "thread", "target")),
        ("send_", "Send", ("channel", "to", "recipient")),
        ("create_", "Create", ("name", "title", "body")),
        ("update_", "Update", ("id", "name", "title")),
        ("delete_", "Delete", ("id", "name", "key")),
        ("query_", "Query", ("query", "q", "filter")),
    )

    # Output-shape walk: top-level property names that frequently hold
    # the result array. First array-shaped match becomes
    # ``result_preview_path``. Order matters — earlier entries win.
    _RESULT_ARRAY_KEYS: tuple[str, ...] = (
        "items",
        "results",
        "data",
        "rows",
        "matches",
        "documents",
        "sources",
    )

    # Per-row property heuristics. The synthesiser maps these into the
    # ``result_preview_row`` dict so the projector knows which row keys
    # to surface. Order in each tuple is preference-order.
    _ROW_TITLE_KEYS: tuple[str, ...] = (
        "title",
        "name",
        "summary",
        "subject",
        "headline",
    )
    _ROW_SUBTITLE_KEYS: tuple[str, ...] = (
        "snippet",
        "description",
        "preview",
        "excerpt",
        "status",
    )
    _ROW_URL_KEYS: tuple[str, ...] = ("url", "link", "href", "permalink")
    _ROW_BADGE_KEYS: tuple[str, ...] = ("source", "connector", "kind", "type")

    @classmethod
    def synthesise_for_mcp(
        cls,
        *,
        tool_name: str,
        connector: str,
        input_schema: Mapping[str, Any] | None,
        output_shape: Mapping[str, Any] | None,
    ) -> ToolDisplayTemplate:
        """Build a deterministic :class:`ToolDisplayTemplate` for an MCP tool.

        Inputs:
            tool_name: vendor-supplied tool name (e.g. ``"list_issues"``).
            connector: vendor display name (e.g. ``"Linear"``).
            input_schema: JSON-schema for the args (used to pick placeholders).
            output_shape: JSON-schema for the result (used to pick
                ``result_preview_path`` and ``result_preview_row``).

        Always sets ``synthetic=True`` so Phase 3's agent-supplied ``_display_*``
        is allowed to override.
        """

        verb_form, primary_keys = cls._verb_form_for(tool_name)
        primary_placeholder = cls._pick_primary_placeholder(
            input_schema, primary_keys, tool_name
        )
        title_template = cls._compose_title(
            verb_form=verb_form,
            connector=connector,
            tool_name=tool_name,
            primary_placeholder=primary_placeholder,
        )
        result_title_template = cls._compose_result_title(
            verb_form=verb_form,
            connector=connector,
        )
        preview_path, preview_row = cls._project_output_shape(output_shape)
        return ToolDisplayTemplate(
            title_template=title_template,
            summary_template=None,
            result_title_template=result_title_template,
            result_summary_template=None,
            result_preview_path=preview_path,
            result_preview_row=preview_row,
            synthetic=True,
        )

    # --- Helpers ----------------------------------------------------------

    @classmethod
    def _verb_form_for(cls, tool_name: str) -> tuple[str, tuple[str, ...]]:
        """Resolve ``tool_name`` to a (verb_form, primary_keys) pair.

        Returns ``("", ())`` when no prefix matches — caller falls back to
        humanising the bare name as a noun phrase.
        """

        lowered = tool_name.lower()
        for prefix, verb, hints in cls._VERB_FORMS:
            if lowered.startswith(prefix):
                return verb, hints
        return "", ()

    @classmethod
    def _pick_primary_placeholder(
        cls,
        input_schema: Mapping[str, Any] | None,
        primary_keys: tuple[str, ...],
        tool_name: str,
    ) -> str | None:
        """Choose the most-likely user-meaningful arg name for the title.

        Walks ``input_schema.properties`` in the order suggested by
        ``primary_keys`` (verb-form-driven), falling back to the first
        ``string``-typed property. Returns ``None`` if no suitable
        property is found — the title omits the placeholder in that case.
        """

        properties = cls._properties(input_schema)
        if not properties:
            return None
        for key in primary_keys:
            if key in properties and cls._is_string_property(properties[key]):
                return key
        # Fallback: first string-typed property, stable order.
        for key, value in properties.items():
            if cls._is_string_property(value):
                return key
        # Last resort: the first property regardless of type — better
        # to render ``"List Linear {filter}"`` than to drop the noun.
        return next(iter(properties), None)

    @classmethod
    def _compose_title(
        cls,
        *,
        verb_form: str,
        connector: str,
        tool_name: str,
        primary_placeholder: str | None,
    ) -> str:
        """Compose the ``title_template`` string.

        Three shapes:

        - ``"List Linear issues for {query}"`` — verb match + placeholder.
        - ``"List Linear issues"``                 — verb match, no placeholder.
        - ``"Linear: list custom action"``         — no verb match (fallback).
        """

        humanised_remainder = cls._humanise_remainder(tool_name, verb_form)
        connector_label = cls._humanise_identifier(connector)
        if verb_form:
            head = f"{verb_form} {connector_label} {humanised_remainder}".strip()
            if primary_placeholder is not None:
                return f"{head} for {{{primary_placeholder}}}".strip()
            return head
        # Fallback for tools whose names don't match a verb prefix.
        humanised = cls._humanise_identifier(tool_name)
        return f"{connector_label}: {humanised}"

    @classmethod
    def _compose_result_title(
        cls,
        *,
        verb_form: str,
        connector: str,
    ) -> str | None:
        """Compose the optional ``result_title_template``.

        Most MCP results are best summarised by a concise post-action label
        (``"Linear results"``, ``"Slack message posted"``). For verb-less
        names we leave it ``None`` and let the projector body fill it.
        """

        connector_label = cls._humanise_identifier(connector)
        if not verb_form:
            return None
        # Past-tense-ish noun phrase. We don't try to be grammatically clever —
        # consistency matters more than fluency.
        if verb_form in {"List", "Search", "Get", "Read", "Fetch", "Query"}:
            return f"{connector_label} results"
        if verb_form in {"Post to", "Send"}:
            return f"{connector_label} message sent"
        if verb_form in {"Create", "Update", "Delete"}:
            return f"{connector_label} updated"
        return None

    @classmethod
    def _project_output_shape(
        cls,
        output_shape: Mapping[str, Any] | None,
    ) -> tuple[str | None, dict[str, str] | None]:
        """Walk ``output_shape`` for a result-array root + row heuristics.

        Returns ``(result_preview_path, result_preview_row)``. Either may be
        ``None`` — the projector then falls back to its built-in field-name
        heuristics on the actual result payload.
        """

        properties = cls._properties(output_shape)
        if not properties:
            return None, None
        for key in cls._RESULT_ARRAY_KEYS:
            value = properties.get(key)
            if not isinstance(value, Mapping):
                continue
            if cls._schema_type(value) != "array":
                continue
            row_schema = cls._array_item_schema(value)
            row_props = cls._properties(row_schema)
            return key, cls._row_mapping(row_props)
        return None, None

    @classmethod
    def _row_mapping(cls, row_props: Mapping[str, Any]) -> dict[str, str] | None:
        """Build the ``result_preview_row`` dict from row property names.

        Returns ``None`` when no row property matched a slot — the projector
        will use its built-in heuristics in that case.
        """

        if not row_props:
            return None
        mapping: dict[str, str] = {}
        title_key = cls._first_present(row_props, cls._ROW_TITLE_KEYS)
        if title_key is not None:
            mapping["title"] = title_key
        subtitle_key = cls._first_present(row_props, cls._ROW_SUBTITLE_KEYS)
        if subtitle_key is not None and subtitle_key != mapping.get("title"):
            mapping["subtitle"] = subtitle_key
        url_key = cls._first_present(row_props, cls._ROW_URL_KEYS)
        if url_key is not None:
            mapping["url"] = url_key
        badge_key = cls._first_present(row_props, cls._ROW_BADGE_KEYS)
        if badge_key is not None:
            mapping["badge"] = badge_key
        return mapping or None

    # --- Pure schema helpers ---------------------------------------------

    @staticmethod
    def _properties(schema: Mapping[str, Any] | None) -> Mapping[str, Any]:
        if not isinstance(schema, Mapping):
            return {}
        properties = schema.get("properties")
        return properties if isinstance(properties, Mapping) else {}

    @staticmethod
    def _schema_type(schema: Mapping[str, Any] | None) -> str | None:
        if not isinstance(schema, Mapping):
            return None
        value = schema.get("type")
        if isinstance(value, str):
            return value
        # ``type`` may be a list (e.g. ``["string", "null"]``); pick the first
        # non-null entry.
        if isinstance(value, list):
            for entry in value:
                if isinstance(entry, str) and entry != "null":
                    return entry
        return None

    @classmethod
    def _is_string_property(cls, schema: Any) -> bool:
        return isinstance(schema, Mapping) and cls._schema_type(schema) == "string"

    @classmethod
    def _array_item_schema(
        cls, array_schema: Mapping[str, Any]
    ) -> Mapping[str, Any] | None:
        items = array_schema.get("items")
        return items if isinstance(items, Mapping) else None

    @staticmethod
    def _first_present(
        properties: Mapping[str, Any], keys: tuple[str, ...]
    ) -> str | None:
        for key in keys:
            if key in properties:
                return key
        return None

    @classmethod
    def _humanise_remainder(cls, tool_name: str, verb_form: str) -> str:
        """Humanise the post-prefix tail of ``tool_name`` for the title."""

        lowered = tool_name.lower()
        prefix = cls._matched_prefix(lowered, verb_form)
        remainder = tool_name[len(prefix) :] if prefix else tool_name
        return cls._humanise_identifier(remainder).lower()

    @classmethod
    def _matched_prefix(cls, lowered_name: str, verb_form: str) -> str:
        if not verb_form:
            return ""
        for prefix, verb, _hints in cls._VERB_FORMS:
            if verb == verb_form and lowered_name.startswith(prefix):
                return prefix
        return ""

    @staticmethod
    def _humanise_identifier(value: str) -> str:
        text = value.strip()
        # Strip vendor-y suffixes that produce awkward phrasing.
        for suffix in ("_com", "_io", "_app"):
            if text.lower().endswith(suffix):
                text = text[: -len(suffix)]
        # Tokenise on snake- and kebab-case.
        words = [word for word in text.replace("-", "_").split("_") if word]
        if not words:
            return value.strip()
        return " ".join(word[0].upper() + word[1:] for word in words)
