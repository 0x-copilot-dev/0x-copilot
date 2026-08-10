"""Deterministic fail-closed validation for governed dataflow plans."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import heapq
import json

from agent_runtime.capabilities.dataflow.capability_policy import (
    ConcurrencyMode,
    PolicySource,
    SideEffectKind,
)
from agent_runtime.capabilities.dataflow.contracts import (
    DataflowEvaluatorSemantics,
    DataflowExpression,
    DataflowExpressionKind,
    DataflowNode,
    DataflowNodeKind,
    DataflowPlan,
    DataflowValidationPolicy,
    DataflowValueType,
    ResolvedDataflowCapability,
    ResolvedDataflowInput,
    ValidatedDataflowPlan,
)
from agent_runtime.surfaces_v2.ledger_models import EffectClass


class DataflowValidationErrorCode(StrEnum):
    """Stable, content-free rejection classes."""

    POLICY_LIMIT_EXCEEDED = "policy_limit_exceeded"
    NODE_KIND_DENIED = "node_kind_denied"
    INVALID_REFERENCE = "invalid_reference"
    CYCLIC_GRAPH = "cyclic_graph"
    UNREACHABLE_NODE = "unreachable_node"
    UNUSED_BINDING = "unused_binding"
    EXPRESSION_LIMIT_EXCEEDED = "expression_limit_exceeded"
    ITERATION_LIMIT_EXCEEDED = "iteration_limit_exceeded"
    INNER_CALL_LIMIT_EXCEEDED = "inner_call_limit_exceeded"
    CAPABILITY_UNKNOWN = "capability_unknown"
    INPUT_BINDING_UNKNOWN = "input_binding_unknown"
    INPUT_FIELD_UNKNOWN = "input_field_unknown"
    EXPRESSION_TYPE_MISMATCH = "expression_type_mismatch"
    EVALUATOR_SEMANTICS_MISMATCH = "evaluator_semantics_mismatch"
    CAPABILITY_EFFECT_DENIED = "capability_effect_denied"
    CAPABILITY_CALL_LIMIT_EXCEEDED = "capability_call_limit_exceeded"
    BATCH_POLICY_DENIED = "batch_policy_denied"
    OUTPUT_TYPE_MISMATCH = "output_type_mismatch"
    NON_CANONICAL_VALUE = "non_canonical_value"


class DataflowValidationError(ValueError):
    """Typed safe validation error without plan content."""

    def __init__(
        self,
        code: DataflowValidationErrorCode,
        safe_message: str,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


@dataclass(frozen=True)
class _ValueShape:
    """Runtime-owned structural type used only during static validation."""

    value_type: DataflowValueType
    fields: Mapping[tuple[str, ...], DataflowValueType]


class DataflowPlanValidator:
    """Validate a closed plan without performing I/O or resolving authority."""

    def validate(
        self,
        plan: DataflowPlan,
        *,
        inputs: tuple[ResolvedDataflowInput, ...],
        evaluator_semantics: DataflowEvaluatorSemantics,
        capabilities: tuple[ResolvedDataflowCapability, ...] = (),
        policy: DataflowValidationPolicy | None = None,
    ) -> ValidatedDataflowPlan:
        """Return deterministic facts and a digest, or reject the plan."""

        effective_policy = policy or DataflowValidationPolicy()
        resolved_inputs = self._resolved_inputs(inputs)
        self._validate_input_declarations(plan, resolved_inputs)
        self._validate_evaluator_semantics(plan, evaluator_semantics)
        self._validate_policy_limits(plan, resolved_inputs, effective_policy)
        self._validate_allowed_nodes(plan, effective_policy)

        nodes = {node.node_id: node for node in plan.nodes}
        input_declarations = {binding.name: binding for binding in plan.input_bindings}
        resolved = self._resolved_capabilities(capabilities)
        self._validate_capability_declarations(plan, resolved)
        edge_count, topological_order = self._validate_graph(
            plan,
            nodes,
            input_declarations,
        )
        expression_count, expression_depth = self._validate_expressions(plan)
        max_input_items, iterations, inner_calls, output_types = (
            self._validate_static_bounds_and_types(
                plan,
                nodes=nodes,
                resolved_inputs=resolved_inputs,
                resolved=resolved,
                policy=effective_policy,
                topological_order=topological_order,
            )
        )
        if output_types[plan.output_node_id] is not plan.output_type:
            raise DataflowValidationError(
                DataflowValidationErrorCode.OUTPUT_TYPE_MISMATCH,
                "declared output type does not match the statically proven type",
            )
        digest = self._digest(
            plan,
            resolved_inputs,
            resolved,
            evaluator_semantics,
        )
        return ValidatedDataflowPlan(
            plan_digest=digest,
            topological_order=topological_order,
            node_count=len(nodes),
            edge_count=edge_count,
            expression_node_count=expression_count,
            maximum_expression_depth=expression_depth,
            maximum_input_items=max_input_items,
            estimated_iterations=iterations,
            estimated_inner_calls=inner_calls,
        )

    @staticmethod
    def _validate_policy_limits(
        plan: DataflowPlan,
        resolved_inputs: Mapping[str, ResolvedDataflowInput],
        policy: DataflowValidationPolicy,
    ) -> None:
        plan_limits = plan.limits.model_dump(mode="python")
        policy_limits = policy.limits.model_dump(mode="python")
        if any(
            plan_limits[field_name] > policy_limits[field_name]
            for field_name in plan_limits
        ):
            raise DataflowValidationError(
                DataflowValidationErrorCode.POLICY_LIMIT_EXCEEDED,
                "plan limits exceed the trusted validation policy",
            )
        if len(plan.nodes) > plan.limits.max_nodes:
            raise DataflowValidationError(
                DataflowValidationErrorCode.POLICY_LIMIT_EXCEEDED,
                "plan node count exceeds its declared limit",
            )
        if sum(binding.max_items for binding in resolved_inputs.values()) > (
            plan.limits.max_input_items
        ):
            raise DataflowValidationError(
                DataflowValidationErrorCode.POLICY_LIMIT_EXCEEDED,
                "input cardinality exceeds the plan limit",
            )

    @staticmethod
    def _resolved_inputs(
        inputs: tuple[ResolvedDataflowInput, ...],
    ) -> dict[str, ResolvedDataflowInput]:
        resolved = {input_binding.binding_id: input_binding for input_binding in inputs}
        if len(resolved) != len(inputs):
            raise DataflowValidationError(
                DataflowValidationErrorCode.INPUT_BINDING_UNKNOWN,
                "resolved input binding identifiers must be unique",
            )
        return resolved

    @staticmethod
    def _validate_input_declarations(
        plan: DataflowPlan,
        resolved_inputs: Mapping[str, ResolvedDataflowInput],
    ) -> None:
        if {binding.name for binding in plan.input_bindings} != set(resolved_inputs):
            raise DataflowValidationError(
                DataflowValidationErrorCode.INPUT_BINDING_UNKNOWN,
                "plan and runtime input bindings do not match exactly",
            )

    @staticmethod
    def _validate_evaluator_semantics(
        plan: DataflowPlan,
        evaluator_semantics: DataflowEvaluatorSemantics,
    ) -> None:
        if evaluator_semantics.language_version != plan.language_version:
            raise DataflowValidationError(
                DataflowValidationErrorCode.EVALUATOR_SEMANTICS_MISMATCH,
                "plan language and trusted evaluator semantics do not match",
            )

    @staticmethod
    def _validate_allowed_nodes(
        plan: DataflowPlan,
        policy: DataflowValidationPolicy,
    ) -> None:
        allowed = frozenset(policy.allowed_node_kinds)
        if any(node.op not in allowed for node in plan.nodes):
            raise DataflowValidationError(
                DataflowValidationErrorCode.NODE_KIND_DENIED,
                "plan contains a node kind disabled by policy",
            )

    @staticmethod
    def _resolved_capabilities(
        capabilities: tuple[ResolvedDataflowCapability, ...],
    ) -> dict[str, ResolvedDataflowCapability]:
        resolved = {capability.binding_id: capability for capability in capabilities}
        if len(resolved) != len(capabilities):
            raise DataflowValidationError(
                DataflowValidationErrorCode.CAPABILITY_UNKNOWN,
                "resolved capability binding identifiers must be unique",
            )
        return resolved

    @staticmethod
    def _validate_capability_declarations(
        plan: DataflowPlan,
        resolved: Mapping[str, ResolvedDataflowCapability],
    ) -> None:
        declared = set(plan.capability_bindings)
        if declared != set(resolved):
            raise DataflowValidationError(
                DataflowValidationErrorCode.CAPABILITY_UNKNOWN,
                "plan and runtime capability bindings do not match exactly",
            )
        used = {
            node.capability_binding
            for node in plan.nodes
            if node.capability_binding is not None
        }
        if used != declared:
            raise DataflowValidationError(
                DataflowValidationErrorCode.UNUSED_BINDING,
                "every declared capability binding must be used",
            )

    @staticmethod
    def _validate_graph(
        plan: DataflowPlan,
        nodes: Mapping[str, DataflowNode],
        inputs: Mapping[str, object],
    ) -> tuple[int, tuple[str, ...]]:
        if plan.output_node_id not in nodes:
            raise DataflowValidationError(
                DataflowValidationErrorCode.INVALID_REFERENCE,
                "output_node_id must reference a plan node",
            )
        all_references = set(nodes).union(inputs)
        adjacency: dict[str, list[str]] = {node_id: [] for node_id in nodes}
        indegree = {node_id: 0 for node_id in nodes}
        edge_count = 0
        for node in nodes.values():
            for input_id in node.inputs:
                if input_id not in all_references:
                    raise DataflowValidationError(
                        DataflowValidationErrorCode.INVALID_REFERENCE,
                        "a node input references an unknown binding",
                    )
                edge_count += 1
                if input_id in nodes:
                    adjacency[input_id].append(node.node_id)
                    indegree[node.node_id] += 1
        if edge_count > plan.limits.max_edges:
            raise DataflowValidationError(
                DataflowValidationErrorCode.POLICY_LIMIT_EXCEEDED,
                "plan edge count exceeds its declared limit",
            )

        ready = [node_id for node_id, degree in indegree.items() if degree == 0]
        heapq.heapify(ready)
        ordered: list[str] = []
        while ready:
            node_id = heapq.heappop(ready)
            ordered.append(node_id)
            for dependent in sorted(adjacency[node_id]):
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    heapq.heappush(ready, dependent)
        if len(ordered) != len(nodes):
            raise DataflowValidationError(
                DataflowValidationErrorCode.CYCLIC_GRAPH,
                "plan graph must be acyclic",
            )

        reachable_nodes: set[str] = set()
        reachable_inputs: set[str] = set()
        pending = [plan.output_node_id]
        while pending:
            node_id = pending.pop()
            if node_id in reachable_nodes:
                continue
            reachable_nodes.add(node_id)
            for dependency in nodes[node_id].inputs:
                if dependency in nodes:
                    pending.append(dependency)
                else:
                    reachable_inputs.add(dependency)
        if reachable_nodes != set(nodes):
            raise DataflowValidationError(
                DataflowValidationErrorCode.UNREACHABLE_NODE,
                "every node must contribute to the declared output",
            )
        if reachable_inputs != set(inputs):
            raise DataflowValidationError(
                DataflowValidationErrorCode.UNUSED_BINDING,
                "every input binding must contribute to the declared output",
            )
        return edge_count, tuple(ordered)

    @staticmethod
    def _expression_size(expression: DataflowExpression) -> tuple[int, int]:
        if not expression.args:
            return 1, 1
        children = tuple(
            DataflowPlanValidator._expression_size(argument)
            for argument in expression.args
        )
        return (
            1 + sum(size for size, _depth in children),
            1 + max(depth for _size, depth in children),
        )

    @classmethod
    def _validate_expressions(cls, plan: DataflowPlan) -> tuple[int, int]:
        sizes = tuple(cls._expression_size(node.expression) for node in plan.nodes)
        expression_count = sum(size for size, _depth in sizes)
        expression_depth = max(depth for _size, depth in sizes)
        if (
            expression_count > plan.limits.max_expression_nodes
            or expression_depth > plan.limits.max_expression_depth
        ):
            raise DataflowValidationError(
                DataflowValidationErrorCode.EXPRESSION_LIMIT_EXCEEDED,
                "plan expressions exceed their declared structural limits",
            )
        return expression_count, expression_depth

    @classmethod
    def _validate_static_bounds_and_types(
        cls,
        plan: DataflowPlan,
        *,
        nodes: Mapping[str, DataflowNode],
        resolved_inputs: Mapping[str, ResolvedDataflowInput],
        resolved: Mapping[str, ResolvedDataflowCapability],
        policy: DataflowValidationPolicy,
        topological_order: tuple[str, ...],
    ) -> tuple[int, int, int, dict[str, DataflowValueType]]:
        item_bounds = {
            binding_id: binding.max_items
            for binding_id, binding in resolved_inputs.items()
        }
        output_shapes: dict[str, _ValueShape] = {
            binding_id: _ValueShape(
                value_type=binding.value_type,
                fields={field.path: field.value_type for field in binding.fields},
            )
            for binding_id, binding in resolved_inputs.items()
        }
        output_types = {
            binding_id: shape.value_type for binding_id, shape in output_shapes.items()
        }
        iterations = 0
        inner_calls = 0
        calls_by_binding: dict[str, int] = {
            binding_id: 0 for binding_id in plan.capability_bindings
        }

        for node_id in topological_order:
            node = nodes[node_id]
            input_bound = sum(item_bounds[input_id] for input_id in node.inputs)
            iterations += input_bound
            item_bounds[node_id] = min(node.max_output_items, input_bound)
            expression_type = cls._infer_expression_type(
                node.expression,
                input_shapes={
                    input_id: output_shapes[input_id] for input_id in node.inputs
                },
            )
            output_shapes[node_id] = cls._node_output_shape(
                node,
                expression_type=expression_type,
                input_shapes=tuple(output_shapes[item] for item in node.inputs),
                resolved=resolved,
            )
            output_types[node_id] = output_shapes[node_id].value_type
            if node.op in {
                DataflowNodeKind.INVOKE,
                DataflowNodeKind.BATCH_INVOKE,
            }:
                assert node.capability_binding is not None
                capability = resolved[node.capability_binding]
                cls._validate_invocation_policy(plan, node, capability, policy)
                inner_calls += input_bound
                calls_by_binding[node.capability_binding] += input_bound

        if iterations > plan.limits.max_iterations:
            raise DataflowValidationError(
                DataflowValidationErrorCode.ITERATION_LIMIT_EXCEEDED,
                "statically estimated iterations exceed the plan limit",
            )
        if inner_calls > plan.limits.max_inner_calls:
            raise DataflowValidationError(
                DataflowValidationErrorCode.INNER_CALL_LIMIT_EXCEEDED,
                "statically estimated inner calls exceed the plan limit",
            )
        if any(
            calls_by_binding[binding_id] > capability.max_calls
            for binding_id, capability in resolved.items()
        ):
            raise DataflowValidationError(
                DataflowValidationErrorCode.CAPABILITY_CALL_LIMIT_EXCEEDED,
                "a capability binding call bound exceeds its trusted limit",
            )
        return (
            max(binding.max_items for binding in resolved_inputs.values()),
            iterations,
            inner_calls,
            output_types,
        )

    @classmethod
    def _infer_expression_type(
        cls,
        expression: DataflowExpression,
        *,
        input_shapes: Mapping[str, _ValueShape],
    ) -> DataflowValueType:
        if expression.op is DataflowExpressionKind.LITERAL:
            return expression.literal_type()
        if expression.op is DataflowExpressionKind.FIELD:
            return cls._resolve_field_type(expression.field_path, input_shapes)

        argument_types = tuple(
            cls._infer_expression_type(argument, input_shapes=input_shapes)
            for argument in expression.args
        )
        numeric_types = {DataflowValueType.INTEGER, DataflowValueType.NUMBER}
        boolean_ops = {DataflowExpressionKind.AND, DataflowExpressionKind.OR}
        if expression.op in boolean_ops and any(
            value_type is not DataflowValueType.BOOLEAN for value_type in argument_types
        ):
            cls._expression_type_error("boolean operators require boolean arguments")
        if expression.op is DataflowExpressionKind.NOT and argument_types != (
            DataflowValueType.BOOLEAN,
        ):
            cls._expression_type_error("not requires one boolean argument")

        numeric_ops = {
            DataflowExpressionKind.ADD,
            DataflowExpressionKind.SUBTRACT,
            DataflowExpressionKind.MULTIPLY,
            DataflowExpressionKind.DIVIDE,
        }
        if expression.op in numeric_ops and any(
            value_type not in numeric_types for value_type in argument_types
        ):
            cls._expression_type_error("arithmetic operators require numeric arguments")

        ordered_ops = {
            DataflowExpressionKind.LESS_THAN,
            DataflowExpressionKind.LESS_THAN_OR_EQUAL,
            DataflowExpressionKind.GREATER_THAN,
            DataflowExpressionKind.GREATER_THAN_OR_EQUAL,
        }
        if expression.op in ordered_ops:
            both_numeric = all(
                value_type in numeric_types for value_type in argument_types
            )
            if not both_numeric and argument_types != (
                DataflowValueType.STRING,
                DataflowValueType.STRING,
            ):
                cls._expression_type_error(
                    "ordered comparisons require numeric values or two strings"
                )

        equality_ops = {
            DataflowExpressionKind.EQUAL,
            DataflowExpressionKind.NOT_EQUAL,
        }
        if (
            expression.op in equality_ops
            and argument_types[0] != argument_types[1]
            and not all(value_type in numeric_types for value_type in argument_types)
        ):
            cls._expression_type_error("equality operands must have compatible types")

        string_binary = {
            DataflowExpressionKind.CONTAINS,
            DataflowExpressionKind.STARTS_WITH,
            DataflowExpressionKind.ENDS_WITH,
        }
        if expression.op in string_binary and argument_types != (
            DataflowValueType.STRING,
            DataflowValueType.STRING,
        ):
            cls._expression_type_error("string predicates require two strings")
        if expression.op in {
            DataflowExpressionKind.LOWER,
            DataflowExpressionKind.UPPER,
        } and argument_types != (DataflowValueType.STRING,):
            cls._expression_type_error("string transforms require one string")
        if expression.op is DataflowExpressionKind.LENGTH and argument_types[0] not in {
            DataflowValueType.STRING,
            DataflowValueType.ARRAY,
            DataflowValueType.OBJECT,
        }:
            cls._expression_type_error("length requires a string, array, or object")

        if expression.op in {
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
        if expression.op in {
            DataflowExpressionKind.LOWER,
            DataflowExpressionKind.UPPER,
        }:
            return DataflowValueType.STRING
        if expression.op is DataflowExpressionKind.LENGTH:
            return DataflowValueType.INTEGER
        if expression.op is DataflowExpressionKind.DIVIDE:
            return DataflowValueType.NUMBER
        if DataflowValueType.NUMBER in argument_types:
            return DataflowValueType.NUMBER
        return DataflowValueType.INTEGER

    @staticmethod
    def _resolve_field_type(
        field_path: tuple[str, ...],
        input_shapes: Mapping[str, _ValueShape],
    ) -> DataflowValueType:
        if len(input_shapes) == 1:
            shape = next(iter(input_shapes.values()))
            relative_path = field_path
        else:
            source_id = field_path[0]
            shape = input_shapes.get(source_id)
            relative_path = field_path[1:]
        if shape is None:
            field_type = None
        elif (
            relative_path == ("value",)
            and shape.value_type is not DataflowValueType.OBJECT
        ):
            field_type = shape.value_type
        else:
            field_type = shape.fields.get(relative_path)
        if field_type is None:
            raise DataflowValidationError(
                DataflowValidationErrorCode.INPUT_FIELD_UNKNOWN,
                "field expression is absent from the trusted input schema",
            )
        return field_type

    @staticmethod
    def _expression_type_error(message: str) -> None:
        raise DataflowValidationError(
            DataflowValidationErrorCode.EXPRESSION_TYPE_MISMATCH,
            message,
        )

    @classmethod
    def _node_output_shape(
        cls,
        node: DataflowNode,
        *,
        expression_type: DataflowValueType,
        input_shapes: tuple[_ValueShape, ...],
        resolved: Mapping[str, ResolvedDataflowCapability],
    ) -> _ValueShape:
        if (
            node.op in {DataflowNodeKind.INVOKE, DataflowNodeKind.BATCH_INVOKE}
            and expression_type is not DataflowValueType.OBJECT
        ):
            cls._expression_type_error("invocation arguments must resolve to an object")
        if (
            node.op in {DataflowNodeKind.FILTER, DataflowNodeKind.BRANCH}
            and expression_type is not DataflowValueType.BOOLEAN
        ):
            cls._expression_type_error("filter and branch expressions must be boolean")
        if node.op is DataflowNodeKind.LIMIT:
            if expression_type is not DataflowValueType.INTEGER:
                cls._expression_type_error("limit expressions must be integers")
            if node.expression.op is DataflowExpressionKind.LITERAL and (
                not isinstance(node.expression.literal, int)
                or isinstance(node.expression.literal, bool)
                or node.expression.literal < 0
            ):
                cls._expression_type_error(
                    "literal limits must be non-negative integers"
                )
        if node.op in {
            DataflowNodeKind.SORT,
            DataflowNodeKind.GROUP,
        } and expression_type not in {
            DataflowValueType.BOOLEAN,
            DataflowValueType.INTEGER,
            DataflowValueType.NUMBER,
            DataflowValueType.STRING,
        }:
            cls._expression_type_error("sort and group keys must be scalar")

        if node.op in {
            DataflowNodeKind.FILTER,
            DataflowNodeKind.SORT,
            DataflowNodeKind.LIMIT,
            DataflowNodeKind.BRANCH,
        }:
            return input_shapes[0]
        if node.op is DataflowNodeKind.GROUP:
            return _ValueShape(DataflowValueType.OBJECT, {})
        if node.op is DataflowNodeKind.INVOKE:
            assert node.capability_binding is not None
            return _ValueShape(
                resolved[node.capability_binding].output_type,
                {},
            )
        if node.op is DataflowNodeKind.BATCH_INVOKE:
            return _ValueShape(DataflowValueType.ARRAY, {})
        return _ValueShape(expression_type, {})

    @staticmethod
    def _validate_invocation_policy(
        plan: DataflowPlan,
        node: DataflowNode,
        capability: ResolvedDataflowCapability,
        policy: DataflowValidationPolicy,
    ) -> None:
        if (
            capability.effect_class is not EffectClass.NONE
            or capability.effect_class not in policy.allowed_effect_classes
        ):
            raise DataflowValidationError(
                DataflowValidationErrorCode.CAPABILITY_EFFECT_DENIED,
                "v1 dataflow invocation permits only no-effect capabilities",
            )
        if node.op is not DataflowNodeKind.BATCH_INVOKE:
            return
        concurrency = capability.concurrency_policy
        if (
            concurrency.policy_source is PolicySource.CONSERVATIVE_DEFAULT
            or concurrency.mode is ConcurrencyMode.SERIAL
            or concurrency.side_effect not in {SideEffectKind.NONE, SideEffectKind.READ}
            or concurrency.max_parallelism is None
            or concurrency.max_parallelism > plan.limits.max_parallelism
        ):
            raise DataflowValidationError(
                DataflowValidationErrorCode.BATCH_POLICY_DENIED,
                "batch invocation requires explicit trusted read concurrency metadata",
            )

    @staticmethod
    def _digest(
        plan: DataflowPlan,
        resolved_inputs: Mapping[str, ResolvedDataflowInput],
        resolved: Mapping[str, ResolvedDataflowCapability],
        evaluator_semantics: DataflowEvaluatorSemantics,
    ) -> str:
        plan_payload = plan.model_dump(mode="json")
        plan_payload["input_bindings"] = sorted(
            plan_payload["input_bindings"],
            key=lambda item: item["name"],
        )
        plan_payload["capability_bindings"] = sorted(
            plan_payload["capability_bindings"]
        )
        plan_payload["nodes"] = sorted(
            plan_payload["nodes"],
            key=lambda item: item["node_id"],
        )
        for node in plan_payload["nodes"]:
            node["inputs"] = sorted(node["inputs"])
        capability_payload = [
            resolved[binding_id].model_dump(mode="json")
            for binding_id in sorted(resolved)
        ]
        input_payload = [
            resolved_inputs[binding_id].model_dump(mode="json")
            for binding_id in sorted(resolved_inputs)
        ]
        for input_binding in input_payload:
            input_binding["fields"] = sorted(
                input_binding["fields"],
                key=lambda item: item["path"],
            )
        try:
            canonical = json.dumps(
                {
                    "plan": plan_payload,
                    "resolved_inputs": input_payload,
                    "resolved_capabilities": capability_payload,
                    "evaluator_semantics": evaluator_semantics.model_dump(mode="json"),
                },
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise DataflowValidationError(
                DataflowValidationErrorCode.NON_CANONICAL_VALUE,
                "plan contains a value that cannot be canonically encoded",
            ) from exc
        return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


__all__ = (
    "DataflowPlanValidator",
    "DataflowValidationError",
    "DataflowValidationErrorCode",
)
