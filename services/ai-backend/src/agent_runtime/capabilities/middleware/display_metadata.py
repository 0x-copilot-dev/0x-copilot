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
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, create_model

from agent_runtime.capabilities.tools.cards import ToolDisplayTemplate

# --- Tier-3 wire format (Phase 3.A) --------------------------------------
#
# The agent fills these two optional fields on the tool args dict when the
# deterministic title (Tier 1/2) would be too generic. They flow through
# the wire as ``payload.args._display_title`` / ``payload.args._display_summary``
# and are read by ``PresentationGenerator`` only when the matched template
# is ``synthetic=True`` (or absent).
#
# These constants are referenced from both the helpers below and the
# presentation generator's Tier-3 read so the wire key only lives in one
# place.

DISPLAY_TITLE_KEY = "_display_title"
DISPLAY_SUMMARY_KEY = "_display_summary"
# Reserved kwarg key for the LangChain-injected tool_call_id. Captured by
# both wrap branches and used by ``_DispatchEnvelope`` to forward to inner
# tools that declare ``InjectedToolCallId``. Never visible to the model
# (LangChain hides ``Annotated[str, InjectedToolCallId]`` fields from the
# tool block), so it does not collide with any user-emitted arg name.
TOOL_CALL_ID_KEY = "tool_call_id"
# Wire keys (alias form) — what the model emits in tool_call args and what
# the projector reads off ``payload.args``. Pydantic's
# ``populate_by_name=True`` lets the model emit either form, but the JSON
# schema's ``alias`` is what the model sees in its tool block, so the
# wire form is the underscore-prefixed alias.
_DISPLAY_WIRE_KEYS: tuple[str, ...] = (DISPLAY_TITLE_KEY, DISPLAY_SUMMARY_KEY)
# Validated kwarg keys (field-name form) — LangChain's ``StructuredTool``
# converts the raw args dict to kwargs using the Pydantic FIELD names,
# not the aliases, before invoking the wrapped coroutine. So the strip
# target inside the wrap is BOTH the wire form (defensive — for callers
# that bypass Pydantic) AND the field-name form.
_DISPLAY_FIELD_KEYS: tuple[str, ...] = ("display_title", "display_summary")
_DISPLAY_KEYS: tuple[str, ...] = _DISPLAY_WIRE_KEYS + _DISPLAY_FIELD_KEYS


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


# --- Tier-3 helpers (Phase 3.A) ------------------------------------------
#
# These are the receive-side helpers. The producer side — wrapping each
# bound tool's args_schema and stripping at invoke time — lives in Phase
# 3.B (``docs/refactor/01-presentation-polish-removal.md``).


class _DisplayFields(BaseModel):
    """Optional agent-supplied display fields appended to every wrapped
    tool's args_schema.

    Brevity is enforced by the field ``description`` shown to the model
    (which carries explicit examples and counter-examples), not by
    Pydantic ``max_length`` or runtime truncation. See PRD §8 for the
    rationale: truncation looks broken in the UI; an over-long agent
    response just makes the card a row taller.

    ``extra="forbid"`` rejects unknown ``_display_*`` keys (e.g. typoed
    ``_display_summery``) so the wrap fails loudly during testing rather
    than silently dropping the field.
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
    # NB on ``tool_call_id``: PRD §3 Part A. We intentionally do NOT declare
    # ``tool_call_id: Annotated[str, InjectedToolCallId]`` on this base
    # class. Doing so would force every wrap caller — including tests that
    # bypass LangGraph and call ``ainvoke({...plain args...})`` — to use a
    # full ``ToolCall`` envelope, since LangChain enforces envelope shape
    # whenever a schema declares ``InjectedToolCallId``. Instead, we rely
    # on Pydantic's model inheritance in :func:`wrap_args_schema`: when
    # the inner tool's own ``args_schema`` already declares
    # ``Annotated[str, InjectedToolCallId]`` (citation-capturing wrapper,
    # every MCP tool), the wrapped schema inherits it automatically. The
    # wrap coroutine signature ``*, tool_call_id: str = ""`` captures the
    # injected id when present and defaults to ``""`` otherwise — both
    # branches forward through :class:`_DispatchEnvelope` regardless.


def wrap_args_schema(args_schema: type[BaseModel] | None) -> type[BaseModel]:
    """Return a Pydantic model that extends ``args_schema`` with
    optional ``_display_title`` + ``_display_summary`` fields.

    When ``args_schema`` is ``None`` (some LangChain tools omit it) we
    return a fresh model with just the two display fields. Either way the
    returned class can be assigned to ``BaseTool.args_schema`` and Pydantic
    validates inputs the same way.

    Idempotent: wrapping an already-wrapped schema returns the wrapped
    schema unchanged. This makes ``build_deep_agent`` safe to call on a
    tools list that may include a re-bound subagent's tools.
    """

    if args_schema is None:
        return _DisplayFields  # already exactly what we need
    if getattr(args_schema, "__display_wrapped__", False):
        return args_schema
    wrapped = create_model(
        f"{args_schema.__name__}WithDisplay",
        __base__=(args_schema, _DisplayFields),
    )
    # Marker attribute so ``wrap_args_schema`` is idempotent — useful when
    # a tools list passes through the wrap more than once (e.g. subagent
    # composition that re-binds the supervisor's tools).
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
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


# --- Phase 3.B tool-binding wrap -----------------------------------------
#
# Wraps a bound tool so its ``args_schema`` (the JSON-schema the agent
# sees in its tool block) carries the optional ``_display_*`` fields and
# its invocation strips them before delegating to the underlying tool.
#
# Two tool shapes show up in the bound tool list per
# ``factory._model_visible_tools``:
#
# 1. ``StructuredTool`` — every custom dataclass adapter goes through
#    ``factory._structured_tool``. Wrap via ``model_copy`` of the schema
#    + the coroutine (and the sync ``func`` when present).
# 2. Other ``BaseTool`` subclasses (DuckDuckGo etc.) — wrap by creating a
#    NEW ``StructuredTool`` that delegates to the original via ``ainvoke``.
#    Loses any tool-specific niceties (callbacks, custom error handlers)
#    but is the safest path that preserves behaviour for the common case.
#
# Anything else is returned unchanged with a debug log — better to ship a
# tool with no agent override than to break a tool we don't recognise.


def wrap_tool_with_display(tool: object) -> object:
    """Return a tool whose ``args_schema`` accepts ``_display_*`` and whose
    invocation strips those fields before delegating to the underlying
    implementation.

    Idempotent: a tool whose schema already carries the
    ``__display_wrapped__`` marker is returned unchanged.

    Falls back to returning ``tool`` unchanged for shapes we don't
    recognise — Phase 3.B's safety contract is "never break a working
    tool to add display copy."
    """

    args_schema = getattr(tool, "args_schema", None)
    if args_schema is not None and getattr(args_schema, "__display_wrapped__", False):
        return tool
    # Local import — avoid a hard module-load dependency on langchain when
    # the rest of this module is imported by tests that don't need it.
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
    """Apply :func:`wrap_tool_with_display` to every entry of an iterable
    of tools. Returns a new list — does not mutate the input."""

    return [wrap_tool_with_display(tool) for tool in tools]


class _DispatchEnvelope:
    """Canonical LangChain ``ToolCall`` envelope shape used by wrap dispatch.

    Centralised so a future LangChain bump that changes the envelope keys
    is a one-file edit, and so the two wrap branches share one source of
    truth for "how the wrapper hands off to its inner."

    A plain ``args`` dict is rejected by ``BaseTool.ainvoke`` when the
    inner's schema declares ``InjectedToolCallId`` (citation-capturing
    wrapper, MCP tools). The envelope shape below is what LangChain
    requires in that case and is harmless for tools that don't declare
    the annotation — the ``id`` field is treated as metadata and the
    args are extracted exactly the same way.
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
        return {
            cls.KEY_ARGS: args,
            cls.KEY_NAME: name,
            cls.KEY_ID: tool_call_id,
            cls.KEY_TYPE: cls.TYPE_TOOL_CALL,
        }


def _wrap_structured_tool(tool: Any, structured_tool_cls: type) -> Any:
    """Produce a copy of ``tool`` with wrapped schema + stripping invokers.

    Both ``func`` (sync) and ``coroutine`` (async) are wrapped when
    present. ``StructuredTool.from_function`` inside ``factory.py`` only
    sets ``coroutine`` for our adapters, but a third-party tool may set
    ``func`` instead — handling both keeps the wrap general.

    The wrapper's schema declares ``tool_call_id`` via
    :class:`InjectedToolCallId`, so LangChain injects it as a kwarg.
    Captured here but **not** forwarded to the inner callable — the
    inner is a ``StructuredTool`` whose own coroutine signature does
    not include ``tool_call_id``; passing it would raise
    ``TypeError: unexpected keyword argument``. PRD §3 Part A.
    """

    original_schema = tool.args_schema
    wrapped_schema = wrap_args_schema(original_schema)
    original_func = getattr(tool, "func", None)
    original_coroutine = getattr(tool, "coroutine", None)

    update: dict[str, object] = {"args_schema": wrapped_schema}

    if callable(original_func):

        def _wrapped_func(*, tool_call_id: str = "", **kwargs: Any) -> Any:
            del tool_call_id  # captured for LangChain, not forwarded — see docstring
            real, _ = strip_display(kwargs)
            return original_func(**real)

        update["func"] = _wrapped_func

    if callable(original_coroutine):

        async def _wrapped_coroutine(*, tool_call_id: str = "", **kwargs: Any) -> Any:
            del tool_call_id  # captured for LangChain, not forwarded — see docstring
            real, _ = strip_display(kwargs)
            return await original_coroutine(**real)

        update["coroutine"] = _wrapped_coroutine

    return tool.model_copy(update=update)


def _wrap_base_tool_via_delegation(tool: Any, structured_tool_cls: type) -> Any:
    """Build a new ``StructuredTool`` that delegates to ``tool.ainvoke``.

    Used for non-``StructuredTool`` ``BaseTool`` subclasses where we
    can't safely mutate the args_schema + invoke methods in place. The
    delegate invokes the original via ``BaseTool.ainvoke(envelope)``
    where ``envelope`` is a full LangChain ``ToolCall`` dict
    (:class:`_DispatchEnvelope`).

    The full envelope is required when the inner tool's schema declares
    :class:`InjectedToolCallId` (citation-capturing wrapper, every MCP
    tool); passing a plain args dict raises ``ValueError`` from
    LangChain's tool dispatch. The envelope is benign for inner tools
    that do not declare the annotation — ``id`` is metadata, args are
    extracted unchanged. PRD §3 Part A.
    """

    original_schema = getattr(tool, "args_schema", None)
    wrapped_schema = wrap_args_schema(original_schema)
    inner_name = getattr(tool, "name", "tool")

    async def _delegating_coroutine(*, tool_call_id: str = "", **kwargs: Any) -> Any:
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
