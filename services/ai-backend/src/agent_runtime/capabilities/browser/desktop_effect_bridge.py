"""Authenticated client for Electron main's private browser-effect routes.

The model-facing MCP transport can only dispatch advertised reads. This client
is separately injected into A5's browser executor and can only prepare an exact
action plan, consume a one-use prepared reference, or reconcile that reference.
It has no arbitrary tool/action method and never exposes its bearer in repr,
errors, events, or model-visible output.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx

from agent_runtime.capabilities.browser.constants import (
    BrowserBroker,
    BrowserEnv,
    BrowserMessages,
)
from agent_runtime.capabilities.browser.contracts import (
    BrowserActionPlan,
    BrowserApplyReceipt,
    BrowserEffectBridge,
    BrowserOperationError,
    BrowserPrepareResult,
)


@dataclass
class DesktopBrowserEffectBridge(BrowserEffectBridge):
    """Strict private HTTP adapter to the Electron-main browser authority."""

    broker_url: str
    broker_token: str = field(repr=False)
    service_identity: str | None = None
    broker_audience: str | None = None
    timeout_seconds: float = 10.0
    http_client: httpx.AsyncClient = field(
        default_factory=httpx.AsyncClient,
        repr=False,
        compare=False,
    )

    async def prepare_action(self, plan: BrowserActionPlan) -> BrowserPrepareResult:
        payload = await self._post(
            BrowserBroker.ROUTE_PRIVATE_PREPARE,
            {"plan": _plan_to_wire(plan)},
        )
        prepared = payload.get("prepared")
        if not isinstance(prepared, dict):
            raise BrowserOperationError(BrowserMessages.INVALID_RESPONSE)
        try:
            _require_only_keys(
                prepared,
                {
                    "preparedRef",
                    "observedPreconditionDigest",
                    "expiresAt",
                    "preconditionDrift",
                },
            )
            return BrowserPrepareResult(
                prepared_ref=_optional_str(prepared, "preparedRef"),
                observed_precondition_digest=_required_str(
                    prepared,
                    "observedPreconditionDigest",
                ),
                expires_at=_optional_str(prepared, "expiresAt"),
                precondition_drift=_required_bool(prepared, "preconditionDrift"),
            )
        except (TypeError, ValueError) as exc:
            raise BrowserOperationError(BrowserMessages.INVALID_RESPONSE) from exc

    async def apply_prepared(self, prepared_ref: str) -> BrowserApplyReceipt:
        payload = await self._post(
            BrowserBroker.ROUTE_PRIVATE_APPLY,
            {"preparedRef": prepared_ref},
        )
        return _receipt_from_payload(payload)

    async def reconcile_action(self, prepared_ref: str) -> BrowserApplyReceipt:
        payload = await self._post(
            BrowserBroker.ROUTE_PRIVATE_RECONCILE,
            {"preparedRef": prepared_ref},
        )
        return _receipt_from_payload(payload)

    async def _post(self, route: str, body: dict[str, object]) -> dict[str, Any]:
        now_ms = int(time.time() * 1000)
        envelope: dict[str, object] = {
            "aud": BrowserBroker.AUDIENCE,
            "nonce": uuid4().hex,
            "requestId": uuid4().hex,
            "expiresAt": now_ms + BrowserBroker.ENVELOPE_TTL_MS,
            **body,
        }
        headers = {
            "authorization": f"Bearer {self.broker_token}",
            BrowserBroker.PROTOCOL_HEADER: BrowserBroker.PROTOCOL_VERSION,
            "content-type": "application/json",
        }
        if self.service_identity:
            headers[BrowserEnv.SERVICE_IDENTITY_HEADER] = self.service_identity
        if self.broker_audience:
            headers[BrowserEnv.SERVICE_AUDIENCE_HEADER] = self.broker_audience
        try:
            response = await self.http_client.post(
                f"{self.broker_url.rstrip('/')}{route}",
                json=envelope,
                headers=headers,
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise BrowserOperationError(BrowserMessages.BROKER_UNAVAILABLE) from exc
        if response.status_code in {401, 403}:
            raise BrowserOperationError(BrowserMessages.BROKER_UNAUTHENTICATED)
        if response.status_code >= 400:
            raise BrowserOperationError(BrowserMessages.BROKER_UNAVAILABLE)
        try:
            payload = response.json()
        except ValueError as exc:
            raise BrowserOperationError(BrowserMessages.INVALID_RESPONSE) from exc
        if not isinstance(payload, dict):
            raise BrowserOperationError(BrowserMessages.INVALID_RESPONSE)
        return payload


def _plan_to_wire(plan: BrowserActionPlan) -> dict[str, object]:
    precondition: dict[str, object] = {
        "pageGeneration": plan.precondition.page_generation,
        "origin": plan.precondition.origin,
    }
    if plan.precondition.element_fingerprint is not None:
        precondition["elementFingerprint"] = plan.precondition.element_fingerprint
    if plan.precondition.form_fingerprint is not None:
        precondition["formFingerprint"] = plan.precondition.form_fingerprint
    if plan.precondition.form_payload_digest is not None:
        precondition["formPayloadDigest"] = plan.precondition.form_payload_digest

    wire: dict[str, object] = {
        "sessionRef": plan.session_ref,
        "pageRef": plan.page_ref,
        "origin": plan.origin,
        "topLevelOrigin": plan.top_level_origin,
        "actionKind": plan.action_kind.value,
        "canonicalFieldsRef": plan.canonical_fields_ref,
        "fieldsDigest": plan.fields_digest,
        "uploadArtifactRefs": list(plan.upload_artifact_refs),
        "uploadArtifacts": [
            {
                "artifactRef": upload.artifact_ref,
                "digest": upload.digest,
                "byteSize": upload.byte_size,
                "mediaType": upload.media_type,
                "suggestedFilename": upload.suggested_filename,
            }
            for upload in plan.upload_artifacts
        ],
        "precondition": precondition,
        "preconditionDigest": plan.precondition_digest,
        "userVisibleSummary": plan.user_visible_summary,
    }
    optional_fields = {
        "elementRef": plan.element_ref,
        "elementFingerprint": plan.element_fingerprint,
        "formFingerprint": plan.form_fingerprint,
        "formPayloadDigest": plan.form_payload_digest,
        "formActionUrl": plan.form_action_url,
        "method": plan.method,
    }
    wire.update(
        {key: value for key, value in optional_fields.items() if value is not None}
    )
    return wire


def _receipt_from_payload(payload: dict[str, Any]) -> BrowserApplyReceipt:
    receipt = payload.get("receipt")
    if not isinstance(receipt, dict):
        raise BrowserOperationError(BrowserMessages.INVALID_RESPONSE)
    try:
        _require_only_keys(
            receipt,
            {"outcome", "receiptRef", "resultDigest", "safeMessage"},
        )
        return BrowserApplyReceipt(
            outcome=_required_str(receipt, "outcome"),
            receipt_ref=_optional_str(receipt, "receiptRef"),
            result_digest=_optional_str(receipt, "resultDigest"),
            safe_message=_optional_str(receipt, "safeMessage"),
        )
    except (TypeError, ValueError) as exc:
        raise BrowserOperationError(BrowserMessages.INVALID_RESPONSE) from exc


def _required_str(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{key} is required")
    return item


def _optional_str(value: dict[str, Any], key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str) or not item:
        raise ValueError(f"{key} is invalid")
    return item


def _required_bool(value: dict[str, Any], key: str) -> bool:
    item = value.get(key)
    if not isinstance(item, bool):
        raise ValueError(f"{key} is required")
    return item


def _require_only_keys(value: dict[str, Any], allowed: set[str]) -> None:
    if not set(value).issubset(allowed):
        raise ValueError("browser broker response has unknown fields")


__all__ = ("DesktopBrowserEffectBridge",)
