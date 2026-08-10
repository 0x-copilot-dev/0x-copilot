"""Operation-Gateway adapter for the Electron-owned desktop browser.

This module is intentionally not a generic browser client.  Reads cross the
private bridge once after A3 classification.  Every non-read browser action
becomes an exact :class:`BrowserActionPlan` and goes to A4/A5 staging; the
adapter has no method that can click, submit, or upload.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

from agent_runtime.capabilities.browser.contracts import (
    BrowserActionKind,
    BrowserActionPlan,
    BrowserOperationError,
    BrowserPrecondition,
    BrowserReadBridge,
    BrowserReadRequest,
    BrowserStagePort,
    BrowserUploadArtifact,
    BrowserUploadAuthorizer,
    artifact_publication_from_payload,
    read_result_from_browser,
)
from agent_runtime.capabilities.operations.context import OperationContext
from agent_runtime.capabilities.operations.contracts import (
    ArtifactPublicationSource,
    OperationRawResult,
    ProposedEffect,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_bytes, sha256_hex
from agent_runtime.surfaces_v2.entities import OperationRequest


_READ_OPS: Final[frozenset[str]] = frozenset(
    {
        "browser_navigate",
        "browser_snapshot",
        "browser_wait",
        "browser_screenshot",
        "browser_close",
        # A download produces an internal artifact only. It is never a host
        # filesystem write and it has no generic browser side-effect path.
        "browser_download",
    }
)

_STAGED_ACTIONS: Final[dict[str, BrowserActionKind]] = {
    # Unknown clicks stay held. A model cannot assert that a click is a read.
    "browser_click": BrowserActionKind.CLICK,
    "browser_type": BrowserActionKind.INPUT,
    "browser_select": BrowserActionKind.SELECT,
    "browser_submit": BrowserActionKind.SUBMIT,
    "browser_upload_submit": BrowserActionKind.UPLOAD_SUBMIT,
}


@dataclass
class BrowserOperationAdapter:
    """A3 operation adapter with an intentionally one-way consequential path."""

    stager: BrowserStagePort
    bridge: BrowserReadBridge | None = None
    upload_authorizer: BrowserUploadAuthorizer | None = None
    _artifact_payloads: dict[str, ArtifactPublicationSource] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    async def execute_read(self, request: OperationRequest) -> OperationRawResult:
        """Run one classified browser read or internal artifact capture once."""

        if request.op not in _READ_OPS:
            raise BrowserOperationError(
                "browser operation must be staged before dispatch"
            )
        if self.bridge is None:
            raise BrowserOperationError("browser read bridge is unavailable")
        arguments = self._arguments(request)
        if (
            request.op in {"browser_download", "browser_screenshot"}
            and request.artifact_intent is None
        ):
            # A screenshot is activity-only by default; explicit publication is
            # required. Downloads must be captured as artifacts rather than a
            # transient host file, so reject an unpublishable download.
            if request.op == "browser_download":
                raise BrowserOperationError(
                    "browser download requires an artifact intent"
                )
        result = await self.bridge.execute_read(
            BrowserReadRequest(
                operation_id=request.operation_id,
                run_id=request.run_id,
                op=request.op,
                arguments=arguments,
            )
        )
        if request.artifact_intent is not None:
            payload = await self.bridge.artifact_payload(
                operation_id=request.operation_id
            )
            if payload is not None:
                # The A3 gateway owns publication. Retain at most the single
                # opaque hand-off for this invocation and consume it below.
                self._artifact_payloads[request.operation_id] = (
                    artifact_publication_from_payload(payload)
                )
        return read_result_from_browser(result)

    async def build_proposal(self, request: OperationRequest) -> ProposedEffect:
        """Create an exact held action plan with zero browser dispatch."""

        action_kind = _STAGED_ACTIONS.get(request.op)
        if action_kind is None:
            # Unknown operations must not silently fall back to a generic click
            # or a permissive bridge route.
            raise BrowserOperationError("unknown browser action is held")
        plan = await self._plan(request=request, action_kind=action_kind)
        return await self.stager.stage(request=request, plan=plan)

    async def artifact_publication(
        self, request: OperationRequest
    ) -> ArtifactPublicationSource | None:
        """Consume the exact private bytes only when A3 is publishing them."""

        return self._artifact_payloads.pop(request.operation_id, None)

    @staticmethod
    def _arguments(request: OperationRequest) -> dict[str, object]:
        context = OperationContext.require()
        stored = context.arguments.get(request.canonical_args_ref)
        if stored is None:
            raise BrowserOperationError("browser arguments are unavailable")
        digest, raw = stored
        if digest != request.args_digest or sha256_hex(raw) != request.args_digest:
            raise BrowserOperationError("browser argument digest does not match")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BrowserOperationError("browser arguments are invalid") from exc
        if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
            raise BrowserOperationError("browser arguments are not canonical")
        return value

    async def _plan(
        self,
        *,
        request: OperationRequest,
        action_kind: BrowserActionKind,
    ) -> BrowserActionPlan:
        args = self._arguments(request)
        session_ref = self._required_string(args, "sessionRef", "session_ref")
        page_ref = self._required_string(args, "pageRef", "page_ref")
        origin = self._required_string(args, "origin")
        top_level_origin = self._required_string(
            args, "topLevelOrigin", "top_level_origin"
        )
        element_ref = self._required_string(args, "elementRef", "element_ref")
        element_fingerprint = self._required_string(
            args, "elementFingerprint", "element_fingerprint"
        )
        generation = self._required_nonnegative_int(
            args, "pageGeneration", "page_generation"
        )
        form_fingerprint = self._optional_string(
            args, "formFingerprint", "form_fingerprint"
        )
        form_payload_digest = self._optional_string(
            args, "formPayloadDigest", "form_payload_digest"
        )
        form_action_url = self._optional_string(
            args, "formActionUrl", "form_action_url"
        )
        method = self._optional_string(args, "method")
        upload_refs = self._artifact_refs(
            self._aliased_value(
                args,
                "uploadArtifactRefs",
                "upload_artifact_refs",
            )
        )
        uploads = await self._authorize_uploads(
            request=request,
            action_kind=action_kind,
            artifact_refs=upload_refs,
        )

        return BrowserActionPlan(
            session_ref=session_ref,
            page_ref=page_ref,
            origin=origin,
            top_level_origin=top_level_origin,
            action_kind=action_kind,
            element_ref=element_ref,
            element_fingerprint=element_fingerprint,
            form_fingerprint=form_fingerprint,
            form_payload_digest=form_payload_digest,
            form_action_url=form_action_url,
            method=method,
            # Exact field values remain behind the A3 durable canonical-args
            # resolver. The public card sees only the digest and safe summary.
            canonical_fields_ref=request.canonical_args_ref,
            fields_digest=request.args_digest,
            upload_artifact_refs=upload_refs,
            upload_artifacts=uploads,
            precondition=(
                precondition := BrowserPrecondition(
                    page_generation=generation,
                    origin=origin,
                    element_fingerprint=element_fingerprint,
                    form_fingerprint=form_fingerprint,
                    form_payload_digest=form_payload_digest,
                )
            ),
            precondition_digest=precondition.digest,
            user_visible_summary=(
                f"Review browser {action_kind.value.replace('_', ' ')} on {origin}."
            ),
        )

    @staticmethod
    def _required_string(
        args: Mapping[str, object],
        key: str,
        legacy_key: str | None = None,
    ) -> str:
        value = BrowserOperationAdapter._aliased_value(args, key, legacy_key)
        if not isinstance(value, str) or not value:
            raise BrowserOperationError(
                f"browser action is missing {legacy_key or key}"
            )
        return value

    @staticmethod
    def _optional_string(
        args: Mapping[str, object],
        key: str,
        legacy_key: str | None = None,
    ) -> str | None:
        value = BrowserOperationAdapter._aliased_value(args, key, legacy_key)
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            raise BrowserOperationError(f"browser action has invalid {key}")
        return value

    @staticmethod
    def _required_nonnegative_int(
        args: Mapping[str, object],
        key: str,
        legacy_key: str | None = None,
    ) -> int:
        value = BrowserOperationAdapter._aliased_value(args, key, legacy_key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise BrowserOperationError(f"browser action has invalid {key}")
        return value

    @staticmethod
    def _aliased_value(
        args: Mapping[str, object],
        key: str,
        legacy_key: str | None,
    ) -> object:
        if legacy_key is None:
            return args.get(key)
        if key in args and legacy_key in args:
            raise BrowserOperationError(
                f"browser action cannot supply both {key} and {legacy_key}"
            )
        return args.get(key) if key in args else args.get(legacy_key)

    @staticmethod
    def _artifact_refs(value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise BrowserOperationError(
                "browser upload artifact references are invalid"
            )
        return tuple(value)

    async def _authorize_uploads(
        self,
        *,
        request: OperationRequest,
        action_kind: BrowserActionKind,
        artifact_refs: tuple[str, ...],
    ) -> tuple[BrowserUploadArtifact, ...]:
        if action_kind is not BrowserActionKind.UPLOAD_SUBMIT:
            if artifact_refs:
                raise BrowserOperationError(
                    "browser upload sources require an upload_submit action"
                )
            return ()
        if not artifact_refs:
            raise BrowserOperationError("browser upload requires artifact revisions")
        if self.upload_authorizer is None:
            raise BrowserOperationError("browser upload authorization is unavailable")
        uploads = await self.upload_authorizer.authorize(
            request=request,
            artifact_refs=artifact_refs,
        )
        if tuple(upload.artifact_ref for upload in uploads) != artifact_refs:
            raise BrowserOperationError(
                "browser upload authorization did not bind the requested revisions"
            )
        return uploads


def is_browser_read_operation(op: str) -> bool:
    return op in _READ_OPS


def is_staged_browser_action(op: str) -> bool:
    return op in _STAGED_ACTIONS


__all__ = (
    "BrowserOperationAdapter",
    "is_browser_read_operation",
    "is_staged_browser_action",
)
