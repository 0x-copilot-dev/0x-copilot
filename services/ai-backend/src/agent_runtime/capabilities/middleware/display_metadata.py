"""Deterministic display-metadata synthesis for tool descriptors and display-field wrapping."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, create_model

from agent_runtime.capabilities.tools.cards import ToolDisplayTemplate

# Wire keys (alias form) for optional agent-supplied display overrides.
# The model emits these in tool_call args; the presentation layer reads them
# off ``payload.args`` and applies them only when the matched template is
# ``synthetic=True``. Single source of truth for both producers and consumers.
DISPLAY_TITLE_KEY = "_display_title"
DISPLAY_SUMMARY_KEY = "_display_summary"
# Reserved kwarg key for the LangChain-injected tool_call_id. Captured by
# both wrap branches; never forwarded to the inner tool to avoid
# ``TypeError: unexpected keyword argument`` on callables that don't
# declare it. Never visible to the model (LangChain hides InjectedToolCallId
# fields from the tool schema block).
TOOL_CALL_ID_KEY = "tool_call_id"
# Alias-form wire keys — what the model emits and what the projector reads.
_DISPLAY_WIRE_KEYS: tuple[str, ...] = (DISPLAY_TITLE_KEY, DISPLAY_SUMMARY_KEY)
# Field-name-form keys — what LangChain converts alias keys to before invoking
# the wrapped coroutine. Strip targets must cover both forms.
_DISPLAY_FIELD_KEYS: tuple[str, ...] = ("display_title", "display_summary")
_DISPLAY_KEYS: tuple[str, ...] = _DISPLAY_WIRE_KEYS + _DISPLAY_FIELD_KEYS


class DisplayMetadataMiddleware:
    """Deterministic synthesis for MCP tool descriptors. Pure, side-effect-free."""

    # Maps verb prefix → (verb_form, primary_entity_keys). Names that don't
    # match any prefix fall back to noun-phrase humanisation; ``synthetic=True``
    # lets agent-supplied ``_display_*`` override the result.
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

        ``synthetic=True`` is always set so agent-supplied ``_display_*`` args
        may override the synthesised values at invocation time.
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
        # Noun-phrase post-action label — consistency matters more than fluency.
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
        """Return the ``properties`` dict from a JSON schema, or ``{}`` if absent."""
        if not isinstance(schema, Mapping):
            return {}
        properties = schema.get("properties")
        return properties if isinstance(properties, Mapping) else {}

    @staticmethod
    def _schema_type(schema: Mapping[str, Any] | None) -> str | None:
        """Return the primary ``type`` string from a JSON schema property, or ``None``."""
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
        """Return ``True`` when the schema describes a string-typed property."""
        return isinstance(schema, Mapping) and cls._schema_type(schema) == "string"

    @classmethod
    def _array_item_schema(
        cls, array_schema: Mapping[str, Any]
    ) -> Mapping[str, Any] | None:
        """Return the ``items`` sub-schema from an array schema, or ``None``."""
        items = array_schema.get("items")
        return items if isinstance(items, Mapping) else None

    @staticmethod
    def _first_present(
        properties: Mapping[str, Any], keys: tuple[str, ...]
    ) -> str | None:
        """Return the first key from ``keys`` that exists in ``properties``, or ``None``."""
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
        """Return the matched verb prefix string for ``lowered_name``, or ``""``."""
        if not verb_form:
            return ""
        for prefix, verb, _hints in cls._VERB_FORMS:
            if verb == verb_form and lowered_name.startswith(prefix):
                return prefix
        return ""

    @staticmethod
    def _humanise_identifier(value: str) -> str:
        """Convert a snake- or kebab-case identifier to title-cased words."""
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


class _DisplayFields(BaseModel):
    """Optional agent-supplied display overrides appended to every wrapped tool's args_schema.

    Brevity is enforced by the field ``description`` shown to the model rather than
    by ``max_length`` truncation — truncation renders as a broken card, while an
    over-long string just makes the card taller. ``extra="forbid"`` rejects
    unknown ``_display_*`` keys (e.g. a typo like ``_display_summery``) so
    wrapping fails loudly during testing rather than silently dropping the field.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )

    display_title: str | None = Field(
        default=None,
        alias=DISPLAY_TITLE_KEY,
        description=(
            "Optional. A short noun phrase (~3-7 words) for the activity "
            "card title. NOT a full sentence. Use ONLY when the deterministic "
            "title would be too generic. "
            "Examples: 'Q1 launch risk tickets', 'Recent Slack mentions', "
            "'External Q1 coverage'. "
            "Counter-examples (do NOT do this): 'Searching Linear for the "
            "user-requested...', 'Looking through all the documents that...'"
        ),
    )
    display_summary: str | None = Field(
        default=None,
        alias=DISPLAY_SUMMARY_KEY,
        description=(
            "Optional. ONE short clause (~10-15 words) for the activity "
            "card body. Why this specific call helps the current request, "
            "in plain English. NOT a description of what the tool does in "
            "general. "
            "Examples: 'Risk-tagged tickets opened in the launch quarter', "
            "'Posts that mention the launch in the past two weeks'. "
            "Leave null if the tool's deterministic title is already clear."
        ),
    )
    # ``tool_call_id`` is deliberately NOT declared here. Declaring
    # ``Annotated[str, InjectedToolCallId]`` forces every caller — including
    # tests that bypass LangGraph — to use a full ToolCall envelope. Instead,
    # the wrap coroutine captures it via ``*, tool_call_id: str = ""`` and the
    # inner schema inherits it through Pydantic model inheritance when the
    # inner tool already declares it (e.g. citation-capturing MCP tools).


def wrap_args_schema(args_schema: type[BaseModel] | None) -> type[BaseModel]:
    """Return a Pydantic model that extends ``args_schema`` with optional ``_display_title`` and ``_display_summary`` fields.

    Returns ``_DisplayFields`` directly when ``args_schema`` is ``None``.
    Idempotent: a schema bearing the ``__display_wrapped__`` marker is returned unchanged,
    making it safe to call on tools lists that pass through the wrap more than once.
    """

    if args_schema is None:
        return _DisplayFields  # already exactly what we need
    if getattr(args_schema, "__display_wrapped__", False):
        return args_schema
    wrapped = create_model(
        f"{args_schema.__name__}WithDisplay",
        __base__=(args_schema, _DisplayFields),
    )
    # Mark so re-wrapping is a no-op (subagent composition may re-apply the wrap).
    wrapped.__display_wrapped__ = True  # type: ignore[attr-defined]
    return wrapped


def strip_display(
    args: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, str | None]]:
    """Split a wrapped-args dict into ``(real_args, display_fields)``.

    ``real_args`` is a fresh dict containing every key except the display
    keys (in either alias form ``_display_title`` or field-name form
    ``display_title``) — safe to pass to the original tool implementation.

    ``display_fields`` is a 2-key dict (always keyed by the wire/alias
    form: ``_display_title`` / ``_display_summary``) with the
    agent-supplied strings or ``None`` for each absent field. The wire
    form is the canonical key callers should expect — both LangChain's
    field-name kwargs and the agent's raw alias emissions are coalesced
    here.

    Tolerates ``None`` / non-mapping input (e.g. from misshaped LangChain
    invocations) — returns ``({}, {DISPLAY_TITLE_KEY: None, DISPLAY_SUMMARY_KEY: None})``
    so callers don't need a separate guard.
    """

    if not isinstance(args, Mapping):
        return {}, {key: None for key in _DISPLAY_WIRE_KEYS}

    # Map both alias and field-name forms to the canonical wire key.
    # Order in the values pair matches ``_DISPLAY_WIRE_KEYS`` so
    # ``zip`` stays stable.
    field_to_wire: dict[str, str] = {
        DISPLAY_TITLE_KEY: DISPLAY_TITLE_KEY,
        DISPLAY_SUMMARY_KEY: DISPLAY_SUMMARY_KEY,
        "display_title": DISPLAY_TITLE_KEY,
        "display_summary": DISPLAY_SUMMARY_KEY,
    }
    display: dict[str, str | None] = {key: None for key in _DISPLAY_WIRE_KEYS}
    real: dict[str, Any] = {}
    for key, value in args.items():
        wire_key = field_to_wire.get(key)
        if wire_key is None:
            real[key] = value
            continue
        # Last non-None wins (so an alias emission beats a defaulted
        # field-name None, and vice versa). Non-strings are dropped to
        # ``None`` defensively — Pydantic should never let these through,
        # but the projector consumer never has to type-check.
        candidate = value if isinstance(value, str) else None
        if candidate is not None:
            display[wire_key] = candidate
        elif display[wire_key] is None:
            display[wire_key] = None
    return real, display


def agent_display_from_payload(
    payload: Mapping[str, Any],
) -> tuple[str | None, str | None]:
    """Extract ``(title, summary)`` agent-supplied display from an event payload.

    Read order: ``payload.args._display_title`` and ``payload.args._display_summary``.
    The args dict is where the agent's tool_call args land — same shape
    for regular tools and for the ``call_mcp_tool`` dispatcher (the agent
    puts ``_display_*`` at the top level of the dispatcher's args, not
    nested inside ``args.arguments``).

    Returns ``(None, None)`` for any non-mapping payload, missing args,
    or missing display keys. Empty strings are treated as missing —
    Pydantic's default validation accepts ``""`` for ``str | None``, but
    a Tier-3 override with empty title would render an empty card.
    """

    if not isinstance(payload, Mapping):
        return None, None
    args = payload.get("args")
    if not isinstance(args, Mapping):
        return None, None
    title = _non_empty_string(args.get(DISPLAY_TITLE_KEY))
    summary = _non_empty_string(args.get(DISPLAY_SUMMARY_KEY))
    return title, summary


def _non_empty_string(value: object) -> str | None:
    """Return the stripped string when non-empty, otherwise ``None``."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def wrap_tool_with_display(tool: object) -> object:
    """Return a tool whose ``args_schema`` accepts ``_display_*`` and whose invocation strips those fields before delegating.

    Idempotent: a tool already bearing the ``__display_wrapped__`` schema marker
    is returned unchanged. Falls back to returning the original tool for unrecognised
    shapes — the safety contract is "never break a working tool to add display copy."
    """

    args_schema = getattr(tool, "args_schema", None)
    if args_schema is not None and getattr(args_schema, "__display_wrapped__", False):
        return tool
    # Late import to avoid loading langchain in test paths that don't need it.
    try:
        from langchain_core.tools import BaseTool, StructuredTool  # noqa: PLC0415
    except ImportError:  # pragma: no cover - langchain is a hard runtime dep
        return tool

    if isinstance(tool, StructuredTool):
        return _wrap_structured_tool(tool, StructuredTool)
    if isinstance(tool, BaseTool):
        return _wrap_base_tool_via_delegation(tool, StructuredTool)
    return tool


def wrap_tools_with_display(tools: Any) -> list[object]:
    """Apply :func:`wrap_tool_with_display` to every tool and return a new list."""

    return [wrap_tool_with_display(tool) for tool in tools]


class _DispatchEnvelope:
    """Canonical LangChain ``ToolCall`` envelope builder shared by both wrap branches.

    Using the full envelope is required when the inner's schema declares
    ``InjectedToolCallId``; it is harmless for tools that don't.
    """

    TYPE_TOOL_CALL = "tool_call"
    KEY_ARGS = "args"
    KEY_NAME = "name"
    KEY_ID = "id"
    KEY_TYPE = "type"

    @classmethod
    def build(
        cls,
        *,
        args: dict[str, Any],
        name: str,
        tool_call_id: str,
    ) -> dict[str, Any]:
        """Build a LangChain ToolCall envelope dict."""
        return {
            cls.KEY_ARGS: args,
            cls.KEY_NAME: name,
            cls.KEY_ID: tool_call_id,
            cls.KEY_TYPE: cls.TYPE_TOOL_CALL,
        }


def _wrap_structured_tool(tool: Any, structured_tool_cls: type) -> Any:
    """Produce a copy of ``tool`` with the display-wrapped schema and stripping invokers.

    Wraps both ``func`` (sync) and ``coroutine`` (async) when present.
    ``tool_call_id`` is captured from LangChain injection but not forwarded
    to the inner callable, which does not declare it.
    """

    original_schema = tool.args_schema
    wrapped_schema = wrap_args_schema(original_schema)
    original_func = getattr(tool, "func", None)
    original_coroutine = getattr(tool, "coroutine", None)

    update: dict[str, object] = {"args_schema": wrapped_schema}

    if callable(original_func):

        def _wrapped_func(*, tool_call_id: str = "", **kwargs: Any) -> Any:
            """Sync dispatch path: strip display args and invoke the inner function."""
            del tool_call_id  # captured by LangChain injection; not forwarded to inner
            real, _ = strip_display(kwargs)
            return original_func(**real)

        update["func"] = _wrapped_func

    if callable(original_coroutine):

        async def _wrapped_coroutine(*, tool_call_id: str = "", **kwargs: Any) -> Any:
            """Async dispatch path: strip display args and await the inner coroutine."""
            del tool_call_id  # captured for LangChain, not forwarded — see docstring
            real, _ = strip_display(kwargs)
            return await original_coroutine(**real)

        update["coroutine"] = _wrapped_coroutine

    return tool.model_copy(update=update)


def _wrap_base_tool_via_delegation(tool: Any, structured_tool_cls: type) -> Any:
    """Build a new ``StructuredTool`` that delegates to ``tool.ainvoke``.

    Used for non-``StructuredTool`` ``BaseTool`` subclasses where
    mutating the schema in place is unsafe. The full LangChain
    ``ToolCall`` envelope is required when the inner declares
    ``InjectedToolCallId`` and is harmless for tools that don't.
    """

    original_schema = getattr(tool, "args_schema", None)
    wrapped_schema = wrap_args_schema(original_schema)
    inner_name = getattr(tool, "name", "tool")

    async def _delegating_coroutine(*, tool_call_id: str = "", **kwargs: Any) -> Any:
        """Delegate to ``tool.ainvoke`` with a full LangChain tool-call envelope."""
        real, _ = strip_display(kwargs)
        envelope = _DispatchEnvelope.build(
            args=real,
            name=inner_name,
            tool_call_id=tool_call_id,
        )
        return await tool.ainvoke(envelope)

    return structured_tool_cls.from_function(
        coroutine=_delegating_coroutine,
        name=inner_name,
        description=getattr(tool, "description", ""),
        args_schema=wrapped_schema,
    )
