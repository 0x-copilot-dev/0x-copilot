"""A4/A5 stage adapter and executor for exact desktop browser actions.

Nothing in this module can reach a generic browser MCP action.  Staging stores
an immutable action plan; execution is available only through A5's closed
``EffectExecutor`` registry after a digest-pinned approval and durable claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from agent_runtime.capabilities.browser.contracts import (
    BrowserActionKind,
    BrowserActionPlan,
    BrowserActionPlanStore,
    BrowserApplyOutcome,
    BrowserApplyReceipt,
    BrowserEffectBridge,
    BrowserStagePort,
)
from agent_runtime.capabilities.operations.contracts import ProposedEffect
from agent_runtime.effects.claims import EffectClaim
from agent_runtime.effects.contracts import (
    EffectActorIdentity,
    EffectPolicySnapshot,
    EffectStageScope,
    ProposedEffect as StagedEffect,
)
from agent_runtime.effects.executor import (
    EffectExecutionAuthorization,
    EffectExecutorCapabilities,
    PreparedEffect,
)
from agent_runtime.effects.staging import EffectStager
from agent_runtime.surfaces_v2.canonical_json import canonical_json_bytes, sha256_hex
from agent_runtime.surfaces_v2.entities import (
    EffectExecutionRequest,
    EffectExecutionResult,
    EffectTarget,
    OperationRequest,
)
from agent_runtime.surfaces_v2.ledger_models import (
    EffectClass,
    EffectExecutorKind,
    EffectOutcome,
    EffectProposalKind,
)


@dataclass(frozen=True)
class BrowserEffectStageAdapter(BrowserStagePort):
    """Translate an exact browser plan into the existing A4 stage protocol."""

    plans: BrowserActionPlanStore
    stager: EffectStager
    scope: EffectStageScope
    actor: EffectActorIdentity
    policy_snapshot: EffectPolicySnapshot

    async def stage(
        self,
        *,
        request: OperationRequest,
        plan: BrowserActionPlan,
    ) -> ProposedEffect:
        stored = await self.plans.store(plan=plan)
        if stored.digest != plan.digest:
            raise ValueError("browser plan store returned a mismatched digest")
        target_ref = browser_target_ref(plan)
        precondition_ref = browser_precondition_ref(plan)
        staged = await self.stager.stage(
            scope=self.scope,
            proposed_effect=StagedEffect(
                operation_id=request.operation_id,
                executor=EffectExecutorKind.BROWSER,
                target=EffectTarget(
                    executor=EffectExecutorKind.BROWSER,
                    capability=request.capability,
                    op=request.op,
                    target_ref=target_ref,
                    precondition_ref=precondition_ref,
                    display_label=_display_target(plan),
                ),
                target_digest=plan.target_digest,
                display_target=_display_target(plan),
                proposal_kind=EffectProposalKind.BROWSER_SUBMISSION,
                proposal_content_ref=stored.content_ref,
                proposal_digest=stored.digest,
                proposal_media_type="application/vnd.0xcopilot.browser-action+json",
                precondition_ref=precondition_ref,
                precondition_digest=plan.precondition_digest,
                # Browser actions require a human decision even if another
                # policy family happens to allow automatic effects.
                effect_class=_effect_class(plan),
                policy_snapshot_ref=self.policy_snapshot.snapshot_ref,
                agent_hold=True,
                safe_summary_ref=None,
            ),
            policy_snapshot=self.policy_snapshot,
            actor=self.actor,
            idempotency_key=f"browser-stage:{request.operation_id}",
        )
        current = staged.current_revision
        return ProposedEffect(
            stage_id=staged.stage_id,
            proposal_ref=current.proposal_ref,
            safe_summary=f"Browser {_display_target(plan)} is waiting for review.",
            activity_ref=None,
            artifact_source_ref=None,
        )


@dataclass(frozen=True)
class BrowserEffectExecutor:
    """Closed A5 browser executor with prepare/apply/reconcile separation."""

    plans: BrowserActionPlanStore
    bridge: BrowserEffectBridge
    kind: EffectExecutorKind = EffectExecutorKind.BROWSER
    capabilities: EffectExecutorCapabilities = EffectExecutorCapabilities(
        supports_prepare=True,
        supports_reconcile=True,
        native_idempotency=False,
        prepare_performs_mutation=False,
    )

    async def prepare(self, request: EffectExecutionRequest) -> PreparedEffect:
        plan = await self.plans.load(content_ref=request.proposal_content_ref)
        if plan is None or plan.digest != request.proposal_digest:
            raise ValueError("browser action plan is unavailable or changed")
        if (
            browser_target_ref(plan) != request.target_ref
            or plan.target_digest != request.target_digest
        ):
            raise ValueError("browser action target does not match approved plan")
        prepared = await self.bridge.prepare_action(plan)
        return PreparedEffect(
            request=request,
            prepared_ref=prepared.prepared_ref,
            observed_precondition_digest=prepared.observed_precondition_digest,
            expires_at=prepared.expires_at,
        )

    async def apply(self, prepared: PreparedEffect) -> EffectExecutionResult:
        if prepared.prepared_ref is None:
            return EffectExecutionResult(
                outcome=EffectOutcome.PRECONDITION_DRIFT,
                retryable=False,
                safe_message="The browser page changed before the action was applied.",
            )
        receipt = await self.bridge.apply_prepared(prepared.prepared_ref)
        return _effect_result(receipt)

    async def authorize(self, prepared: PreparedEffect) -> EffectExecutionAuthorization:
        """Keep the coordinator's single pre-dispatch contract uniform.

        Electron's private bridge performs the authoritative one-use permit
        check at its own ``apply_prepared`` transport edge.
        """

        del prepared
        return EffectExecutionAuthorization(
            allowed=True,
            safe_code="browser_authorized",
        )

    async def reconcile(self, claim: EffectClaim) -> EffectExecutionResult:
        """Observe a prior attempt only; reconciliation cannot call ``apply``."""

        if claim.prepared_ref is None:
            return EffectExecutionResult(
                outcome=EffectOutcome.INDETERMINATE,
                retryable=False,
                safe_message="The browser action outcome could not be confirmed.",
            )
        receipt = await self.bridge.reconcile_action(claim.prepared_ref)
        return _effect_result(receipt)

    async def abort(self, prepared: PreparedEffect) -> None:
        """No mutation has occurred; Electron expires abandoned prepared handles."""

        del prepared


def _effect_result(receipt: BrowserApplyReceipt) -> EffectExecutionResult:
    # Electron's browser receipt is executor-private evidence.  A5's public
    # receipt namespace is deliberately claim-bound
    # (receipt://effects/<stage>/<claim>), and apply() does not receive the
    # coordinator's claim id.  Do not relabel or leak the broker reference as a
    # canonical effect receipt.  The digest remains safe completion evidence;
    # the prepared handle is retained on the claim for observational
    # reconciliation.
    outcome = receipt.outcome
    if outcome is BrowserApplyOutcome.APPLIED:
        return EffectExecutionResult(
            outcome=EffectOutcome.APPLIED,
            retryable=False,
            result_digest=receipt.result_digest,
            safe_message=(
                receipt.safe_message or "The reviewed browser action was applied."
            ),
        )
    if outcome is BrowserApplyOutcome.PRECONDITION_DRIFT:
        return EffectExecutionResult(
            outcome=EffectOutcome.PRECONDITION_DRIFT,
            retryable=False,
            result_digest=receipt.result_digest,
            safe_message=(
                receipt.safe_message
                or "The browser page changed before the action was applied."
            ),
        )
    if outcome is BrowserApplyOutcome.INDETERMINATE:
        # Browser post/submit outcomes are intentionally never retried. A5 may
        # reconcile a site-specific receipt later, but it cannot replay this.
        return EffectExecutionResult(
            outcome=EffectOutcome.INDETERMINATE,
            retryable=False,
            result_digest=receipt.result_digest,
            safe_message=(
                receipt.safe_message
                or "The browser action outcome could not be confirmed."
            ),
        )
    return EffectExecutionResult(
        outcome=EffectOutcome.FAILED,
        retryable=False,
        result_digest=receipt.result_digest,
        safe_message=(
            receipt.safe_message
            or "The reviewed browser action failed before completion."
        ),
    )


def browser_target_material(plan: BrowserActionPlan) -> bytes:
    """Canonical immutable bytes A5 re-hashes before executor resolution."""

    return canonical_json_bytes(
        {
            "session_ref": plan.session_ref,
            "page_ref": plan.page_ref,
            "origin": plan.origin,
            "top_level_origin": plan.top_level_origin,
            "action_kind": plan.action_kind.value,
            "element_ref": plan.element_ref,
            "element_fingerprint": plan.element_fingerprint,
            "form_fingerprint": plan.form_fingerprint,
            "form_payload_digest": plan.form_payload_digest,
            "form_action_url": plan.form_action_url,
            "method": plan.method,
            "upload_artifact_refs": list(plan.upload_artifact_refs),
            "upload_artifact_digests": [
                upload.digest for upload in plan.upload_artifacts
            ],
        }
    )


def browser_target_ref(plan: BrowserActionPlan) -> str:
    digest = sha256_hex(
        canonical_json_bytes(
            {
                "session_ref": plan.session_ref,
                "page_ref": plan.page_ref,
                "element_ref": plan.element_ref,
                "element_fingerprint": plan.element_fingerprint,
            }
        )
    )
    return f"browser-target://{digest}"


def browser_precondition_ref(plan: BrowserActionPlan) -> str:
    return f"browser-precondition://{plan.precondition_digest}"


# Compatibility for the contract tests that predate the public helper name.
_target_ref = browser_target_ref


def _display_target(plan: BrowserActionPlan) -> str:
    host = urlsplit(plan.origin).netloc
    return f"{plan.action_kind.value.replace('_', ' ')} on {host}"


def _effect_class(plan: BrowserActionPlan) -> EffectClass:
    if plan.action_kind in {BrowserActionKind.SUBMIT, BrowserActionKind.UPLOAD_SUBMIT}:
        return EffectClass.EXTERNAL_DESTRUCTIVE
    if plan.action_kind is BrowserActionKind.CLICK:
        # A click can submit, buy, delete, navigate, or download. It is never
        # granted a safer classification solely because the model calls it a
        # click; the effect gate therefore starts at the conservative default.
        return EffectClass.UNKNOWN
    # Input/select are still held by the mandatory agent_hold above. They are
    # potentially consequential but do not themselves transmit a form.
    return EffectClass.EXTERNAL_REVERSIBLE


__all__ = (
    "BrowserEffectExecutor",
    "BrowserEffectStageAdapter",
    "browser_precondition_ref",
    "browser_target_material",
    "browser_target_ref",
)
