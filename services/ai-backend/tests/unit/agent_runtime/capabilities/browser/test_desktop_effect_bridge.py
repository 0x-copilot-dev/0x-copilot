"""Cross-language wire checks for the private Electron browser-effect bridge."""

from __future__ import annotations

import json

import httpx
import pytest

from agent_runtime.capabilities.browser.contracts import (
    BrowserActionKind,
    BrowserActionPlan,
    BrowserApplyOutcome,
    BrowserOperationError,
    BrowserPrecondition,
)
from agent_runtime.capabilities.browser.desktop_effect_bridge import (
    DesktopBrowserEffectBridge,
)


def _plan() -> BrowserActionPlan:
    precondition = BrowserPrecondition(
        page_generation=7,
        origin="https://example.com",
        element_fingerprint="a" * 64,
        form_fingerprint="d" * 64,
        form_payload_digest="e" * 64,
    )
    return BrowserActionPlan(
        session_ref="browser-session://ses_wire",
        page_ref="browser-page://pg_wire",
        origin="https://example.com",
        top_level_origin="https://example.com",
        action_kind=BrowserActionKind.SUBMIT,
        element_ref="e7_1",
        element_fingerprint="a" * 64,
        form_fingerprint="d" * 64,
        form_payload_digest="e" * 64,
        form_action_url="https://example.com/send",
        method="POST",
        canonical_fields_ref=(
            "operation://op_00000000-0000-4000-8000-000000000001/args"
        ),
        fields_digest="b" * 64,
        precondition=precondition,
        precondition_digest=precondition.digest,
        user_visible_summary="Review browser submit on https://example.com.",
    )


async def test_private_bridge_maps_exact_plan_and_preserves_safe_receipt() -> None:
    seen: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer local-secret"
        body = json.loads(request.content)
        seen.append((request.url.path, body))
        if request.url.path.endswith("/prepare"):
            assert body["plan"] == {
                "sessionRef": "browser-session://ses_wire",
                "pageRef": "browser-page://pg_wire",
                "origin": "https://example.com",
                "topLevelOrigin": "https://example.com",
                "actionKind": "submit",
                "elementRef": "e7_1",
                "elementFingerprint": "a" * 64,
                "formFingerprint": "d" * 64,
                "formPayloadDigest": "e" * 64,
                "formActionUrl": "https://example.com/send",
                "method": "POST",
                "canonicalFieldsRef": (
                    "operation://op_00000000-0000-4000-8000-000000000001/args"
                ),
                "fieldsDigest": "b" * 64,
                "uploadArtifactRefs": [],
                "uploadArtifacts": [],
                "precondition": {
                    "pageGeneration": 7,
                    "origin": "https://example.com",
                    "elementFingerprint": "a" * 64,
                    "formFingerprint": "d" * 64,
                    "formPayloadDigest": "e" * 64,
                },
                "preconditionDigest": _plan().precondition_digest,
                "userVisibleSummary": ("Review browser submit on https://example.com."),
            }
            return httpx.Response(
                200,
                json={
                    "prepared": {
                        "preparedRef": "browser-prepared://ses_wire/one",
                        "observedPreconditionDigest": (_plan().precondition_digest),
                        "preconditionDrift": False,
                    }
                },
            )
        if request.url.path.endswith("/apply"):
            return httpx.Response(
                200,
                json={
                    "receipt": {
                        "outcome": "applied",
                        "receiptRef": "browser-receipt://ses_wire/one",
                        "resultDigest": "c" * 64,
                        "safeMessage": "Applied.",
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "receipt": {
                    "outcome": "indeterminate",
                    "safeMessage": "Unknown.",
                }
            },
        )

    bridge = DesktopBrowserEffectBridge(
        broker_url="http://127.0.0.1:54321",
        broker_token="local-secret",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    prepared = await bridge.prepare_action(_plan())
    applied = await bridge.apply_prepared(prepared.prepared_ref or "")
    reconciled = await bridge.reconcile_action(prepared.prepared_ref or "")

    assert prepared.observed_precondition_digest == _plan().precondition_digest
    assert applied.outcome is BrowserApplyOutcome.APPLIED
    assert applied.receipt_ref == "browser-receipt://ses_wire/one"
    assert applied.result_digest == "c" * 64
    assert reconciled.outcome is BrowserApplyOutcome.INDETERMINATE
    assert [path.rsplit("/", 1)[-1] for path, _ in seen] == [
        "prepare",
        "apply",
        "reconcile",
    ]
    assert "local-secret" not in repr(bridge)


async def test_private_bridge_omits_optional_form_fields_for_exact_click() -> None:
    click_precondition = BrowserPrecondition(
        page_generation=7,
        origin="https://example.com",
        element_fingerprint="a" * 64,
    )
    click = _plan().model_copy(
        update={
            "action_kind": BrowserActionKind.CLICK,
            "form_fingerprint": None,
            "form_payload_digest": None,
            "form_action_url": None,
            "method": None,
            "precondition": click_precondition,
            "precondition_digest": click_precondition.digest,
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        plan = body["plan"]
        assert "formFingerprint" not in plan
        assert "formPayloadDigest" not in plan
        assert "formActionUrl" not in plan
        assert "method" not in plan
        assert "formFingerprint" not in plan["precondition"]
        assert "formPayloadDigest" not in plan["precondition"]
        assert all(value is not None for value in plan.values())
        return httpx.Response(
            200,
            json={
                "prepared": {
                    "preparedRef": "browser-prepared://ses_wire/click",
                    "observedPreconditionDigest": click.precondition_digest,
                    "preconditionDrift": False,
                }
            },
        )

    bridge = DesktopBrowserEffectBridge(
        broker_url="http://127.0.0.1:54321",
        broker_token="local-secret",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    prepared = await bridge.prepare_action(click)

    assert prepared.prepared_ref == "browser-prepared://ses_wire/click"


async def test_private_bridge_fails_closed_on_auth_or_malformed_receipt() -> None:
    unauthorized = DesktopBrowserEffectBridge(
        broker_url="http://127.0.0.1:54321",
        broker_token="local-secret",
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(401, json={"error": "no"})
            )
        ),
    )
    with pytest.raises(BrowserOperationError):
        await unauthorized.prepare_action(_plan())

    malformed = DesktopBrowserEffectBridge(
        broker_url="http://127.0.0.1:54321",
        broker_token="local-secret",
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={"receipt": {"outcome": "applied", "cookie": "secret"}},
                )
            )
        ),
    )
    with pytest.raises(BrowserOperationError):
        await malformed.apply_prepared("browser-prepared://ses_wire/one")
