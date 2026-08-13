"""Repair a vendor MCP tool's JSON Schema instead of hard-rejecting the tool.

Why this exists
---------------
``McpToolDescriptor.input_schema`` used to *raise* on two shapes real
connectors ship every day: a schema with no top-level ``type``, and a schema
larger than :attr:`Limits.MCP_SCHEMA_MAX_BYTES`. Because both raises happen
inside a Pydantic field validator, the failure never reached the user as a
failure — it reached them as an absence. ``McpLoaderHelpers.parse_tools``
turns any ``ValidationError`` into ``MALFORMED_DESCRIPTOR`` **for the whole
server**, and ``BackendMcpClient._tool_descriptor`` lets the error escape
``list_tools_page`` into the loader's ``except ValidationError`` arm. So one
Jira ``createIssue`` payload with an omitted ``type`` deleted the entire
connector from the model's surface, and the agent politely said it could not
do the thing.

Both comparators repair rather than reject: Hermes normalises MCP input
schemas at ingestion (``_normalize_mcp_input_schema``, shipped against a live
bug report), and OpenCode lowers schemas per model family in
``provider/transform.ts``. This module is our version of the same idea, with
one addition neither has: every repair is *observable*, because a silent
repair is how a vendor bug becomes our mystery.

The repairs are the members of :class:`McpSchemaRepairRule`, each documented
at the method that applies it. Then, and only when the encoded schema is
still over the byte ceiling, it degrades in three ordered stages — drop
``examples``, drop ``default``, truncate over-long ``description`` prose —
re-measuring after each and failing only if the schema is *still* over.

Degradation removes documentation, never contract: every property, type,
enum and ``required`` name survives, so the call stays constructible. And the
surviving schema is exactly what ``McpCatalogRenderer.tool_file``
(``capabilities/mcp/catalog.py``) writes into the descriptor filesystem as
pretty-printed JSON, which the model recovers with a single ``read_file`` —
so the contract is never squeezed into a tool-listing blurb in the first
place. Only prose is lost, and only above 16 KB.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum
import json
import logging
from typing import Any, Iterator

from agent_runtime.capabilities.mcp.constants import Keys, Limits, Messages, Values
from agent_runtime.execution.contracts import RuntimeContract


class McpSchemaRepairRule(StrEnum):
    """Closed vocabulary of repairs, one member per rule that can fire.

    Closed on purpose: these values are log labels, so a free-form string
    would let two spellings of the same repair coexist and make the log
    un-greppable.
    """

    TYPE_COERCED = "type_coerced"
    DEFS_RENAMED = "defs_renamed"
    NULLABLE_UNION_COLLAPSED = "nullable_union_collapsed"
    REQUIRED_PRUNED = "required_pruned"
    BOOLEAN_FORM_DROPPED = "boolean_form_dropped"
    EXAMPLES_STRIPPED = "examples_stripped"
    DEFAULTS_STRIPPED = "defaults_stripped"
    DESCRIPTIONS_TRUNCATED = "descriptions_truncated"


class McpSchemaRepairReport(RuntimeContract):
    """Typed record of what a single schema repair changed, and by how much.

    Carries the connector identity as well as the rules because "some schema
    somewhere was repaired" is not evidence — the next reader needs to know
    which vendor to file the bug against.
    """

    server_name: str
    tool_name: str
    field_name: str
    rules: tuple[McpSchemaRepairRule, ...] = ()
    original_bytes: int
    repaired_bytes: int

    @property
    def repaired(self) -> bool:
        """Return ``True`` when at least one repair rule fired."""

        return bool(self.rules)

    def rule_labels(self) -> str:
        """Return the fired rules as one stable, comma-joined log field."""

        return ",".join(rule.value for rule in self.rules) or McpSchemaRepairLog.NONE


class _RepairLedger:
    """Mutable accumulator threaded through one repair pass.

    Deliberately not a contract: it is scratch state for a single call, and
    :class:`McpSchemaRepairReport` is the typed thing that leaves the module.
    """

    def __init__(self) -> None:
        self._rules: set[McpSchemaRepairRule] = set()

    def add(self, rule: McpSchemaRepairRule) -> None:
        """Record that ``rule`` fired at least once during this pass."""

        self._rules.add(rule)

    def fired(self) -> tuple[McpSchemaRepairRule, ...]:
        """Return the fired rules in a stable (alphabetical) order."""

        return tuple(sorted(self._rules, key=lambda rule: rule.value))


class McpSchemaRepairLog:
    """The one structured line every repair emits.

    Mirrors ``McpLoadFailureLog`` (``capabilities/mcp/loader.py``) and
    ``catalog.Log``: the module's existing telemetry channel is a named
    format string on a class plus ``logging.getLogger(__name__)``, so a live
    incident is one ``grep`` rather than one live reproduction. Nothing
    connector-authored is logged — only the server slug, the tool slug, the
    field name, closed-vocabulary rule labels, and two byte counts.
    """

    _LOGGER = logging.getLogger(__name__)

    #: Placeholder for a field we could not resolve, so it is never blank.
    NONE = "-"

    APPLIED = (
        "mcp_schema_repair.applied server=%s tool=%s field=%s rules=%s bytes=%d->%d"
    )
    REFUSED = "mcp_schema_repair.refused server=%s tool=%s field=%s bytes=%d limit=%d"

    _SERVER_CTX: ContextVar[str | None] = ContextVar(
        "mcp_schema_repair_server", default=None
    )

    @classmethod
    @contextmanager
    def for_server(cls, server_name: str) -> Iterator[None]:
        """Attribute repairs raised inside this block to ``server_name``.

        The repair runs inside a Pydantic field validator, which sees the
        descriptor's own fields but not the card that hosts it. Providers wrap
        descriptor construction in this so the log line names the connector.
        Unbound (replay / eval / unit tests) the server field reads ``-``.
        """

        token = cls._SERVER_CTX.set(server_name)
        try:
            yield
        finally:
            cls._SERVER_CTX.reset(token)

    @classmethod
    def active_server(cls) -> str:
        """Return the bound server name, or the ``-`` placeholder."""

        return cls._SERVER_CTX.get(None) or cls.NONE

    @classmethod
    def record(cls, report: McpSchemaRepairReport) -> None:
        """Emit the applied-repair line; do nothing when nothing was repaired."""

        if not report.repaired:
            return
        cls._LOGGER.warning(
            cls.APPLIED,
            report.server_name,
            report.tool_name,
            report.field_name,
            report.rule_labels(),
            report.original_bytes,
            report.repaired_bytes,
        )

    @classmethod
    def refused(cls, *, tool_name: str, field_name: str, size: int) -> None:
        """Emit the line for a schema still over the ceiling after degrading."""

        cls._LOGGER.warning(
            cls.REFUSED,
            cls.active_server(),
            tool_name or cls.NONE,
            field_name,
            size,
            Limits.MCP_SCHEMA_MAX_BYTES,
        )


class McpSchemaRepair:
    """Normalise a vendor JSON Schema into one every provider will accept."""

    class Keyword:
        """JSON Schema keyword names this walker understands structurally."""

        ADDITIONAL_PROPERTIES = "additionalProperties"
        ANY_OF = "anyOf"
        DEFAULT = "default"
        DEFINITIONS = "definitions"
        DEFS = "$defs"
        DESCRIPTION = "description"
        ENUM = "enum"
        EXAMPLES = "examples"
        ITEMS = "items"
        ONE_OF = "oneOf"
        REF = "$ref"
        TITLE = "title"

    class Pointer:
        """Local ``$ref`` prefixes rewritten when ``definitions`` is renamed."""

        LEGACY = "#/definitions/"
        CANONICAL = "#/$defs/"

    #: Keys whose value is a ``name -> schema`` map. Their keys are
    #: user-facing property names, never meta-keywords — so ``definitions``
    #: used as a *property name* (a pipelines tool exposing a
    #: ``definitions: [id]`` argument) is preserved verbatim rather than
    #: rewritten to ``$defs``, which providers reject as a ``$``-prefixed
    #: property name. Same gate Hermes documents on ``_rewrite_local_refs``.
    _SCHEMA_MAPS = frozenset(
        {
            Keys.Schema.PROPERTIES,
            "patternProperties",
            "dependentSchemas",
            Keyword.DEFS,
            Keyword.DEFINITIONS,
        }
    )
    #: Keys whose value is a list of schemas.
    _SCHEMA_LISTS = frozenset({Keyword.ANY_OF, Keyword.ONE_OF, "allOf", "prefixItems"})
    #: The subset of :attr:`_SCHEMA_LISTS` that composes *alternatives*. A node
    #: carrying one of these has no type of its own and must not be given one —
    #: unlike ``prefixItems``, which is a tuple-array and *does* imply ``array``.
    _COMPOSITION_KEYS = frozenset({Keyword.ANY_OF, Keyword.ONE_OF, "allOf"})
    #: Keys whose value is a single schema (or, for ``items``, possibly the
    #: draft-07 tuple form, which is a list of schemas).
    _SCHEMA_VALUES = frozenset(
        {
            Keyword.ITEMS,
            Keyword.ADDITIONAL_PROPERTIES,
            "additionalItems",
            "contains",
            "not",
            "if",
            "then",
            "else",
            "propertyNames",
            "unevaluatedItems",
            "unevaluatedProperties",
        }
    )

    #: Keyword -> implied ``type``, checked in order. Mirrors the inference
    #: table OpenCode uses in ``sanitizeOpenAISchema``.
    _IMPLIED_TYPES: tuple[tuple[frozenset[str], str], ...] = (
        (
            frozenset(
                {
                    Keys.Schema.PROPERTIES,
                    Keys.Schema.REQUIRED,
                    Keyword.ADDITIONAL_PROPERTIES,
                    "patternProperties",
                }
            ),
            Values.SchemaType.OBJECT,
        ),
        (frozenset({Keyword.ITEMS, "prefixItems"}), Values.SchemaType.ARRAY),
        (
            frozenset({Keyword.ENUM, "const", "format", "pattern", "minLength"}),
            Values.SchemaType.STRING,
        ),
        (
            frozenset(
                {
                    "minimum",
                    "maximum",
                    "exclusiveMinimum",
                    "exclusiveMaximum",
                    "multipleOf",
                }
            ),
            Values.SchemaType.NUMBER,
        ),
    )

    #: A boolean in a schema position means "anything" / "nothing". Providers
    #: reject it outright, so it becomes the same permissive string node
    #: OpenCode's ``sanitizeOpenAISchema`` substitutes.
    _BOOLEAN_REPLACEMENT: Mapping[str, Any] = {
        Keys.Schema.TYPE: Values.SchemaType.STRING
    }

    _NULL_TYPE = Values.SchemaType.NULL
    #: Keys a ``{"type": "null"}`` branch may also carry and still count as
    #: the null half of a nullable union.
    _NULL_BRANCH_EXTRAS = frozenset({Keyword.TITLE, Keyword.DESCRIPTION})

    @classmethod
    def repair(
        cls,
        value: Mapping[str, Any],
        *,
        field_name: str,
        tool_name: str = "",
        max_bytes: int = Limits.MCP_SCHEMA_MAX_BYTES,
    ) -> tuple[dict[str, Any], McpSchemaRepairReport]:
        """Return ``(repaired schema, report)`` for one vendor schema.

        Raises ``ValueError`` only for the two conditions no repair can fix:
        a schema that is not JSON-serialisable at all, and one still over
        ``max_bytes`` after every degradation stage has run.
        """

        ledger = _RepairLedger()
        original_bytes = cls._encoded_size(value, field_name)
        repaired = cls._map_nodes(
            value, lambda node, top: cls._repair(node, top, ledger)
        )
        size = cls._encoded_size(repaired, field_name)
        if size > max_bytes:
            repaired, size = cls._degrade(
                repaired,
                ledger=ledger,
                field_name=field_name,
                max_bytes=max_bytes,
                size=size,
            )
        if size > max_bytes:
            McpSchemaRepairLog.refused(
                tool_name=tool_name, field_name=field_name, size=size
            )
            raise ValueError(Messages.Validation.schema_size_exceeded(field_name))
        return repaired, McpSchemaRepairReport(
            server_name=McpSchemaRepairLog.active_server(),
            tool_name=tool_name or McpSchemaRepairLog.NONE,
            field_name=field_name,
            rules=ledger.fired(),
            original_bytes=original_bytes,
            repaired_bytes=size,
        )

    # ---------------------------------------------------------------- walking

    @classmethod
    def _map_nodes(
        cls,
        node: object,
        transform: Callable[[Any, bool], dict[str, Any]],
        *,
        top_level: bool = True,
    ) -> dict[str, Any]:
        """Apply ``transform`` bottom-up to every *schema* node in ``node``.

        Structural, not generic: the walker descends only into positions
        JSON Schema defines as schemas, so a property literally named
        ``items``/``default``/``examples`` is never mistaken for a keyword.
        """

        if isinstance(node, bool) or not isinstance(node, Mapping):
            return transform(node, top_level)
        out: dict[str, Any] = {}
        for raw_key, value in node.items():
            key = str(raw_key)
            if key in cls._SCHEMA_MAPS and isinstance(value, Mapping):
                out[key] = {
                    str(name): cls._map_nodes(sub, transform, top_level=False)
                    for name, sub in value.items()
                }
            elif key in cls._SCHEMA_LISTS and isinstance(value, list):
                out[key] = [
                    cls._map_nodes(item, transform, top_level=False) for item in value
                ]
            elif key in cls._SCHEMA_VALUES:
                out[key] = cls._map_schema_value(key, value, transform)
            else:
                out[key] = value
        return transform(out, top_level)

    @classmethod
    def _map_schema_value(
        cls,
        key: str,
        value: object,
        transform: Callable[[Any, bool], dict[str, Any]],
    ) -> object:
        """Map a single-schema keyword's value, preserving the shapes we keep."""

        if key == cls.Keyword.ADDITIONAL_PROPERTIES and isinstance(value, bool):
            # The one place the boolean form is canonical and universally
            # accepted; rewriting it would change the schema's meaning.
            return value
        if isinstance(value, list):
            # Draft-07 tuple-form ``items``. Preserved as a tuple, with every
            # element repaired: collapsing it to one schema is a *per-provider*
            # lowering (OpenCode does it only for Moonshot/MFJS, in
            # ``sanitizeMoonshot``), and this module runs once at descriptor
            # ingestion, before any model is chosen. Flattening here would
            # discard positional types for every provider that accepts them.
            return [cls._map_nodes(item, transform, top_level=False) for item in value]
        return cls._map_nodes(value, transform, top_level=False)

    # ---------------------------------------------------------------- repairs

    @classmethod
    def _repair(
        cls, node: Any, top_level: bool, ledger: _RepairLedger
    ) -> dict[str, Any]:
        """Run every repair rule against one already-recursed schema node."""

        if isinstance(node, bool):
            ledger.add(McpSchemaRepairRule.BOOLEAN_FORM_DROPPED)
            return dict(cls._BOOLEAN_REPLACEMENT)
        if not isinstance(node, Mapping):
            # A non-mapping in a schema position is not a schema at all;
            # substituting the permissive node keeps the parent valid.
            ledger.add(McpSchemaRepairRule.BOOLEAN_FORM_DROPPED)
            return dict(cls._BOOLEAN_REPLACEMENT)
        repaired = cls._rename_defs(dict(node), ledger)
        repaired = cls._collapse_nullable_union(repaired, ledger)
        repaired = cls._coerce_type(repaired, top_level, ledger)
        return cls._prune_required(repaired, ledger)

    @classmethod
    def _rename_defs(
        cls, node: dict[str, Any], ledger: _RepairLedger
    ) -> dict[str, Any]:
        """Promote draft-07 ``definitions`` to ``$defs`` and repoint ``$ref``."""

        legacy = node.pop(cls.Keyword.DEFINITIONS, None)
        if isinstance(legacy, Mapping):
            existing = node.get(cls.Keyword.DEFS)
            merged = dict(existing) if isinstance(existing, Mapping) else {}
            # ``$defs`` wins on a collision: it is the canonical spelling and
            # the one any already-correct ``$ref`` points at.
            node[cls.Keyword.DEFS] = {**legacy, **merged}
            ledger.add(McpSchemaRepairRule.DEFS_RENAMED)
        elif legacy is not None:
            node[cls.Keyword.DEFINITIONS] = legacy
        ref = node.get(cls.Keyword.REF)
        if isinstance(ref, str) and ref.startswith(cls.Pointer.LEGACY):
            node[cls.Keyword.REF] = (
                cls.Pointer.CANONICAL + ref[len(cls.Pointer.LEGACY) :]
            )
            ledger.add(McpSchemaRepairRule.DEFS_RENAMED)
        return node

    @classmethod
    def _collapse_nullable_union(
        cls, node: dict[str, Any], ledger: _RepairLedger
    ) -> dict[str, Any]:
        """Collapse ``[{...}, {"type": "null"}]`` into one nullable type.

        Every Pydantic-backed MCP server emits this for an optional field, and
        the ``anyOf`` *wrapper* is the part that breaks strict-schema
        providers: it hides the real type behind a composition node, so a
        string field stops looking like a string.

        We collapse to ``type: [t, "null"]``. Hermes goes one step further and
        drops ``"null"`` entirely, because Anthropic rejects a nullable branch
        in a tool input schema. That is a *per-provider lowering*, and this
        module runs once at descriptor ingestion, before any model is chosen —
        so discarding the vendor's declared nullability here would lose
        information for every provider that accepts it (a field that takes an
        explicit ``null`` is not the same as an absent field). Optionality is
        carried by the parent's ``required`` list either way, so the
        Anthropic-shaped narrowing belongs in a provider lowering step, not
        here. See ``transform.ts``'s per-model sanitizers for the same split.
        """

        for keyword in (cls.Keyword.ANY_OF, cls.Keyword.ONE_OF):
            branches = node.get(keyword)
            if not isinstance(branches, list) or len(branches) != 2:
                continue
            survivors = [b for b in branches if not cls._is_null_branch(b)]
            if len(survivors) != 1 or not isinstance(survivors[0], Mapping):
                continue
            survivor = dict(survivors[0])
            # Parent-authored keys win: ``description`` / ``title`` /
            # ``default`` are written on the property, not on the branch.
            survivor.update(
                {
                    key: value
                    for key, value in node.items()
                    if key not in (keyword, Keys.Schema.TYPE)
                }
            )
            survivor[Keys.Schema.TYPE] = cls._nullable_type(survivors[0])
            if survivor[Keys.Schema.TYPE] is None:
                # A ``$ref`` branch has no type to make nullable; keep the
                # reference and drop the empty key rather than emit ``null``.
                survivor.pop(Keys.Schema.TYPE)
            ledger.add(McpSchemaRepairRule.NULLABLE_UNION_COLLAPSED)
            return survivor
        return node

    @classmethod
    def _is_null_branch(cls, branch: object) -> bool:
        """Return ``True`` for the ``{"type": "null"}`` half of a union."""

        if not isinstance(branch, Mapping):
            return False
        if branch.get(Keys.Schema.TYPE) != cls._NULL_TYPE:
            return False
        extras = {str(key) for key in branch} - {Keys.Schema.TYPE}
        return extras <= cls._NULL_BRANCH_EXTRAS

    @classmethod
    def _nullable_type(cls, survivor: Mapping[str, Any]) -> list[str] | None:
        """Return the surviving branch's type widened with ``"null"``."""

        declared = survivor.get(Keys.Schema.TYPE)
        if isinstance(declared, str) and declared:
            return [declared, cls._NULL_TYPE]
        if isinstance(declared, list) and declared:
            names = [str(name) for name in declared]
            return names if cls._NULL_TYPE in names else [*names, cls._NULL_TYPE]
        return None

    @classmethod
    def _coerce_type(
        cls, node: dict[str, Any], top_level: bool, ledger: _RepairLedger
    ) -> dict[str, Any]:
        """Fill a missing / ``null`` ``type`` from implied keywords."""

        if cls._has_usable_type(node):
            return node
        had_key = Keys.Schema.TYPE in node
        node.pop(Keys.Schema.TYPE, None)
        if cls.Keyword.REF in node or any(
            keyword in node for keyword in cls._COMPOSITION_KEYS
        ):
            # ``$ref`` and composition nodes legitimately carry no type.
            if had_key:
                ledger.add(McpSchemaRepairRule.TYPE_COERCED)
            return node
        inferred = cls._infer_type(node)
        if inferred is None and top_level:
            # An MCP ``inputSchema`` is an argument bag by protocol, so the
            # top level has exactly one honest fallback.
            inferred = Values.SchemaType.OBJECT
        if inferred is None:
            if had_key:
                ledger.add(McpSchemaRepairRule.TYPE_COERCED)
            return node
        node[Keys.Schema.TYPE] = inferred
        # Give the newly-typed node the companion keyword its type requires:
        # a typed ``object`` with no ``properties`` leaves every ``required``
        # name dangling, and a typed ``array`` with no ``items`` is rejected
        # outright under OpenAI strict tool schemas. Both mirror the tail of
        # OpenCode's ``sanitizeOpenAISchema``.
        if inferred == Values.SchemaType.OBJECT and not isinstance(
            node.get(Keys.Schema.PROPERTIES), Mapping
        ):
            node[Keys.Schema.PROPERTIES] = {}
        if inferred == Values.SchemaType.ARRAY and cls.Keyword.ITEMS not in node:
            node[cls.Keyword.ITEMS] = {Keys.Schema.TYPE: Values.SchemaType.STRING}
        ledger.add(McpSchemaRepairRule.TYPE_COERCED)
        return node

    @classmethod
    def _has_usable_type(cls, node: Mapping[str, Any]) -> bool:
        """Return ``True`` when ``type`` is already a usable string or list."""

        declared = node.get(Keys.Schema.TYPE)
        if isinstance(declared, str):
            return bool(declared.strip())
        return isinstance(declared, list) and bool(declared)

    @classmethod
    def _infer_type(cls, node: Mapping[str, Any]) -> str | None:
        """Return the ``type`` implied by the node's other keywords, if any."""

        for keywords, implied in cls._IMPLIED_TYPES:
            if any(keyword in node for keyword in keywords):
                return implied
        return None

    @classmethod
    def _prune_required(
        cls, node: dict[str, Any], ledger: _RepairLedger
    ) -> dict[str, Any]:
        """Drop ``required`` names that no sibling property defines."""

        if Values.SchemaType.OBJECT not in cls._declared_types(node):
            return node
        required = node.get(Keys.Schema.REQUIRED)
        if not isinstance(required, list):
            return node
        properties = node.get(Keys.Schema.PROPERTIES)
        if not isinstance(properties, Mapping):
            # No ``properties`` at all: every name dangles — but only if
            # nothing *else* on the node could be defining them. ``$ref`` /
            # ``allOf`` compose in properties from elsewhere, and
            # ``patternProperties`` defines them by regex, so a node carrying
            # any of those is left alone rather than stripped of its contract.
            if cls._defines_properties_indirectly(node):
                return node
            properties = {}
        kept = [
            name for name in required if isinstance(name, str) and name in properties
        ]
        if len(kept) == len(required):
            return node
        if kept:
            node[Keys.Schema.REQUIRED] = kept
        else:
            node.pop(Keys.Schema.REQUIRED, None)
        ledger.add(McpSchemaRepairRule.REQUIRED_PRUNED)
        return node

    #: Keywords that can supply property names a sibling ``properties`` map
    #: does not list, which is what makes pruning unsafe on such a node.
    _INDIRECT_PROPERTY_KEYS = frozenset(
        {Keyword.REF, "allOf", Keyword.ANY_OF, Keyword.ONE_OF, "patternProperties"}
    )

    @classmethod
    def _defines_properties_indirectly(cls, node: Mapping[str, Any]) -> bool:
        """Return ``True`` when properties may come from somewhere else."""

        return any(keyword in node for keyword in cls._INDIRECT_PROPERTY_KEYS)

    @classmethod
    def _declared_types(cls, node: Mapping[str, Any]) -> frozenset[str]:
        """Return the node's declared type names as a set."""

        declared = node.get(Keys.Schema.TYPE)
        if isinstance(declared, str):
            return frozenset({declared})
        if isinstance(declared, list):
            return frozenset(str(name) for name in declared)
        return frozenset()

    # ------------------------------------------------------------ degradation

    @classmethod
    def _degrade(
        cls,
        schema: dict[str, Any],
        *,
        ledger: _RepairLedger,
        field_name: str,
        max_bytes: int,
        size: int,
    ) -> tuple[dict[str, Any], int]:
        """Shed documentation, cheapest first, until the schema fits.

        Ordered by what a caller loses: ``examples`` are illustrative,
        ``default`` is recoverable from the connector's own docs, and prose
        is truncated last because it is what tells the model *what the field
        means*. Stops at the first stage that fits, so a schema 200 bytes
        over keeps its descriptions.
        """

        stages: tuple[
            tuple[McpSchemaRepairRule, Callable[[dict[str, Any]], bool]], ...
        ] = (
            (
                McpSchemaRepairRule.EXAMPLES_STRIPPED,
                lambda node: cls._drop_keyword(node, cls.Keyword.EXAMPLES),
            ),
            (
                McpSchemaRepairRule.DEFAULTS_STRIPPED,
                lambda node: cls._drop_keyword(node, cls.Keyword.DEFAULT),
            ),
            (McpSchemaRepairRule.DESCRIPTIONS_TRUNCATED, cls._truncate_description),
        )
        for rule, stage in stages:
            changed = False

            def _apply(node: Any, _top: bool, _stage=stage) -> dict[str, Any]:
                nonlocal changed
                if not isinstance(node, dict):
                    return node
                changed = _stage(node) or changed
                return node

            schema = cls._map_nodes(schema, _apply)
            if changed:
                ledger.add(rule)
            size = cls._encoded_size(schema, field_name)
            if size <= max_bytes:
                return schema, size
        return schema, size

    @classmethod
    def _drop_keyword(cls, node: dict[str, Any], keyword: str) -> bool:
        """Remove ``keyword`` from ``node``; return whether anything went."""

        if keyword not in node:
            return False
        node.pop(keyword)
        return True

    @classmethod
    def _truncate_description(cls, node: dict[str, Any]) -> bool:
        """Clip an over-long ``description``; return whether anything changed."""

        description = node.get(cls.Keyword.DESCRIPTION)
        limit = Limits.SCHEMA_DESCRIPTION_MAX_LENGTH
        if not isinstance(description, str) or len(description) <= limit:
            return False
        node[cls.Keyword.DESCRIPTION] = description[:limit].rstrip() + "..."
        return True

    # ----------------------------------------------------------------- shared

    @classmethod
    def _encoded_size(cls, schema: Mapping[str, Any], field_name: str) -> int:
        """Return the UTF-8 byte length of ``schema`` encoded as JSON."""

        try:
            encoded = json.dumps(schema, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError(Messages.Validation.json_serializable(field_name)) from exc
        return len(encoded.encode(Keys.Encoding.UTF_8))


__all__ = [
    "McpSchemaRepair",
    "McpSchemaRepairLog",
    "McpSchemaRepairReport",
    "McpSchemaRepairRule",
]
