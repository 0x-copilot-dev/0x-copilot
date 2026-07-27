"""Deterministic fail-closed validation for governed dataflow plans."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
import hashlib
import heapq
import json

from agent_runtime.capabilities.concurrency.contracts import (
    ConcurrencyMode,
    PolicySource,
    SideEffectKind,
)
from agent_runtime.capabilities.dataflow.contracts import (
    DataflowExpression,
    DataflowNode,
    DataflowNodeKind,
    DataflowPlan,
    DataflowValidationPolicy,
    DataflowValueType,
    ResolvedDataflowCapability,
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


class DataflowPlanValidator:
    """Validate a closed plan without performing I/O or resolving authority."""

    def validate(
        self,
        plan: DataflowPlan,
        *,
        capabilities: tuple[ResolvedDataflowCapability, ...] = (),
        policy: DataflowValidationPolicy | None = None,
    ) -> ValidatedDataflowPlan:
        """Return deterministic facts and a digest, or reject the plan."""

        effective_policy = policy or DataflowValidationPolicy()
        self._validate_policy_limits(plan, effective_policy)
        self._validate_allowed_nodes(plan, effective_policy)

        nodes = {node.node_id: node for node in plan.nodes}
        inputs = {binding.name: binding for binding in plan.input_bindings}
        resolved = self._resolved_capabilities(capabilities)
        self._validate_capability_declarations(plan, resolved)
        edge_count, topological_order = self._validate_graph(plan, nodes, inputs)
        expression_count, expression_depth = self._validate_expressions(plan)
        max_input_items, iterations, inner_calls, output_types = (
            self._validate_static_bounds_and_types(
                plan,
                nodes=nodes,
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
        digest = self._digest(plan, resolved)
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
        if sum(binding.max_items for binding in plan.input_bindings) > (
            plan.limits.max_input_items
        ):
            raise DataflowValidationError(
                DataflowValidationErrorCode.POLICY_LIMIT_EXCEEDED,
                "input cardinality exceeds the plan limit",
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
        resolved: Mapping[str, ResolvedDataflowCapability],
        policy: DataflowValidationPolicy,
        topological_order: tuple[str, ...],
    ) -> tuple[int, int, int, dict[str, DataflowValueType]]:
        item_bounds = {
            binding.name: binding.max_items for binding in plan.input_bindings
        }
        output_types = {
            binding.name: binding.value_type for binding in plan.input_bindings
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
            output_types[node_id] = cls._node_output_type(
                node,
                input_types=tuple(output_types[item] for item in node.inputs),
                resolved=resolved,
            )
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
            max(binding.max_items for binding in plan.input_bindings),
            iterations,
            inner_calls,
            output_types,
        )

    @staticmethod
    def _node_output_type(
        node: DataflowNode,
        *,
        input_types: tuple[DataflowValueType, ...],
        resolved: Mapping[str, ResolvedDataflowCapability],
    ) -> DataflowValueType:
        expression_type = node.expression.inferred_type()
        if node.op in {
            DataflowNodeKind.FILTER,
            DataflowNodeKind.SORT,
            DataflowNodeKind.LIMIT,
            DataflowNodeKind.BRANCH,
        }:
            return input_types[0]
        if node.op is DataflowNodeKind.GROUP:
            return DataflowValueType.OBJECT
        if node.op is DataflowNodeKind.INVOKE:
            assert node.capability_binding is not None
            return resolved[node.capability_binding].output_type
        if node.op is DataflowNodeKind.BATCH_INVOKE:
            return DataflowValueType.ARRAY
        return expression_type

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
        resolved: Mapping[str, ResolvedDataflowCapability],
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
        try:
            canonical = json.dumps(
                {
                    "plan": plan_payload,
                    "resolved_capabilities": capability_payload,
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
