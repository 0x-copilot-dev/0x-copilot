from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter

import pytest
from pydantic import ValidationError

from agent_runtime.api.model_invocation_catalog import (
    ModelDeploymentCatalogAdapter,
    ModelEndpointAuthority,
    ModelHealthAuthority,
    ModelInvocationAuthorityAdapter,
    ModelInvocationAuthorityAdapterInput,
    ModelInvocationRequirementsAdapter,
    ModelQualificationAuthority,
)
from agent_runtime.control_plane.context import RunControlBinding
from agent_runtime.control_plane.contracts import (
    RunControlSnapshot,
    RunPolicyRevisions,
)
from agent_runtime.control_plane.feature_modes import FeatureModeSet
from agent_runtime.execution.call_identity import RuntimeModelCallIdentity
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    ModelConfig,
    ModelReasoningConfig,
)
from agent_runtime.execution.model_invocation import (
    ByokPolicy,
    ModelCapability,
    ModelCredentialMode,
    ModelDeploymentCatalog,
    ModelDeploymentHealth,
    ModelFallbackPolicy,
    ModelInvocationAuthority,
    ModelInvocationAuthorityBinder,
    ModelInvocationRequirementsSnapshot,
    ModelPrivacyFeature,
    ModelRouteExclusionReason,
    ModelRoutePolicy,
)
from runtime_api.schemas.runs import ModelCatalogItem

_SHA = "0" * 64


def _context(
    *,
    provider_keys: dict[str, str] | None = None,
    provider_endpoints: dict[str, str] | None = None,
    user_policies_json: dict[str, object] | None = None,
    workspace_behavior_overrides: dict[str, object] | None = None,
) -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id="user-1",
        org_id="org-1",
        roles=frozenset({"member"}),
        model_profile=ModelConfig(
            provider="openai",
            model_name="gpt-5",
            max_input_tokens=128_000,
            max_output_tokens=32_000,
            timeout_seconds=60,
            temperature=0,
            supports_streaming=True,
            reasoning=ModelReasoningConfig(),
        ),
        request_id="request-1",
        run_id="run-1",
        trace_id="trace-1",
        provider_keys=provider_keys or {},
        provider_endpoints=provider_endpoints or {},
        user_policies_json=user_policies_json or {},
        workspace_behavior_overrides=workspace_behavior_overrides or {},
    )


def _item(
    model_name: str,
    *,
    provider: str = "openai",
    enabled: bool = True,
    input_cost: float | None = 1.25,
) -> ModelCatalogItem:
    return ModelCatalogItem(
        id=f"{provider}:{model_name}",
        provider=provider,
        model_name=model_name,
        name=model_name,
        configured=True,
        enabled=enabled,
        supports_streaming=True,
        supports_reasoning=True,
        context_window=128_000,
        max_output_tokens=32_000,
        input_cost_per_mtok=input_cost,
        output_cost_per_mtok=5.0,
        supports_tools=True,
    )


def _default_endpoint(
    *,
    region: str = "default",
    revision: str = "openai-default-v1",
) -> ModelEndpointAuthority:
    return ModelEndpointAuthority.from_revision(
        provider="openai",
        region=region,
        endpoint_identity_revision=revision,
        credential_modes=frozenset(
            {ModelCredentialMode.DEPLOYMENT, ModelCredentialMode.BYOK}
        ),
        privacy_features=frozenset({ModelPrivacyFeature.TRAINING_OPT_OUT}),
    )


def _control(*, route_revision: str = "model-route-policy.v7") -> RunControlBinding:
    revision_values = {field: "policy-v7" for field in RunPolicyRevisions.model_fields}
    revision_values["model_route"] = route_revision
    snapshot = RunControlSnapshot.create(
        run_id="run-1",
        conversation_id="conversation-1",
        subject_fingerprint=_SHA,
        deployment_profile="single_user_desktop",
        harness_variant_ref="harness-v7",
        task_policy_selection_ref="task-policy-v7",
        policy_revisions=RunPolicyRevisions.model_validate(revision_values),
        feature_modes=FeatureModeSet(),
        budget_envelope_ref=f"budget://v7/sha256/{_SHA}",
        assignment_revision="assignment-v7",
        snapshot_id="snapshot-1",
        created_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
    )
    return RunControlBinding(
        snapshot=snapshot,
        effective_modes=FeatureModeSet(),
        decisions=(),
    )


def _identity() -> RuntimeModelCallIdentity:
    return RuntimeModelCallIdentity(
        run_id="run-1",
        snapshot_id="snapshot-1",
        execution_scope="supervisor",
        model_turn=1,
        model_call_id="model-call:stable-1",
    )


def _adapter_input(
    *,
    context: AgentRuntimeContext,
    items: tuple[ModelCatalogItem, ...],
    endpoints: tuple[ModelEndpointAuthority, ...] = (),
    qualifications: tuple[ModelQualificationAuthority, ...] = (),
    health: tuple[ModelHealthAuthority, ...] = (),
    required_capabilities: frozenset[ModelCapability] = frozenset(),
    deployment_credential_providers: frozenset[str] = frozenset(),
    byok_policy: ByokPolicy = ByokPolicy.ALLOWED,
    fallback_policy: ModelFallbackPolicy = ModelFallbackPolicy.NONE,
) -> ModelInvocationAuthorityAdapterInput:
    return ModelInvocationAuthorityAdapterInput(
        runtime_context=context,
        catalog_items=items,
        endpoints=endpoints,
        qualifications=qualifications,
        health=health,
        task_family="public_research",
        required_capabilities=required_capabilities,
        deployment_credential_providers=deployment_credential_providers,
        byok_policy=byok_policy,
        fallback_policy=fallback_policy,
        purpose="supervisor",
        request_digest=f"sha256:{'a' * 64}",
    )


def test_catalog_is_deterministic_and_contains_only_non_secret_revisions() -> None:
    context = _context(
        provider_keys={"openai": "sk-user-secret"},
        provider_endpoints={"openai": "https://private.example/v1"},
    )
    items = (_item("gpt-5-mini"), _item("gpt-5"))
    adapter = ModelDeploymentCatalogAdapter()

    first = adapter.build(_adapter_input(context=context, items=items))
    second = adapter.build(
        _adapter_input(context=context, items=tuple(reversed(items)))
    )

    assert first == second
    assert first.catalog_revision.startswith("model-deployment-catalog.v1:sha256:")
    assert first.descriptor_set_digest.startswith("sha256:")
    serialized = first.model_dump_json()
    assert "sk-user-secret" not in serialized
    assert "private.example" not in serialized
    for descriptor in first.descriptors:
        assert descriptor.deployment_id.startswith("model-deployment:")
        assert descriptor.deployment_revision.startswith("model-deployment.v1:sha256:")
        assert descriptor.endpoint_ref.startswith("endpoint_")
        assert descriptor.endpoint_revision.startswith("model-endpoint.v1:sha256:")
        assert descriptor.price_revision.startswith("model-price.v1:sha256:")
        assert descriptor.descriptor_revision.startswith("model-descriptor.v1:sha256:")


def test_adapter_input_snapshots_ephemeral_authority_facts() -> None:
    context = _context(
        provider_keys={"openai": "sk-original"},
        provider_endpoints={"openai": "https://original.example/v1"},
    )
    authority_input = _adapter_input(
        context=context,
        items=(_item("gpt-5"),),
    )

    context.provider_keys["openai"] = "sk-mutated"
    context.provider_endpoints["openai"] = "https://mutated.example/v1"

    assert authority_input.runtime_context.provider_keys["openai"] == "sk-original"
    assert (
        authority_input.runtime_context.provider_endpoints["openai"]
        == "https://original.example/v1"
    )
    serialized = authority_input.model_dump_json()
    assert "sk-original" not in serialized
    assert "original.example" not in serialized


def test_price_endpoint_qualification_and_health_revisions_invalidate_catalog() -> None:
    context = _context()
    adapter = ModelDeploymentCatalogAdapter()
    baseline = adapter.build(
        _adapter_input(
            context=context,
            items=(_item("gpt-5"),),
            endpoints=(_default_endpoint(),),
        )
    )
    changed_price = adapter.build(
        _adapter_input(
            context=context,
            items=(_item("gpt-5", input_cost=2.0),),
            endpoints=(_default_endpoint(),),
        )
    )
    changed_endpoint = adapter.build(
        _adapter_input(
            context=context,
            items=(_item("gpt-5"),),
            endpoints=(_default_endpoint(revision="openai-default-v2"),),
        )
    )
    qualified = adapter.build(
        _adapter_input(
            context=context,
            items=(_item("gpt-5"),),
            endpoints=(_default_endpoint(),),
            qualifications=(
                ModelQualificationAuthority(
                    provider="openai",
                    model_name="gpt-5",
                    task_families=frozenset({"public_research"}),
                    qualification_revision="qualification-f1-v2",
                ),
            ),
        )
    )
    unhealthy = adapter.build(
        _adapter_input(
            context=context,
            items=(_item("gpt-5"),),
            endpoints=(_default_endpoint(),),
            health=(
                ModelHealthAuthority(
                    provider="openai",
                    model_name="gpt-5",
                    region="default",
                    health=ModelDeploymentHealth.UNAVAILABLE,
                    health_revision="health-window-v2",
                ),
            ),
        )
    )

    variants = (changed_price, changed_endpoint, qualified, unhealthy)
    assert all(
        variant.descriptor_set_digest != baseline.descriptor_set_digest
        for variant in variants
    )
    assert (
        changed_price.descriptors[0].deployment_id
        == baseline.descriptors[0].deployment_id
    )
    assert (
        changed_endpoint.descriptors[0].deployment_id
        != baseline.descriptors[0].deployment_id
    )
    assert qualified.descriptors[0].qualification_revision == "qualification-f1-v2"
    assert unhealthy.descriptors[0].health is ModelDeploymentHealth.UNAVAILABLE


def test_requirements_use_verified_privacy_byok_and_default_to_no_fallback() -> None:
    context = _context(
        provider_keys={"openai": "sk-user-secret"},
        user_policies_json={
            "privacy": {
                "training_opt_out": False,
                "region": "eu-west-1",
            }
        },
        workspace_behavior_overrides={"training_data_opt_out": True},
    )
    endpoint = _default_endpoint(region="eu-west-1", revision="openai-eu-v1")
    authority_input = _adapter_input(
        context=context,
        items=(_item("gpt-5"),),
        endpoints=(endpoint,),
        required_capabilities=frozenset({ModelCapability.TOOLS}),
    )
    catalog = ModelDeploymentCatalogAdapter().build(authority_input)

    snapshot = ModelInvocationRequirementsAdapter().build(
        authority_input=authority_input,
        catalog=catalog,
    )
    requirements = snapshot.requirements

    assert snapshot.requirements_digest.startswith("sha256:")
    assert requirements.region == "eu-west-1"
    assert requirements.training_opt_out_required
    assert requirements.fallback_policy is ModelFallbackPolicy.NONE
    assert requirements.required_capabilities == {
        ModelCapability.STREAMING,
        ModelCapability.REASONING,
        ModelCapability.TOOLS,
    }
    assert requirements.credential_availability[0].modes == {ModelCredentialMode.BYOK}
    assert requirements.primary_deployment_id == catalog.descriptors[0].deployment_id
    assert "sk-user-secret" not in snapshot.model_dump_json()


def test_missing_credentials_are_an_explicit_route_exclusion() -> None:
    context = _context()
    authority_input = _adapter_input(
        context=context,
        items=(_item("gpt-5"),),
    )
    catalog = ModelDeploymentCatalogAdapter().build(authority_input)
    requirements = ModelInvocationRequirementsAdapter().build(
        authority_input=authority_input,
        catalog=catalog,
    )

    plan = ModelRoutePolicy().plan_catalog(
        requirements.requirements,
        catalog,
    )

    assert plan.routes == ()
    assert plan.exclusions[0].reasons == (
        ModelRouteExclusionReason.CREDENTIAL_UNAVAILABLE,
    )
    with pytest.raises(ValueError, match="BYOK is required"):
        ModelInvocationRequirementsAdapter().build(
            authority_input=authority_input.model_copy(
                update={"byok_policy": ByokPolicy.REQUIRED}
            ),
            catalog=catalog,
        )


def test_authority_binding_is_replay_stable_and_rejects_stale_inputs() -> None:
    context = _context(provider_keys={"openai": "sk-user-secret"})
    authority_input = _adapter_input(
        context=context,
        items=(_item("gpt-5"),),
    )
    catalog = ModelDeploymentCatalogAdapter().build(authority_input)
    requirements = ModelInvocationRequirementsAdapter().build(
        authority_input=authority_input,
        catalog=catalog,
    )
    control = _control()
    route_plan = ModelRoutePolicy().plan_catalog(
        requirements.requirements,
        catalog,
        policy_revision=control.snapshot.policy_revisions.model_route,
    )
    binder = ModelInvocationAuthorityBinder()
    arguments = {
        "call_identity": _identity(),
        "control": control,
        "purpose": "supervisor",
        "request_digest": f"sha256:{'a' * 64}",
        "requirements": requirements,
        "catalog": catalog,
        "route_plan": route_plan,
    }

    authority = binder.bind(**arguments)
    replay = binder.bind(**arguments)

    assert authority == replay
    assert authority.invocation_id.startswith("model-invocation:")
    assert authority.call_identity == _identity()
    assert authority.run_control_snapshot_digest == control.snapshot.snapshot_digest
    assert authority.requirements_digest == requirements.requirements_digest
    assert authority.descriptor_set_digest == catalog.descriptor_set_digest
    assert authority.route_plan_digest == route_plan.route_digest
    assert binder.verify(authority, **arguments) is authority

    with pytest.raises(ValueError, match="conflicts with replay"):
        binder.verify(
            authority,
            **{
                **arguments,
                "request_digest": f"sha256:{'b' * 64}",
            },
        )

    stale_requirements = ModelInvocationRequirementsSnapshot.create(
        requirements.requirements.model_copy(
            update={"fallback_policy": ModelFallbackPolicy.SAME_MODEL}
        )
    )
    with pytest.raises(ValueError, match="route plan is stale"):
        binder.bind(
            **{
                **arguments,
                "requirements": stale_requirements,
            },
        )


def test_authority_rejects_wrong_call_or_route_policy_revision() -> None:
    context = _context(provider_keys={"openai": "sk-user-secret"})
    authority_input = _adapter_input(
        context=context,
        items=(_item("gpt-5"),),
    )
    catalog = ModelDeploymentCatalogAdapter().build(authority_input)
    requirements = ModelInvocationRequirementsAdapter().build(
        authority_input=authority_input,
        catalog=catalog,
    )
    control = _control()
    wrong_plan = ModelRoutePolicy().plan_catalog(
        requirements.requirements,
        catalog,
        policy_revision="wrong-route-policy",
    )
    binder = ModelInvocationAuthorityBinder()

    with pytest.raises(ValueError, match="route policy revision"):
        binder.bind(
            call_identity=_identity(),
            control=control,
            purpose="supervisor",
            request_digest=f"sha256:{'a' * 64}",
            requirements=requirements,
            catalog=catalog,
            route_plan=wrong_plan,
        )
    with pytest.raises(ValueError, match="verified run"):
        binder.bind(
            call_identity=_identity().model_copy(update={"run_id": "other-run"}),
            control=control,
            purpose="supervisor",
            request_digest=f"sha256:{'a' * 64}",
            requirements=requirements,
            catalog=catalog,
            route_plan=ModelRoutePolicy().plan_catalog(
                requirements.requirements,
                catalog,
                policy_revision=control.snapshot.policy_revisions.model_route,
            ),
        )


def test_authority_contract_rejects_digest_tampering() -> None:
    context = _context(provider_keys={"openai": "sk-user-secret"})
    authority_input = _adapter_input(
        context=context,
        items=(_item("gpt-5"),),
    )
    catalog = ModelDeploymentCatalogAdapter().build(authority_input)
    payload = catalog.model_dump(mode="json")
    payload["descriptors"][0]["price_revision"] = "forged-price"
    with pytest.raises(ValidationError, match="descriptor_set_digest"):
        ModelDeploymentCatalog.model_validate(payload)

    requirements = ModelInvocationRequirementsAdapter().build(
        authority_input=authority_input,
        catalog=catalog,
    )
    requirements_payload = requirements.model_dump(mode="json")
    requirements_payload["requirements"]["task_family"] = "forged-family"
    with pytest.raises(ValidationError, match="requirements_digest"):
        ModelInvocationRequirementsSnapshot.model_validate(requirements_payload)

    control = _control()
    route_plan = ModelRoutePolicy().plan_catalog(
        requirements.requirements,
        catalog,
        policy_revision=control.snapshot.policy_revisions.model_route,
    )
    authority = ModelInvocationAuthorityBinder().bind(
        call_identity=_identity(),
        control=control,
        purpose="supervisor",
        request_digest=f"sha256:{'a' * 64}",
        requirements=requirements,
        catalog=catalog,
        route_plan=route_plan,
    )
    authority_payload = authority.model_dump(mode="json")
    authority_payload["purpose"] = "subagent"
    with pytest.raises(ValidationError, match="authority_digest"):
        ModelInvocationAuthority.model_validate(authority_payload)


def test_canonical_catalog_route_planning_is_one_pass_and_bounded() -> None:
    context = _context(provider_keys={"openai": "sk-user-secret"})
    items = tuple(
        _item("gpt-5" if index == 0 else f"model-{index:03}") for index in range(512)
    )
    authority_input = _adapter_input(
        context=context,
        items=items,
        endpoints=(_default_endpoint(),),
    )
    catalog = ModelDeploymentCatalogAdapter().build(authority_input)
    requirements = ModelInvocationRequirementsAdapter().build(
        authority_input=authority_input,
        catalog=catalog,
    )

    class CountingRoutePolicy(ModelRoutePolicy):
        calls = 0

        @classmethod
        def _exclusion_reasons(cls, *args, **kwargs):
            cls.calls += 1
            return super()._exclusion_reasons(*args, **kwargs)

    policy = CountingRoutePolicy()
    durations: list[float] = []
    for _ in range(25):
        CountingRoutePolicy.calls = 0
        started = perf_counter()
        plan = policy.plan_catalog(requirements.requirements, catalog)
        durations.append(perf_counter() - started)
        assert CountingRoutePolicy.calls == len(catalog.descriptors)
        assert plan.routes

    p95 = sorted(durations)[int(len(durations) * 0.95) - 1]
    # The PRD target is 5 ms on production hardware. Keep the hermetic CI
    # assertion tolerant of shared runners while still catching accidental
    # quadratic work at the maximum 512-descriptor bound.
    assert p95 < 0.050

    with pytest.raises(ValidationError, match="at most 512 items"):
        _adapter_input(
            context=context,
            items=(*items, _item("model-overflow")),
        )


def test_unverified_catalog_alternates_do_not_receive_endpoint_authority() -> None:
    context = _context(provider_keys={"openai": "sk-user-secret"})
    authority_input = _adapter_input(
        context=context,
        items=(
            _item("gpt-5-mini"),
            _item("gpt-5"),
            _item("claude-opus", provider="anthropic"),
        ),
        fallback_policy=ModelFallbackPolicy.QUALIFIED_EQUIVALENT,
    )

    prepared = ModelInvocationAuthorityAdapter().prepare(
        authority_input=authority_input,
        call_identity=_identity(),
        control=_control(),
    )

    assert len(prepared.catalog.descriptors) == 1
    assert prepared.catalog.descriptors[0].model_name == "gpt-5"
    assert prepared.route_plan.deployment_ids == (
        prepared.catalog.descriptors[0].deployment_id,
    )
    assert prepared.authority.route_plan_digest == prepared.route_plan.route_digest
    serialized_input = authority_input.model_dump_json()
    assert "runtime_context" not in serialized_input


def test_verified_endpoint_records_can_authorize_catalog_alternates() -> None:
    context = _context(provider_keys={"openai": "sk-user-secret"})
    authority_input = _adapter_input(
        context=context,
        items=(_item("gpt-5-mini"), _item("gpt-5")),
        endpoints=(_default_endpoint(),),
        fallback_policy=ModelFallbackPolicy.QUALIFIED_EQUIVALENT,
        qualifications=(
            ModelQualificationAuthority(
                provider="openai",
                model_name="gpt-5-mini",
                task_families=frozenset({"public_research"}),
                qualification_revision="qualification-f1-v1",
            ),
        ),
    )

    catalog = ModelDeploymentCatalogAdapter().build(authority_input)

    assert {descriptor.model_name for descriptor in catalog.descriptors} == {
        "gpt-5",
        "gpt-5-mini",
    }
