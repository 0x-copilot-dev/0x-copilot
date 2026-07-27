"""Closed, adapter-free contracts for governed dataflow plans.

The model-authored plan contains only opaque capability binding identifiers.
Trusted descriptor metadata is supplied separately by the runtime, so a plan
cannot assert that an effectful capability is safe or read-only.
"""

from __future__ import annotations

from enum import StrEnum
import math
import re
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from agent_runtime.capabilities.concurrency.contracts import ConcurrencyPolicy
from agent_runtime.execution.contracts import JsonValue, RuntimeContract
from agent_runtime.surfaces_v2.ledger_models import EffectClass

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")


class DataflowNodeKind(StrEnum):
    """The complete v1 node vocabulary."""

    MAP = "map"
    FILTER = "filter"
    SELECT = "select"
    SORT = "sort"
    LIMIT = "limit"
    REDUCE = "reduce"
    GROUP = "group"
    BRANCH = "branch"
    INVOKE = "invoke"
    BATCH_INVOKE = "batch_invoke"
    EMIT = "emit"


class DataflowExpressionKind(StrEnum):
    """The complete v1 pure-expression vocabulary."""

    LITERAL = "literal"
    FIELD = "field"
    EQUAL = "equal"
    NOT_EQUAL = "not_equal"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    AND = "and"
    OR = "or"
    NOT = "not"
    ADD = "add"
    SUBTRACT = "subtract"
    MULTIPLY = "multiply"
    DIVIDE = "divide"
    CONTAINS = "contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    LOWER = "lower"
    UPPER = "upper"
    LENGTH = "length"


class DataflowValueType(StrEnum):
    """Closed coarse value types understood by the v1 validator."""

    NULL = "null"
    BOOLEAN = "boolean"
    INTEGER = "integer"
    NUMBER = "number"
    STRING = "string"
    OBJECT = "object"
    ARRAY = "array"


class DataflowErrorPolicy(StrEnum):
    """Closed per-node failure behavior."""

    FAIL_PLAN = "fail_plan"
    SKIP_ITEM = "skip_item"
    COLLECT_ERROR = "collect_error"
    STOP_NEW = "stop_new"


def _require_identifier(value: str) -> str:
    normalized = value.strip()
    if _IDENTIFIER_PATTERN.fullmatch(normalized) is None:
        raise ValueError("identifier must use the bounded dataflow identifier syntax")
    return normalized


def _literal_type(value: JsonValue) -> DataflowValueType:
    if value is None:
        return DataflowValueType.NULL
    if isinstance(value, bool):
        return DataflowValueType.BOOLEAN
    if isinstance(value, int):
        return DataflowValueType.INTEGER
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("literal numbers must be finite")
        return DataflowValueType.NUMBER
    if isinstance(value, str):
        return DataflowValueType.STRING
    if isinstance(value, dict):
        return DataflowValueType.OBJECT
    if isinstance(value, list):
        return DataflowValueType.ARRAY
    raise ValueError("literal must be JSON-compatible")


class DataflowExpression(RuntimeContract):
    """One closed pure-expression node.

    There is deliberately no function name, source text, module, callable,
    transport, URL, filesystem path, or environment field.
    """

    op: DataflowExpressionKind
    literal: JsonValue = None
    field_path: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    field_type: DataflowValueType | None = None
    args: tuple["DataflowExpression", ...] = Field(default_factory=tuple, max_length=8)

    @field_validator("field_path", mode="before")
    @classmethod
    def _normalize_field_path(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, (str, bytes)):
            raise ValueError("field_path must be an array of identifiers")
        return tuple(_require_identifier(item) for item in value)  # type: ignore[arg-type]

    @model_validator(mode="after")
    def _closed_shape_and_types(self) -> Self:
        if self.op is DataflowExpressionKind.LITERAL:
            if self.field_path or self.field_type is not None or self.args:
                raise ValueError("literal expressions accept only literal")
            _literal_type(self.literal)
            return self

        if self.op is DataflowExpressionKind.FIELD:
            if (
                not self.field_path
                or self.field_type is None
                or self.args
                or self.literal is not None
            ):
                raise ValueError(
                    "field expressions require field_path and field_type only"
                )
            return self

        if self.field_path or self.field_type is not None or self.literal is not None:
            raise ValueError("operator expressions accept only args")

        argument_types = tuple(argument.inferred_type() for argument in self.args)
        unary = {
            DataflowExpressionKind.NOT,
            DataflowExpressionKind.LOWER,
            DataflowExpressionKind.UPPER,
            DataflowExpressionKind.LENGTH,
        }
        binary = set(DataflowExpressionKind) - {
            DataflowExpressionKind.LITERAL,
            DataflowExpressionKind.FIELD,
            *unary,
        }
        expected_count = 1 if self.op in unary else 2 if self.op in binary else 0
        if len(self.args) != expected_count:
            raise ValueError(f"{self.op.value} requires {expected_count} arguments")

        boolean_ops = {DataflowExpressionKind.AND, DataflowExpressionKind.OR}
        if self.op in boolean_ops and any(
            value_type is not DataflowValueType.BOOLEAN for value_type in argument_types
        ):
            raise ValueError("boolean operators require boolean arguments")
        if self.op is DataflowExpressionKind.NOT and argument_types != (
            DataflowValueType.BOOLEAN,
        ):
            raise ValueError("not requires one boolean argument")

        numeric_ops = {
            DataflowExpressionKind.ADD,
            DataflowExpressionKind.SUBTRACT,
            DataflowExpressionKind.MULTIPLY,
            DataflowExpressionKind.DIVIDE,
        }
        numeric_types = {DataflowValueType.INTEGER, DataflowValueType.NUMBER}
        if self.op in numeric_ops and any(
            value_type not in numeric_types for value_type in argument_types
        ):
            raise ValueError("arithmetic operators require numeric arguments")

        ordered_ops = {
            DataflowExpressionKind.LESS_THAN,
            DataflowExpressionKind.LESS_THAN_OR_EQUAL,
            DataflowExpressionKind.GREATER_THAN,
            DataflowExpressionKind.GREATER_THAN_OR_EQUAL,
        }
        if self.op in ordered_ops:
            both_numeric = all(
                value_type in numeric_types for value_type in argument_types
            )
            if not both_numeric and argument_types != (
                DataflowValueType.STRING,
                DataflowValueType.STRING,
            ):
                raise ValueError(
                    "ordered comparisons require numeric values or two strings"
                )

        equality_ops = {
            DataflowExpressionKind.EQUAL,
            DataflowExpressionKind.NOT_EQUAL,
        }
        if (
            self.op in equality_ops
            and argument_types[0] != argument_types[1]
            and not all(value_type in numeric_types for value_type in argument_types)
        ):
            raise ValueError("equality operands must have compatible types")

        string_binary = {
            DataflowExpressionKind.CONTAINS,
            DataflowExpressionKind.STARTS_WITH,
            DataflowExpressionKind.ENDS_WITH,
        }
        if self.op in string_binary and argument_types != (
            DataflowValueType.STRING,
            DataflowValueType.STRING,
        ):
            raise ValueError("string predicates require two strings")
        if self.op in {
            DataflowExpressionKind.LOWER,
            DataflowExpressionKind.UPPER,
        } and argument_types != (DataflowValueType.STRING,):
            raise ValueError("string transforms require one string")
        if self.op is DataflowExpressionKind.LENGTH and argument_types[0] not in {
            DataflowValueType.STRING,
            DataflowValueType.ARRAY,
            DataflowValueType.OBJECT,
        }:
            raise ValueError("length requires a string, array, or object")
        return self

    def inferred_type(self) -> DataflowValueType:
        """Return the statically proven result type."""

        if self.op is DataflowExpressionKind.LITERAL:
            return _literal_type(self.literal)
        if self.op is DataflowExpressionKind.FIELD:
            assert self.field_type is not None
            return self.field_type
        if self.op in {
            DataflowExpressionKind.EQUAL,
            DataflowExpressionKind.NOT_EQUAL,
            DataflowExpressionKind.LESS_THAN,
            DataflowExpressionKind.LESS_THAN_OR_EQUAL,
            DataflowExpressionKind.GREATER_THAN,
            DataflowExpressionKind.GREATER_THAN_OR_EQUAL,
            DataflowExpressionKind.AND,
            DataflowExpressionKind.OR,
            DataflowExpressionKind.NOT,
            DataflowExpressionKind.CONTAINS,
            DataflowExpressionKind.STARTS_WITH,
            DataflowExpressionKind.ENDS_WITH,
        }:
            return DataflowValueType.BOOLEAN
        if self.op in {DataflowExpressionKind.LOWER, DataflowExpressionKind.UPPER}:
            return DataflowValueType.STRING
        if self.op is DataflowExpressionKind.LENGTH:
            return DataflowValueType.INTEGER
        if self.op is DataflowExpressionKind.DIVIDE:
            return DataflowValueType.NUMBER
        argument_types = tuple(argument.inferred_type() for argument in self.args)
        if DataflowValueType.NUMBER in argument_types:
            return DataflowValueType.NUMBER
        return DataflowValueType.INTEGER


class DataflowInputBinding(RuntimeContract):
    """Trusted shape and cardinality for one run-scoped input binding."""

    name: str
    value_type: DataflowValueType
    max_items: int = Field(ge=1, le=1_000)

    _normalize_name = field_validator("name")(_require_identifier)


class DataflowNode(RuntimeContract):
    """One bounded node in the closed dataflow graph."""

    node_id: str
    op: DataflowNodeKind
    inputs: tuple[str, ...] = Field(min_length=1, max_length=16)
    expression: DataflowExpression
    capability_binding: str | None = None
    max_output_items: int = Field(ge=1, le=1_000)
    error_policy: DataflowErrorPolicy = DataflowErrorPolicy.FAIL_PLAN

    _normalize_node_id = field_validator("node_id")(_require_identifier)

    @field_validator("inputs", mode="before")
    @classmethod
    def _normalize_inputs(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, (str, bytes)):
            raise ValueError("inputs must be an array of identifiers")
        normalized = tuple(_require_identifier(item) for item in value)  # type: ignore[arg-type]
        if len(normalized) != len(set(normalized)):
            raise ValueError("node inputs must be unique")
        return normalized

    @field_validator("capability_binding")
    @classmethod
    def _normalize_capability_binding(cls, value: str | None) -> str | None:
        return None if value is None else _require_identifier(value)

    @model_validator(mode="after")
    def _operation_shape(self) -> Self:
        invocation_ops = {
            DataflowNodeKind.INVOKE,
            DataflowNodeKind.BATCH_INVOKE,
        }
        if self.op in invocation_ops:
            if self.capability_binding is None:
                raise ValueError("invocation nodes require a capability binding")
            if self.expression.inferred_type() is not DataflowValueType.OBJECT:
                raise ValueError("invocation arguments must resolve to an object")
        elif self.capability_binding is not None:
            raise ValueError("only invocation nodes may use capability bindings")

        expression_type = self.expression.inferred_type()
        if self.op in {DataflowNodeKind.FILTER, DataflowNodeKind.BRANCH}:
            if expression_type is not DataflowValueType.BOOLEAN:
                raise ValueError("filter and branch expressions must be boolean")
        if self.op is DataflowNodeKind.LIMIT:
            if expression_type is not DataflowValueType.INTEGER:
                raise ValueError("limit expressions must be integers")
            if self.expression.op is DataflowExpressionKind.LITERAL and (
                not isinstance(self.expression.literal, int)
                or isinstance(self.expression.literal, bool)
                or self.expression.literal < 0
            ):
                raise ValueError("literal limits must be non-negative integers")
        if self.op in {DataflowNodeKind.SORT, DataflowNodeKind.GROUP}:
            if expression_type not in {
                DataflowValueType.BOOLEAN,
                DataflowValueType.INTEGER,
                DataflowValueType.NUMBER,
                DataflowValueType.STRING,
            }:
                raise ValueError("sort and group keys must be scalar")
        return self


class DataflowLimits(RuntimeContract):
    """Model-visible ceilings, themselves capped by immutable product maxima."""

    max_nodes: int = Field(default=100, ge=1, le=100)
    max_edges: int = Field(default=400, ge=1, le=400)
    max_expression_nodes: int = Field(default=1_000, ge=1, le=2_000)
    max_expression_depth: int = Field(default=16, ge=1, le=32)
    max_input_items: int = Field(default=1_000, ge=1, le=1_000)
    max_iterations: int = Field(default=100_000, ge=1, le=100_000)
    max_inner_calls: int = Field(default=50, ge=0, le=50)
    max_parallelism: int = Field(default=8, ge=1, le=8)
    max_result_bytes: int = Field(default=32 * 1024, ge=1, le=256 * 1024)
    max_cpu_ms: int = Field(default=10_000, ge=1, le=30_000)
    max_wall_ms: int = Field(default=300_000, ge=1, le=300_000)


class DataflowPlan(RuntimeContract):
    """Versioned, source-free plan assembled with trusted run bindings."""

    plan_id: str
    language_version: Literal["dataflow.v1"] = "dataflow.v1"
    input_bindings: tuple[DataflowInputBinding, ...] = Field(
        min_length=1,
        max_length=16,
    )
    capability_bindings: tuple[str, ...] = Field(default_factory=tuple, max_length=50)
    nodes: tuple[DataflowNode, ...] = Field(min_length=1, max_length=100)
    output_node_id: str
    output_type: DataflowValueType
    limits: DataflowLimits = Field(default_factory=DataflowLimits)

    _normalize_plan_id = field_validator("plan_id")(_require_identifier)
    _normalize_output_node_id = field_validator("output_node_id")(_require_identifier)

    @field_validator("capability_bindings", mode="before")
    @classmethod
    def _normalize_capability_bindings(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, (str, bytes)):
            raise ValueError("capability_bindings must be an array of identifiers")
        normalized = tuple(_require_identifier(item) for item in value)  # type: ignore[arg-type]
        if len(normalized) != len(set(normalized)):
            raise ValueError("capability_bindings must be unique")
        return normalized

    @model_validator(mode="after")
    def _unique_identifiers(self) -> Self:
        input_names = tuple(binding.name for binding in self.input_bindings)
        node_ids = tuple(node.node_id for node in self.nodes)
        if len(input_names) != len(set(input_names)):
            raise ValueError("input binding names must be unique")
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("node identifiers must be unique")
        if set(input_names).intersection(node_ids):
            raise ValueError(
                "input binding names and node identifiers must be disjoint"
            )
        return self


class ResolvedDataflowCapability(RuntimeContract):
    """Trusted descriptor facts for one authorized opaque binding."""

    binding_id: str
    capability_ref: str = Field(pattern=r"^cap_[0-9a-f]{32}$")
    descriptor_revision: str = Field(min_length=1, max_length=256)
    effect_class: EffectClass
    output_type: DataflowValueType
    max_calls: int = Field(ge=0, le=50)
    concurrency_policy: ConcurrencyPolicy = Field(default_factory=ConcurrencyPolicy)

    _normalize_binding_id = field_validator("binding_id")(_require_identifier)


class DataflowValidationPolicy(RuntimeContract):
    """Trusted installation/run ceilings that may only narrow plan limits."""

    limits: DataflowLimits = Field(default_factory=DataflowLimits)
    allowed_effect_classes: tuple[EffectClass, ...] = (EffectClass.NONE,)
    allowed_node_kinds: tuple[DataflowNodeKind, ...] = tuple(DataflowNodeKind)

    @model_validator(mode="after")
    def _effect_classes_are_read_only(self) -> Self:
        if any(
            effect_class is not EffectClass.NONE
            for effect_class in self.allowed_effect_classes
        ):
            raise ValueError(
                "v1 validation policy may authorize only no-effect capabilities"
            )
        if not self.allowed_node_kinds:
            raise ValueError("validation policy must allow at least one node kind")
        return self


class ValidatedDataflowPlan(RuntimeContract):
    """Content-free deterministic validation result."""

    plan_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    topological_order: tuple[str, ...]
    node_count: int = Field(ge=1, le=100)
    edge_count: int = Field(ge=0, le=400)
    expression_node_count: int = Field(ge=1, le=2_000)
    maximum_expression_depth: int = Field(ge=1, le=32)
    maximum_input_items: int = Field(ge=1, le=1_000)
    estimated_iterations: int = Field(ge=0, le=100_000)
    estimated_inner_calls: int = Field(ge=0, le=50)


__all__ = (
    "DataflowErrorPolicy",
    "DataflowExpression",
    "DataflowExpressionKind",
    "DataflowInputBinding",
    "DataflowLimits",
    "DataflowNode",
    "DataflowNodeKind",
    "DataflowPlan",
    "DataflowValidationPolicy",
    "DataflowValueType",
    "ResolvedDataflowCapability",
    "ValidatedDataflowPlan",
)
