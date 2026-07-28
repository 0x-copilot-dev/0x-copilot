"""Replay-stable authority binding for the existing model-route policy."""

from __future__ import annotations

from agent_runtime.control_plane.context import RunControlBinding
from agent_runtime.execution.call_identity import RuntimeModelCallIdentity
from agent_runtime.execution.model_invocation.contracts import (
    ModelDeploymentCatalog,
    ModelInvocationAuthority,
    ModelInvocationRequirementsSnapshot,
    ModelRoutePlan,
)
from agent_runtime.execution.model_invocation.policy import ModelRoutePolicy


class ModelInvocationAuthorityBinder:
    """Prove one route plan belongs to the verified run and model call."""

    def __init__(self, *, route_policy: ModelRoutePolicy | None = None) -> None:
        self._route_policy = route_policy or ModelRoutePolicy()

    def bind(
        self,
        *,
        call_identity: RuntimeModelCallIdentity,
        control: RunControlBinding,
        purpose: str,
        request_digest: str,
        requirements: ModelInvocationRequirementsSnapshot,
        catalog: ModelDeploymentCatalog,
        route_plan: ModelRoutePlan,
    ) -> ModelInvocationAuthority:
        """Validate current authority and return its deterministic identity."""

        snapshot = control.snapshot
        if call_identity.run_id != snapshot.run_id:
            raise ValueError("model-call identity does not match the verified run")
        if call_identity.snapshot_id != snapshot.snapshot_id:
            raise ValueError("model-call identity does not match run-control snapshot")
        if route_plan.policy_revision != snapshot.policy_revisions.model_route:
            raise ValueError("route policy revision does not match run control")

        expected = self._route_policy.plan_catalog(
            requirements.requirements,
            catalog,
            policy_revision=snapshot.policy_revisions.model_route,
        )
        if route_plan != expected:
            raise ValueError("route plan is stale or does not match current authority")

        return ModelInvocationAuthority.create(
            call_identity=call_identity,
            purpose=purpose,
            request_digest=request_digest,
            run_control_snapshot_digest=snapshot.snapshot_digest,
            requirements=requirements,
            catalog=catalog,
            route_plan=route_plan,
        )

    def verify(
        self,
        authority: ModelInvocationAuthority,
        *,
        call_identity: RuntimeModelCallIdentity,
        control: RunControlBinding,
        purpose: str,
        request_digest: str,
        requirements: ModelInvocationRequirementsSnapshot,
        catalog: ModelDeploymentCatalog,
        route_plan: ModelRoutePlan,
    ) -> ModelInvocationAuthority:
        """Rebuild the expected record and reject changed replay inputs."""

        expected = self.bind(
            call_identity=call_identity,
            control=control,
            purpose=purpose,
            request_digest=request_digest,
            requirements=requirements,
            catalog=catalog,
            route_plan=route_plan,
        )
        if authority != expected:
            raise ValueError("model invocation authority conflicts with replay inputs")
        return authority


__all__ = ("ModelInvocationAuthorityBinder",)
