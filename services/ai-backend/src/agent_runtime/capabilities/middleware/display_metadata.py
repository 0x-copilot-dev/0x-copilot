"""Deterministic display-metadata synthesis for tool descriptors and display-field wrapping."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Mapping
from typing import Annotated, Any, ClassVar, Callable, get_type_hints

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolCallId
from pydantic import BaseModel, ConfigDict, Field, create_model

from agent_runtime.capabilities.tools.cards import ToolDisplayTemplate


_LOGGER = logging.getLogger(__name__)

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

# A tool_call_id is the provider's correlation handle between a tool call and
# its result. It is injected by LangChain from the model's own tool call, so a
# wrapper never invents one — it either receives the real id or it has a bug.
#
# The field cannot simply be required: it is injected, so it must carry a
# default for schema construction. The default used to be "", which is why this
# failed the way it did — an empty string is a legitimate-looking value, so it
# passed every internal boundary and only surfaced turns later inside the
# provider SDK as `Invalid 'input[3].call_id': empty string`, with a traceback
# pointing at langchain_openai rather than at the tool that produced it.
#
# So the default is a sentinel that can never be mistaken for data, and the
# boundary below refuses to build an envelope around one.
UNINJECTED_TOOL_CALL_ID = "__uninjected_tool_call_id__"


class MissingToolCallIdError(RuntimeError):
    """A wrapped tool tried to emit a result carrying no usable tool_call_id.

    Raised at the envelope boundary rather than left to the provider. Providers
    disagree about this: OpenAI rejects an empty ``call_id`` outright with a 400,
    while Anthropic accepts it — so the same defect is fatal on one model and
    invisible on another. Failing here makes it deterministic, and names the
    tool that caused it instead of surfacing as an opaque request error.
    """


def require_tool_call_id(tool_call_id: str, *, tool_name: str) -> str:
    """Return ``tool_call_id`` when it is usable, else raise.

    The single choke point for this invariant. Wrappers are added over time —
    the display wrapper, the budget guard, and whatever comes next — and each
    one is a chance to drop the injection. Checking here covers all of them
    instead of asking every future wrapper to remember.
    """
    if tool_call_id and tool_call_id != UNINJECTED_TOOL_CALL_ID:
        return tool_call_id
    raise MissingToolCallIdError(
        f"tool {tool_name!r} produced a result with no tool_call_id "
        f"(got {tool_call_id!r}). The id is injected from the model's tool "
        f"call; a missing one means a wrapper in the chain did not declare or "
        f"forward the InjectedToolCallId field."
    )


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

    **These two descriptions are paid for once per tool, on every turn.** The
    wrap is applied to every model-visible tool, so anything written here is
    duplicated across the whole surface — a dozen-odd tools today. The
    descriptions therefore carry only the *shape* of each field (what kind of
    string, roughly how long), which is what a caller needs at the point of
    use and cannot infer from the field name alone.

    The *convention* — when to override at all, worked examples, and the
    counter-examples that stop the model writing a narrated sentence — is
    stated once in ``DISPLAY_FIELD_CONVENTION``
    (``agent_runtime.prompts.runtime``), which the runtime folds into the
    system prompt. That fragment is installation-scoped immutable policy and
    joins the cacheable stable prefix, so it is amortised to roughly nothing,
    whereas per-tool schema bytes are re-sent in full on every request and
    cannot be cached that way. Keep the split: guidance that is *identical for
    every tool* belongs in the prompt, not here.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )

    display_title: str | None = Field(
        default=None,
        alias=DISPLAY_TITLE_KEY,
        description=(
            "Optional activity-card title: a 3-7 word noun phrase, not a sentence."
        ),
    )
    display_summary: str | None = Field(
        default=None,
        alias=DISPLAY_SUMMARY_KEY,
        description=(
            "Optional activity-card body: one ~10-15 word clause on why this "
            "call helps."
        ),
    )
    # This is injected by LangGraph and omitted from the model-visible schema.
    # It is the provider's correlation id, not a user argument. Every display
    # wrapper must declare it: the BaseTool-delegation branch returns the
    # inner ToolMessage directly, and therefore needs the exact id to build
    # the nested ToolCall envelope. Leaving this as an ordinary defaulted
    # coroutine parameter silently produced ``ToolMessage(tool_call_id="")``
    # for tools whose original schema did not already declare the injection.
    tool_call_id: Annotated[str, InjectedToolCallId] = UNINJECTED_TOOL_CALL_ID


def wrap_args_schema(args_schema: object | None) -> object:
    """Return a Pydantic model that extends ``args_schema`` with optional ``_display_title`` and ``_display_summary`` fields.

    Returns ``_DisplayFields`` directly when ``args_schema`` is ``None``.
    Idempotent: a schema bearing the ``__display_wrapped__`` marker is returned unchanged,
    making it safe to call on tools lists that pass through the wrap more than once.

    A **raw JSON-Schema mapping** is returned unchanged. LangChain allows a tool's
    ``args_schema`` to be a plain dict, and ``langchain-mcp-adapters`` always uses
    one — it assigns the server's ``inputSchema``, which the MCP spec types as a
    required ``dict[str, Any]``. Such a schema cannot be extended by
    ``create_model`` (there is no ``__name__`` and no base class to inherit), and
    it cannot express ``InjectedToolCallId`` at all, since injection is a Pydantic
    ``Annotated`` marker rather than anything JSON Schema can state. Attempting it
    raised ``AttributeError: 'dict' object has no attribute '__name__'`` **inside
    harness construction**, which surfaces as a generic non-retryable
    ``AgentRuntimeError`` — so with per-tool MCP registration enabled, every run
    in the deployment failed before the model was ever invoked, whether or not it
    touched a connector.

    The cost of returning it unchanged is bounded and correct: those tools simply
    do not gain the optional ``_display_title`` / ``_display_summary`` arguments.
    The alternative — synthesizing a model from the JSON Schema — would rewrite
    the connector's own argument contract, and a lossy rewrite there is a far
    worse failure than a missing display hint.
    """

    if args_schema is None:
        return _DisplayFields  # already exactly what we need
    if isinstance(args_schema, Mapping):
        return args_schema
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

    The args dict is where the agent's tool-call args land. LangChain/Pydantic
    may preserve the model-facing aliases (``_display_title`` /
    ``_display_summary``) or serialise their validated field names
    (``display_title`` / ``display_summary``), so both forms are canonical
    inputs here. This is the same shape for regular tools and for the
    ``call_mcp_tool`` dispatcher: display fields remain at the top level of
    the dispatcher's args, not nested inside ``args.arguments``.

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
    title = _non_empty_string(args.get(DISPLAY_TITLE_KEY)) or _non_empty_string(
        args.get("display_title")
    )
    summary = _non_empty_string(args.get(DISPLAY_SUMMARY_KEY)) or _non_empty_string(
        args.get("display_summary")
    )
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

    The wrapped tool inherits the inner's Context Occupancy declaration. The
    ``StructuredTool`` branch keeps it for free (the stamp lives in the instance
    ``__dict__``, which ``model_copy`` carries), but the delegation branch
    *rebuilds* the tool with ``from_function`` and holds the inner in a closure,
    so the new object had neither the stamp nor an attribute chain leading back
    to it. That silently un-declared every non-``StructuredTool`` on the model
    surface — ``web_search``, which arrives wrapped in ``RetryingTool`` — and
    the occupancy report blamed a composition site that had declared it
    correctly. Carrying it here rather than re-declaring downstream keeps the
    rule that a declaration is made once, by the code that composed the text.
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
        return _with_carried_context_origin(
            tool, _wrap_structured_tool(tool, StructuredTool)
        )
    if isinstance(tool, BaseTool):
        return _with_carried_context_origin(
            tool, _wrap_base_tool_via_delegation(tool, StructuredTool)
        )
    return tool


def _with_carried_context_origin(source: object, wrapped: object) -> object:
    """Preserve ``source``'s occupancy declaration on the tool that replaces it.

    Deferred import for the same reason ``ModelToolDeclaration`` defers its own:
    ``capabilities.middleware`` is imported eagerly from the execution lane, and
    a module-scope edge into the observability lane would make importing either
    package order-dependent.

    Fail-open, because declaring an origin is observability and this function is
    on the graph-build path: a binding that cannot be read or written costs a
    label in a report, never a working tool (§6.4).
    """

    if wrapped is source:
        return wrapped
    try:
        from agent_runtime.observability.context_origin import (  # noqa: PLC0415
            carry_context_origin,
        )

        return carry_context_origin(source, wrapped)
    except Exception:  # noqa: BLE001 — a label is never worth breaking a tool
        _LOGGER.debug(
            "Could not carry a context origin onto a display-wrapped tool; "
            "it will measure as UNDECLARED.",
            exc_info=True,
        )
        return wrapped


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
        """Build a LangChain ToolCall envelope dict.

        Refuses an unusable id: this envelope becomes the inner ToolMessage that
        enters conversation history, so a bad id here is not a local error — it
        poisons every subsequent model call in the run.
        """
        return {
            cls.KEY_ARGS: args,
            cls.KEY_NAME: name,
            cls.KEY_ID: require_tool_call_id(tool_call_id, tool_name=name),
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
        config_param = _runnable_config_parameter(original_func)

        if config_param is None:

            def _wrapped_func(
                *, tool_call_id: str = UNINJECTED_TOOL_CALL_ID, **kwargs: Any
            ) -> Any:
                """Sync dispatch path: strip display args and invoke the inner function."""
                del (
                    tool_call_id
                )  # captured by LangChain injection; not forwarded to inner
                real, _ = strip_display(kwargs)
                return original_func(**real)

        else:

            def _wrapped_func(
                *,
                tool_call_id: str = UNINJECTED_TOOL_CALL_ID,
                config: RunnableConfig,
                **kwargs: Any,
            ) -> Any:
                """Preserve LangChain's injected config for config-aware inner tools."""
                del tool_call_id
                real, _ = strip_display(kwargs)
                real[config_param] = config
                return original_func(**real)

        update["func"] = _wrapped_func

    if callable(original_coroutine):
        config_param = _runnable_config_parameter(original_coroutine)

        if config_param is None:

            async def _wrapped_coroutine(
                *, tool_call_id: str = UNINJECTED_TOOL_CALL_ID, **kwargs: Any
            ) -> Any:
                """Async dispatch path: strip display args and await the inner coroutine."""
                del (
                    tool_call_id
                )  # captured for LangChain, not forwarded — see docstring
                real, _ = strip_display(kwargs)
                return await original_coroutine(**real)

        else:

            async def _wrapped_coroutine(
                *,
                tool_call_id: str = UNINJECTED_TOOL_CALL_ID,
                config: RunnableConfig,
                **kwargs: Any,
            ) -> Any:
                """Preserve LangChain's injected config for config-aware inner tools."""
                del tool_call_id
                real, _ = strip_display(kwargs)
                real[config_param] = config
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

    async def _delegating_coroutine(
        *,
        tool_call_id: str = UNINJECTED_TOOL_CALL_ID,
        config: RunnableConfig,
        **kwargs: Any,
    ) -> Any:
        """Delegate to ``tool.ainvoke`` with a full LangChain tool-call envelope.

        When nothing was injected the plain arguments are forwarded instead. That
        happens for a tool whose ``args_schema`` is a raw JSON-Schema mapping —
        ``langchain-mcp-adapters`` always builds one — because injection is a
        Pydantic ``Annotated`` marker that such a schema cannot declare, so
        LangChain has no field to inject into and passes the sentinel. Building
        the envelope anyway would hand ``require_tool_call_id`` an unusable id and
        raise, failing the call at the wrapper rather than running the tool.
        """

        real, _ = strip_display(kwargs)
        if tool_call_id == UNINJECTED_TOOL_CALL_ID:
            return await tool.ainvoke(real, config=config)
        envelope = _DispatchEnvelope.build(
            args=real,
            name=inner_name,
            tool_call_id=tool_call_id,
        )
        return await tool.ainvoke(envelope, config=config)

    return structured_tool_cls.from_function(
        coroutine=_delegating_coroutine,
        name=inner_name,
        description=getattr(tool, "description", ""),
        args_schema=wrapped_schema,
    )


def _runnable_config_parameter(func: Callable[..., Any]) -> str | None:
    """Return the parameter LangChain injects with ``RunnableConfig``.

    ``StructuredTool`` detects config support from the callable's resolved type
    hints.  A wrapper must retain the same typed parameter; otherwise the
    wrapped callable is invoked without its required runtime configuration.
    This deliberately mirrors LangChain's detector without importing its
    private helper.
    """

    try:
        hints = get_type_hints(func)
    except (NameError, TypeError):
        hints = {}
    for name, annotation in hints.items():
        if annotation is RunnableConfig:
            return name
    # Nested test adapters and dynamically-created tools can retain a string
    # forward reference that cannot be resolved from the module globals.  The
    # signature still carries enough information to preserve LangChain's
    # injection contract.
    for name, parameter in inspect.signature(func).parameters.items():
        annotation = parameter.annotation
        if (
            annotation is RunnableConfig
            or str(annotation).strip("'") == "RunnableConfig"
        ):
            return name
    return None
