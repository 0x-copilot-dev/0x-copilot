from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime.capabilities.concurrency.contracts import (
    ConcurrencyMode,
    ConcurrencyPolicy,
    PolicySource,
    SideEffectKind,
)
from agent_runtime.capabilities.dataflow import (
    DataflowEvaluatorSemantics,
    DataflowExpression,
    DataflowExpressionKind,
    DataflowFieldDescriptor,
    DataflowInputBinding,
    DataflowLimits,
    DataflowNode,
    DataflowNodeKind,
    DataflowPlan,
    DataflowPlanValidator,
    DataflowValidationError,
    DataflowValidationErrorCode,
    DataflowValidationPolicy,
    DataflowValueType,
    ResolvedDataflowCapability,
    ResolvedDataflowInput,
)
from agent_runtime.surfaces_v2.ledger_models import EffectClass


def _literal(value: object) -> DataflowExpression:
    return DataflowExpression(op=DataflowExpressionKind.LITERAL, literal=value)


def _field(
    name: str,
) -> DataflowExpression:
    return DataflowExpression(
        op=DataflowExpressionKind.FIELD,
        field_path=(name,),
    )


_EVALUATOR_SEMANTICS = DataflowEvaluatorSemantics(
    evaluator_revision="evaluator-v1",
    expression_semantics_revision="expressions-v1",
    canonicalization_revision="canonical-json-v1",
)


def _trusted_inputs(plan: DataflowPlan) -> tuple[ResolvedDataflowInput, ...]:
    bindings: list[ResolvedDataflowInput] = []
    declarations = sorted(plan.input_bindings, key=lambda item: item.name)
    for index, declaration in enumerate(declarations, start=1):
        value_type = (
            DataflowValueType.OBJECT
            if declaration.name == "records"
            else DataflowValueType.STRING
        )
        max_items = (
            plan.nodes[0].max_output_items
            if declaration.name in {"rows", "records"}
            else 1
        )
        bindings.append(
            ResolvedDataflowInput(
                binding_id=declaration.name,
                input_ref=f"input_{index:032x}",
                source_revision="source-v1",
                schema_revision="schema-v1",
                value_type=value_type,
                max_items=max_items,
            )
        )
    return tuple(bindings)


def _validate(
    plan: DataflowPlan,
    *,
    inputs: tuple[ResolvedDataflowInput, ...] | None = None,
    evaluator_semantics: DataflowEvaluatorSemantics = _EVALUATOR_SEMANTICS,
    capabilities: tuple[ResolvedDataflowCapability, ...] = (),
    policy: DataflowValidationPolicy | None = None,
):
    return DataflowPlanValidator().validate(
        plan,
        inputs=_trusted_inputs(plan) if inputs is None else inputs,
        evaluator_semantics=evaluator_semantics,
        capabilities=capabilities,
        policy=policy,
    )


def _pure_plan(*, limits: DataflowLimits | None = None) -> DataflowPlan:
    return DataflowPlan(
        plan_id="pure-plan",
        input_bindings=(
            DataflowInputBinding(
                name="rows",
            ),
        ),
        nodes=(
            DataflowNode(
                node_id="normalize",
                op=DataflowNodeKind.MAP,
                inputs=("rows",),
                expression=DataflowExpression(
                    op=DataflowExpressionKind.LOWER,
                    args=(_field("value"),),
                ),
                max_output_items=3,
            ),
        ),
        output_node_id="normalize",
        output_type=DataflowValueType.STRING,
        limits=limits or DataflowLimits(),
    )


def _capability(
    *,
    effect_class: EffectClass = EffectClass.NONE,
    max_calls: int = 3,
    concurrency_policy: ConcurrencyPolicy | None = None,
) -> ResolvedDataflowCapability:
    return ResolvedDataflowCapability(
        binding_id="lookup",
        capability_ref="cap_0123456789abcdef0123456789abcdef",
        descriptor_revision="descriptor-v3",
        input_schema_revision="input-schema-v2",
        output_schema_revision="output-schema-v5",
        effect_class=effect_class,
        output_type=DataflowValueType.STRING,
        max_calls=max_calls,
        concurrency_policy=concurrency_policy or ConcurrencyPolicy(),
    )


def _invoke_plan(
    *,
    op: DataflowNodeKind = DataflowNodeKind.INVOKE,
    max_items: int = 3,
    limits: DataflowLimits | None = None,
) -> DataflowPlan:
    return DataflowPlan(
        plan_id="invoke-plan",
        input_bindings=(
            DataflowInputBinding(
                name="records",
            ),
        ),
        capability_bindings=("lookup",),
        nodes=(
            DataflowNode(
                node_id="lookup-record",
                op=op,
                inputs=("records",),
                expression=_literal({}),
                capability_binding="lookup",
                max_output_items=max_items,
            ),
        ),
        output_node_id="lookup-record",
        output_type=(
            DataflowValueType.ARRAY
            if op is DataflowNodeKind.BATCH_INVOKE
            else DataflowValueType.STRING
        ),
        limits=limits or DataflowLimits(),
    )


def test_validates_closed_pure_plan_with_stable_digest() -> None:
    plan = _pure_plan()

    first = _validate(plan)
    second = _validate(plan)

    assert first == second
    assert first.plan_digest.startswith("sha256:")
    assert first.topological_order == ("normalize",)
    assert first.estimated_iterations == 3
    assert first.estimated_inner_calls == 0


def test_digest_is_canonical_across_declaration_order() -> None:
    input_bindings = (
        DataflowInputBinding(
            name="left",
        ),
        DataflowInputBinding(
            name="right",
        ),
    )
    nodes = (
        DataflowNode(
            node_id="a",
            op=DataflowNodeKind.MAP,
            inputs=("left",),
            expression=_field("value"),
            max_output_items=1,
        ),
        DataflowNode(
            node_id="b",
            op=DataflowNodeKind.MAP,
            inputs=("right",),
            expression=_field("value"),
            max_output_items=1,
        ),
        DataflowNode(
            node_id="out",
            op=DataflowNodeKind.EMIT,
            inputs=("a", "b"),
            expression=_literal("done"),
            max_output_items=1,
        ),
    )
    first = DataflowPlan(
        plan_id="canonical",
        input_bindings=input_bindings,
        nodes=nodes,
        output_node_id="out",
        output_type=DataflowValueType.STRING,
    )
    second = first.model_copy(
        update={
            "input_bindings": tuple(reversed(input_bindings)),
            "nodes": tuple(reversed(nodes)),
        }
    )

    assert _validate(first).plan_digest == _validate(second).plan_digest


def test_rejects_arbitrary_source_and_unknown_node_vocabulary() -> None:
    payload = _pure_plan().model_dump(mode="json")
    payload["source"] = "import os"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DataflowPlan.model_validate(payload)

    payload.pop("source")
    payload["nodes"][0]["op"] = "execute_python"
    with pytest.raises(ValidationError, match="Input should be"):
        DataflowPlan.model_validate(payload)


def test_expression_contract_rejects_dynamic_call_shape_and_type_mismatch() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DataflowExpression.model_validate(
            {
                "op": "literal",
                "literal": 1,
                "function": "eval",
            }
        )

    expression = DataflowExpression(
        op=DataflowExpressionKind.AND,
        args=(_literal(True), _literal("not-a-boolean")),
    )
    plan = _pure_plan().model_copy(
        update={
            "nodes": (
                _pure_plan().nodes[0].model_copy(update={"expression": expression}),
            ),
            "output_type": DataflowValueType.BOOLEAN,
        }
    )
    with pytest.raises(DataflowValidationError) as caught:
        _validate(plan)

    assert caught.value.code is DataflowValidationErrorCode.EXPRESSION_TYPE_MISMATCH


def test_rejects_cycle_before_any_execution() -> None:
    plan = DataflowPlan(
        plan_id="cycle",
        input_bindings=(
            DataflowInputBinding(
                name="rows",
            ),
        ),
        nodes=(
            DataflowNode(
                node_id="a",
                op=DataflowNodeKind.MAP,
                inputs=("b",),
                expression=_literal("a"),
                max_output_items=1,
            ),
            DataflowNode(
                node_id="b",
                op=DataflowNodeKind.MAP,
                inputs=("a", "rows"),
                expression=_literal("b"),
                max_output_items=1,
            ),
        ),
        output_node_id="b",
        output_type=DataflowValueType.STRING,
    )

    with pytest.raises(DataflowValidationError) as caught:
        _validate(plan)

    assert caught.value.code is DataflowValidationErrorCode.CYCLIC_GRAPH


@pytest.mark.parametrize(
    ("limits", "expected_code"),
    (
        (
            DataflowLimits(max_edges=1),
            DataflowValidationErrorCode.POLICY_LIMIT_EXCEEDED,
        ),
        (
            DataflowLimits(max_expression_nodes=1),
            DataflowValidationErrorCode.EXPRESSION_LIMIT_EXCEEDED,
        ),
    ),
)
def test_rejects_structural_bounds(
    limits: DataflowLimits,
    expected_code: DataflowValidationErrorCode,
) -> None:
    plan = DataflowPlan(
        plan_id="bounded",
        input_bindings=(
            DataflowInputBinding(
                name="left",
            ),
            DataflowInputBinding(
                name="right",
            ),
        ),
        nodes=(
            DataflowNode(
                node_id="out",
                op=DataflowNodeKind.EMIT,
                inputs=("left", "right"),
                expression=DataflowExpression(
                    op=DataflowExpressionKind.LOWER,
                    args=(_literal("value"),),
                ),
                max_output_items=1,
            ),
        ),
        output_node_id="out",
        output_type=DataflowValueType.STRING,
        limits=limits,
    )

    with pytest.raises(DataflowValidationError) as caught:
        _validate(plan)

    assert caught.value.code is expected_code


def test_plan_limits_cannot_raise_installation_policy() -> None:
    policy = DataflowValidationPolicy(
        limits=DataflowLimits(max_nodes=1, max_wall_ms=1_000)
    )

    with pytest.raises(DataflowValidationError) as caught:
        _validate(_pure_plan(), policy=policy)

    assert caught.value.code is DataflowValidationErrorCode.POLICY_LIMIT_EXCEEDED


def test_requires_exact_authorized_capability_binding_set() -> None:
    with pytest.raises(DataflowValidationError) as caught:
        _validate(_invoke_plan())

    assert caught.value.code is DataflowValidationErrorCode.CAPABILITY_UNKNOWN


@pytest.mark.parametrize(
    "effect_class",
    (
        EffectClass.UNKNOWN,
        EffectClass.INTERNAL_REVERSIBLE,
        EffectClass.EXTERNAL_REVERSIBLE,
        EffectClass.EXTERNAL_DESTRUCTIVE,
    ),
)
def test_rejects_effectful_or_unknown_capabilities(
    effect_class: EffectClass,
) -> None:
    with pytest.raises(DataflowValidationError) as caught:
        _validate(
            _invoke_plan(),
            capabilities=(_capability(effect_class=effect_class),),
        )

    assert caught.value.code is DataflowValidationErrorCode.CAPABILITY_EFFECT_DENIED


def test_run_policy_can_disable_all_capability_invocation() -> None:
    policy = DataflowValidationPolicy(allowed_effect_classes=())

    with pytest.raises(DataflowValidationError) as caught:
        _validate(
            _invoke_plan(),
            capabilities=(_capability(),),
            policy=policy,
        )

    assert caught.value.code is DataflowValidationErrorCode.CAPABILITY_EFFECT_DENIED


def test_rejects_unproven_batch_concurrency() -> None:
    with pytest.raises(DataflowValidationError) as caught:
        _validate(
            _invoke_plan(op=DataflowNodeKind.BATCH_INVOKE),
            capabilities=(_capability(),),
        )

    assert caught.value.code is DataflowValidationErrorCode.BATCH_POLICY_DENIED


def test_accepts_explicit_trusted_bounded_read_batch() -> None:
    concurrency = ConcurrencyPolicy(
        mode=ConcurrencyMode.PARALLEL_SAFE,
        side_effect=SideEffectKind.READ,
        max_parallelism=4,
        policy_source=PolicySource.PRODUCT_CATALOG,
    )

    result = _validate(
        _invoke_plan(op=DataflowNodeKind.BATCH_INVOKE),
        capabilities=(_capability(concurrency_policy=concurrency),),
    )

    assert result.estimated_inner_calls == 3
    assert result.topological_order == ("lookup-record",)


def test_rejects_static_inner_call_bound_and_per_capability_bound() -> None:
    plan = _invoke_plan(
        max_items=3,
        limits=DataflowLimits(max_inner_calls=2),
    )
    with pytest.raises(DataflowValidationError) as caught:
        _validate(
            plan,
            capabilities=(_capability(max_calls=3),),
        )
    assert caught.value.code is DataflowValidationErrorCode.INNER_CALL_LIMIT_EXCEEDED

    with pytest.raises(DataflowValidationError) as caught:
        _validate(
            _invoke_plan(max_items=3),
            capabilities=(_capability(max_calls=2),),
        )
    assert (
        caught.value.code is DataflowValidationErrorCode.CAPABILITY_CALL_LIMIT_EXCEEDED
    )


def test_digest_binds_trusted_descriptor_revision() -> None:
    plan = _invoke_plan()
    first = _capability()
    second = first.model_copy(update={"descriptor_revision": "descriptor-v4"})

    assert (
        _validate(
            plan,
            capabilities=(first,),
        ).plan_digest
        != _validate(
            plan,
            capabilities=(second,),
        ).plan_digest
    )


def test_plan_cannot_declare_input_shape_source_or_revision() -> None:
    payload = _pure_plan().model_dump(mode="json")
    binding = payload["input_bindings"][0]
    assert binding == {"name": "rows"}

    for forbidden_field, forbidden_value in (
        ("value_type", "string"),
        ("max_items", 999),
        ("source_revision", "forged"),
        ("schema_revision", "forged"),
        ("input_ref", "input_00000000000000000000000000000000"),
    ):
        candidate = _pure_plan().model_dump(mode="json")
        candidate["input_bindings"][0][forbidden_field] = forbidden_value
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            DataflowPlan.model_validate(candidate)


def test_requires_exact_trusted_input_binding_set() -> None:
    plan = _pure_plan()

    with pytest.raises(DataflowValidationError) as caught:
        _validate(plan, inputs=())

    assert caught.value.code is DataflowValidationErrorCode.INPUT_BINDING_UNKNOWN


def test_field_types_are_derived_from_trusted_input_schema() -> None:
    plan = _pure_plan()
    trusted = _trusted_inputs(plan)[0].model_copy(
        update={"value_type": DataflowValueType.INTEGER}
    )

    with pytest.raises(DataflowValidationError) as caught:
        _validate(plan, inputs=(trusted,))

    assert caught.value.code is DataflowValidationErrorCode.EXPRESSION_TYPE_MISMATCH


def test_object_fields_must_exist_in_trusted_schema() -> None:
    plan = DataflowPlan(
        plan_id="trusted-field",
        input_bindings=(DataflowInputBinding(name="records"),),
        nodes=(
            DataflowNode(
                node_id="select-name",
                op=DataflowNodeKind.SELECT,
                inputs=("records",),
                expression=_field("name"),
                max_output_items=2,
            ),
        ),
        output_node_id="select-name",
        output_type=DataflowValueType.STRING,
    )
    base = ResolvedDataflowInput(
        binding_id="records",
        input_ref="input_00000000000000000000000000000001",
        source_revision="source-v1",
        schema_revision="schema-v1",
        value_type=DataflowValueType.OBJECT,
        max_items=2,
    )

    with pytest.raises(DataflowValidationError) as caught:
        _validate(plan, inputs=(base,))
    assert caught.value.code is DataflowValidationErrorCode.INPUT_FIELD_UNKNOWN

    validated = _validate(
        plan,
        inputs=(
            base.model_copy(
                update={
                    "fields": (
                        DataflowFieldDescriptor(
                            path=("name",),
                            value_type=DataflowValueType.STRING,
                        ),
                    )
                }
            ),
        ),
    )
    assert validated.maximum_input_items == 2


def test_digest_binds_trusted_input_and_evaluator_revisions() -> None:
    plan = _pure_plan()
    trusted = _trusted_inputs(plan)[0]
    schema_changed = trusted.model_copy(update={"schema_revision": "schema-v2"})
    evaluator_changed = _EVALUATOR_SEMANTICS.model_copy(
        update={"expression_semantics_revision": "expressions-v2"}
    )

    baseline = _validate(plan, inputs=(trusted,)).plan_digest
    assert baseline != _validate(plan, inputs=(schema_changed,)).plan_digest
    assert (
        baseline
        != _validate(
            plan,
            inputs=(trusted,),
            evaluator_semantics=evaluator_changed,
        ).plan_digest
    )


def test_digest_binds_trusted_capability_schema_revisions() -> None:
    plan = _invoke_plan()
    first = _capability()
    second = first.model_copy(update={"output_schema_revision": "output-schema-v6"})

    assert (
        _validate(plan, capabilities=(first,)).plan_digest
        != _validate(
            plan,
            capabilities=(second,),
        ).plan_digest
    )
