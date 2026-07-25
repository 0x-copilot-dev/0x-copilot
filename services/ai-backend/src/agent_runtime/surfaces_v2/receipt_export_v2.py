"""Bounded, safe ReceiptExportV2 bundle and offline verifier (PRD-E1 D7).

The legacy receipt export intentionally remains readable at
``receipt_export.py``.  This additive format never serializes a ledger event's
raw payload: each canonical row carries a digest of that payload and a narrow,
receipt-relevant safe projection.  The final synthetic row carries the D4
``RunReceiptV2`` fold.  All rows are HMAC hash-chained through the shared audit
chain primitive, so the verifier can run offline and detect edits, omissions,
reordering, unknown signing keys, and a terminal receipt that does not match
the signed safe facts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import hashlib
import json
import re
from typing import Literal, Protocol, runtime_checkable

from copilot_audit_chain import (
    AuditChainRow,
    AuditChainSigner,
    ChainVerificationResult,
)

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.ledger_ids import (
    ArtifactContentRefCodec,
    ProposalUriCodec,
)
from agent_runtime.surfaces_v2.ledger_models import (
    LedgerEventType,
    WorkLedgerVocabulary,
)
from agent_runtime.surfaces_v2.receipt_export import ReceiptExportVerifier
from agent_runtime.surfaces_v2.receipt_v2 import (
    ReceiptFoldV2,
    ReceiptRunStatusV2,
    RunReceiptV2,
)


class ReceiptExportRefClassV2(StrEnum):
    """What was intentionally omitted from a row's safe projection."""

    NONE = "none"
    OPAQUE_REFERENCE = "opaque_reference"
    PRIVATE_BODY_OMITTED = "private_body_omitted"


class ReceiptExportV2Row(RuntimeContract):
    """One safe, signed export row.  ``safe_payload`` never holds raw bodies."""

    sequence_no: int
    event_type: str
    created_at: str
    payload_digest: str
    safe_payload: dict[str, object]
    ref_class: ReceiptExportRefClassV2
    prev_hash: str | None
    signature: str
    key_id: str
    key_version: int


class ReceiptExportV2(RuntimeContract):
    """Versioned D7 receipt bundle with a terminal ``receipt.v2`` row."""

    bundle_version: Literal[2] = 2
    run_id: str
    generated_at: str
    key_id: str
    rows: tuple[ReceiptExportV2Row, ...]
    row_count: int
    receipt_digest: str
    head_hash: str


@runtime_checkable
class _ExportEventLike(Protocol):
    """Envelope-lite source shape; keeps this pure module below runtime_api."""

    event_type: object
    sequence_no: object
    created_at: object
    payload: object


@dataclass(frozen=True)
class _CanonicalEvent:
    sequence_no: int
    index: int
    event_type: LedgerEventType
    created_at: str
    payload: Mapping[str, object] | None


class _Values:
    BUNDLE_VERSION = 2
    TERMINAL_EVENT_TYPE = "receipt.v2"
    KEY_ID_PREFIX = "audit-hmac:v"
    SAFE_FIELD_VALID = "valid"
    SAFE_FIELD_GATE_TOKEN = "gate_token"
    ZERO_DIGEST = "0" * 64
    SAFE_OPERATION_ID = "op_00000000-0000-4000-8000-000000000000"
    SAFE_STAGE_ID = "stg_00000000-0000-4000-8000-000000000000"
    SAFE_ARTIFACT_ID = "art_00000000-0000-4000-8000-000000000000"
    SAFE_REFERENCE = "safe://redacted/item"
    SAFE_CLAIM_ID = "redacted"
    SAFE_TEXT = "redacted"


class _Keys:
    BUNDLE_VERSION = "bundle_version"
    RUN_ID = "run_id"
    GENERATED_AT = "generated_at"
    KEY_ID = "key_id"
    ROWS = "rows"
    ROW_COUNT = "row_count"
    RECEIPT_DIGEST = "receipt_digest"
    HEAD_HASH = "head_hash"

    SEQUENCE_NO = "sequence_no"
    EVENT_TYPE = "event_type"
    CREATED_AT = "created_at"
    PAYLOAD_DIGEST = "payload_digest"
    SAFE_PAYLOAD = "safe_payload"
    REF_CLASS = "ref_class"
    PREV_HASH = "prev_hash"
    SIGNATURE = "signature"
    KEY_VERSION = "key_version"


_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_SAFE_INTEGER = (1 << 53) - 1
_PRIVATE_BODY_KEYS = frozenset(
    {
        "agent_holds",
        "args",
        "authorization",
        "body",
        "changes",
        "cookie",
        "cookies",
        "detail",
        "display_target",
        "path",
        "raw_args",
        "reason",
        "rowset",
        "secret",
        "target_args",
        "token",
        "title",
    }
)


class _SigningCodec:
    """The exact, public signing payload for a v2 export row."""

    @staticmethod
    def payload(
        *,
        bundle_version: object,
        run_id: object,
        sequence_no: object,
        event_type: object,
        created_at: object,
        payload_digest: object,
        safe_payload: object,
        ref_class: object,
        key_id: object,
    ) -> dict[str, object]:
        return {
            _Keys.BUNDLE_VERSION: bundle_version,
            _Keys.RUN_ID: run_id,
            _Keys.SEQUENCE_NO: sequence_no,
            _Keys.EVENT_TYPE: event_type,
            _Keys.CREATED_AT: created_at,
            _Keys.PAYLOAD_DIGEST: payload_digest,
            _Keys.SAFE_PAYLOAD: safe_payload,
            _Keys.REF_CLASS: ref_class,
            _Keys.KEY_ID: key_id,
        }


class _SafePayloadProjector:
    """Whitelist only the facts needed to refold D4 without raw event bodies."""

    @classmethod
    def project(
        cls,
        *,
        event_type: LedgerEventType,
        payload: Mapping[str, object] | None,
    ) -> tuple[dict[str, object], ReceiptExportRefClassV2]:
        if payload is None:
            return ({_Values.SAFE_FIELD_VALID: False}, ReceiptExportRefClassV2.NONE)
        omitted = cls._ref_class(payload)
        try:
            normalized = WorkLedgerVocabulary.validate_payload(
                event_type.value, payload
            ).model_dump(mode="json")
        except Exception:  # noqa: BLE001 - export stays total over corrupt history
            return ({_Values.SAFE_FIELD_VALID: False}, omitted)

        safe: dict[str, object] = {_Values.SAFE_FIELD_VALID: True}
        if event_type is LedgerEventType.OPERATION_CLASSIFIED:
            cls._copy(safe, normalized, "operation_id", "effect_class")
        elif event_type is LedgerEventType.OPERATION_COMPLETED:
            cls._copy(safe, normalized, "outcome")
        elif event_type is LedgerEventType.EFFECT_STAGED:
            cls._copy(safe, normalized, "stage_id", "operation_id", "effect_class")
        elif event_type is LedgerEventType.WRITE_STAGED:
            cls._copy(safe, normalized, "stage_id")
        elif event_type in {
            LedgerEventType.EFFECT_DECISION_RECORDED,
            LedgerEventType.DECISION_RECORDED,
        }:
            cls._copy(safe, normalized, "stage_id", "decision")
        elif event_type in {
            LedgerEventType.EFFECT_CLAIMED,
            LedgerEventType.EFFECT_INDETERMINATE,
        }:
            cls._copy(safe, normalized, "stage_id")
        elif event_type in {
            LedgerEventType.EFFECT_APPLIED,
            LedgerEventType.EFFECT_RECONCILED,
        }:
            cls._copy(safe, normalized, "stage_id", "outcome")
        elif event_type is LedgerEventType.WRITE_APPLIED:
            cls._copy(safe, normalized, "stage_id", "result")
        elif event_type in {
            LedgerEventType.GATE_OPENED,
            LedgerEventType.GATE_RESOLVED,
            LedgerEventType.GATE_OPENED_V2,
            LedgerEventType.GATE_RESOLVED_V2,
        }:
            gate_id = normalized.get("gate_id")
            if isinstance(gate_id, str):
                safe[_Values.SAFE_FIELD_GATE_TOKEN] = cls._opaque_token(gate_id)
        elif event_type is LedgerEventType.USAGE_RECORDED:
            cls._copy(safe, normalized, "purpose", "tokens_in", "tokens_out")

        return safe, cls._most_restrictive(omitted, cls._ref_class(normalized))

    @staticmethod
    def _copy(
        target: dict[str, object], source: Mapping[str, object], *keys: str
    ) -> None:
        for key in keys:
            if key in source and source[key] is not None:
                target[key] = source[key]

    @staticmethod
    def _opaque_token(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _ref_class(payload: Mapping[str, object]) -> ReceiptExportRefClassV2:
        if any(key in payload for key in _PRIVATE_BODY_KEYS):
            return ReceiptExportRefClassV2.PRIVATE_BODY_OMITTED
        if any(key.endswith("_ref") for key in payload):
            return ReceiptExportRefClassV2.OPAQUE_REFERENCE
        return ReceiptExportRefClassV2.NONE

    @staticmethod
    def _most_restrictive(
        first: ReceiptExportRefClassV2,
        second: ReceiptExportRefClassV2,
    ) -> ReceiptExportRefClassV2:
        if ReceiptExportRefClassV2.PRIVATE_BODY_OMITTED in {first, second}:
            return ReceiptExportRefClassV2.PRIVATE_BODY_OMITTED
        if ReceiptExportRefClassV2.OPAQUE_REFERENCE in {first, second}:
            return ReceiptExportRefClassV2.OPAQUE_REFERENCE
        return ReceiptExportRefClassV2.NONE


class _SafeReceiptRehydrator:
    """Rebuilds validator-safe, body-free D4 inputs from a signed projection."""

    @classmethod
    def fold(
        cls,
        *,
        run_id: str,
        rows: Sequence[Mapping[str, object]],
        status: ReceiptRunStatusV2,
    ) -> RunReceiptV2:
        raw_events: list[dict[str, object]] = []
        last_sequence = 0
        generated_at = ""
        for raw in rows:
            event_type = LedgerEventType(raw[_Keys.EVENT_TYPE])
            sequence_no = int(raw[_Keys.SEQUENCE_NO])
            created_at = raw[_Keys.CREATED_AT]
            if isinstance(created_at, str):
                generated_at = created_at
            last_sequence = sequence_no
            safe_payload = raw[_Keys.SAFE_PAYLOAD]
            payload = cls._payload_for(
                event_type,
                safe_payload if isinstance(safe_payload, Mapping) else {},
            )
            if payload is not None:
                raw_events.append(
                    {
                        _Keys.EVENT_TYPE: event_type.value,
                        _Keys.SEQUENCE_NO: sequence_no,
                        _Keys.CREATED_AT: created_at,
                        "payload": payload,
                    }
                )
        folded = ReceiptFoldV2.fold_raw(
            run_id=run_id,
            events=raw_events,
            run_status=status,
        )
        return folded.model_copy(
            update={
                "generated_at": generated_at,
                "fold_ref": f"ledger://{run_id}@{last_sequence}",
            }
        )

    @classmethod
    def _payload_for(
        cls, event_type: LedgerEventType, safe: Mapping[str, object]
    ) -> dict[str, object] | None:
        if safe.get(_Values.SAFE_FIELD_VALID) is not True:
            # A same-type invalid payload preserves D4's one malformed-row warning.
            return {}
        if event_type is LedgerEventType.OPERATION_REQUESTED:
            return {
                "v": 1,
                "operation_id": _Values.SAFE_OPERATION_ID,
                "producer": "system",
                "capability": _Values.SAFE_TEXT,
                "op": _Values.SAFE_TEXT,
                "args_digest": _Values.ZERO_DIGEST,
                "parent_operation_id": None,
            }
        if event_type is LedgerEventType.OPERATION_CLASSIFIED:
            return {
                "v": 1,
                "operation_id": safe.get("operation_id"),
                "effect_class": safe.get("effect_class"),
                "basis": "default",
                "confidence": 0.0,
            }
        if event_type is LedgerEventType.OPERATION_COMPLETED:
            return {
                "v": 1,
                "operation_id": _Values.SAFE_OPERATION_ID,
                "outcome": safe.get("outcome"),
                "result_ref": None,
                "latency_ms": None,
            }
        if event_type is LedgerEventType.OPERATION_FAILED:
            return {
                "v": 1,
                "operation_id": _Values.SAFE_OPERATION_ID,
                "failure_code": _Values.SAFE_TEXT,
                "retryable": False,
            }
        if event_type is LedgerEventType.READ_EXECUTED:
            return {
                "v": 1,
                "call_id": _Values.SAFE_TEXT,
                "connector": _Values.SAFE_TEXT,
                "op": _Values.SAFE_TEXT,
                "latency_ms": 0,
                "payload_ref": _Values.SAFE_TEXT,
            }
        if event_type is LedgerEventType.ARTIFACT_CREATED:
            return {
                "v": 1,
                "artifact_id": _Values.SAFE_ARTIFACT_ID,
                "kind": "document",
                "revision": 1,
                "content_ref": ArtifactContentRefCodec.format(
                    _Values.SAFE_ARTIFACT_ID, 1
                ),
                "content_digest": _Values.ZERO_DIGEST,
                "author": "system",
            }
        if event_type is LedgerEventType.ARTIFACT_REVISED:
            return {
                "v": 1,
                "artifact_id": _Values.SAFE_ARTIFACT_ID,
                "revision": 2,
                "parent_revision": 1,
                "content_ref": ArtifactContentRefCodec.format(
                    _Values.SAFE_ARTIFACT_ID, 2
                ),
                "content_digest": _Values.ZERO_DIGEST,
                "author": "system",
            }
        if event_type is LedgerEventType.ARTIFACT_PROMOTED:
            return {
                "v": 1,
                "artifact_id": _Values.SAFE_ARTIFACT_ID,
                "source_ref": _Values.SAFE_REFERENCE,
                "kind": "document",
                "revision": 1,
            }
        if event_type is LedgerEventType.EFFECT_STAGED:
            return {
                "v": 1,
                "stage_id": safe.get("stage_id"),
                "operation_id": safe.get("operation_id"),
                "executor": "builtin",
                "target_ref": _Values.SAFE_REFERENCE,
                "target_digest": _Values.ZERO_DIGEST,
                "proposal_ref": ProposalUriCodec.format(str(safe.get("stage_id")), 1),
                "proposal_digest": _Values.ZERO_DIGEST,
                "policy": "ask",
                "effect_class": safe.get("effect_class"),
            }
        if event_type is LedgerEventType.WRITE_STAGED:
            return {
                "v": 1,
                "stage_id": safe.get("stage_id"),
                "surface_id": _Values.SAFE_TEXT,
                "target": {"connector": _Values.SAFE_TEXT, "op": _Values.SAFE_TEXT},
                "proposal_ref": _Values.SAFE_TEXT,
                "rows": None,
                "agent_holds": None,
            }
        if event_type is LedgerEventType.EFFECT_DECISION_RECORDED:
            return {
                "v": 1,
                "stage_id": safe.get("stage_id"),
                "revision": 1,
                "decision": safe.get("decision"),
                "actor": "system",
                "proposal_digest": _Values.ZERO_DIGEST,
                "target_digest": _Values.ZERO_DIGEST,
                "actor_ref": None,
                "decided_at": None,
            }
        if event_type is LedgerEventType.DECISION_RECORDED:
            return {
                "v": 1,
                "stage_id": safe.get("stage_id"),
                "decision": safe.get("decision"),
                "scope": {"rev": 1},
                "actor": "user",
                "apply": None,
            }
        if event_type is LedgerEventType.EFFECT_CLAIMED:
            return {
                "v": 1,
                "stage_id": safe.get("stage_id"),
                "revision": 1,
                "claim_id": _Values.SAFE_CLAIM_ID,
                "executor": "builtin",
                "attempt": 1,
            }
        if event_type in {
            LedgerEventType.EFFECT_APPLIED,
            LedgerEventType.EFFECT_RECONCILED,
        }:
            payload: dict[str, object] = {
                "v": 1,
                "stage_id": safe.get("stage_id"),
                "revision": 1,
                "outcome": safe.get("outcome"),
                "receipt_ref": None,
            }
            if event_type is LedgerEventType.EFFECT_RECONCILED:
                payload["claim_id"] = _Values.SAFE_CLAIM_ID
            else:
                payload["result_digest"] = None
            return payload
        if event_type is LedgerEventType.WRITE_APPLIED:
            return {
                "v": 1,
                "stage_id": safe.get("stage_id"),
                "rev": 1,
                "result": safe.get("result"),
                "row_keys": None,
                "connector_receipt_ref": None,
                "failure": None,
                "decided_by": None,
                "row_results": None,
            }
        if event_type is LedgerEventType.EFFECT_INDETERMINATE:
            return {
                "v": 1,
                "stage_id": safe.get("stage_id"),
                "revision": 1,
                "claim_id": _Values.SAFE_CLAIM_ID,
                "reason": _Values.SAFE_TEXT,
            }
        if event_type is LedgerEventType.GATE_OPENED:
            return {
                "v": 1,
                "gate_id": safe.get(_Values.SAFE_FIELD_GATE_TOKEN),
                "connector": _Values.SAFE_TEXT,
                "purpose": _Values.SAFE_TEXT,
                "scopes": (),
                "auth_state": "missing",
            }
        if event_type is LedgerEventType.GATE_RESOLVED:
            return {
                "v": 1,
                "gate_id": safe.get(_Values.SAFE_FIELD_GATE_TOKEN),
                "outcome": "connected",
                "write_policy": None,
            }
        if event_type is LedgerEventType.GATE_OPENED_V2:
            return {
                "v": 1,
                "gate_id": safe.get(_Values.SAFE_FIELD_GATE_TOKEN),
                "operation_id": _Values.SAFE_OPERATION_ID,
                "gate_kind": "policy",
                "capability": _Values.SAFE_TEXT,
                "reason": _Values.SAFE_TEXT,
            }
        if event_type is LedgerEventType.GATE_RESOLVED_V2:
            return {
                "v": 1,
                "gate_id": safe.get(_Values.SAFE_FIELD_GATE_TOKEN),
                "decision": "granted",
                "actor": "system",
            }
        if event_type is LedgerEventType.USAGE_RECORDED:
            return {
                "v": 1,
                "purpose": safe.get("purpose"),
                "model": _Values.SAFE_TEXT,
                "tokens_in": safe.get("tokens_in"),
                "tokens_out": safe.get("tokens_out"),
                "surface_id": None,
            }
        # Valid rows that do not affect D4's counters are intentionally omitted.
        return None


class ReceiptExportV2Builder:
    """Build a signed, redacted D7 bundle from canonical ledger events."""

    def __init__(self, *, signer: AuditChainSigner) -> None:
        self._signer = signer

    def build(
        self,
        *,
        run_id: str,
        events: Sequence[_ExportEventLike | Mapping[str, object]],
        run_status: object | None = None,
    ) -> ReceiptExportV2:
        canonical = self._canonical_events(events)
        receipt = ReceiptFoldV2.fold_raw(
            run_id=run_id,
            events=[
                {
                    _Keys.EVENT_TYPE: row.event_type.value,
                    _Keys.SEQUENCE_NO: row.sequence_no,
                    _Keys.CREATED_AT: row.created_at,
                    "payload": row.payload,
                }
                for row in canonical
            ],
            run_status=run_status,
        )
        key_id = self._key_id(self._signer.active_version)
        rows: list[ReceiptExportV2Row] = []
        previous_signature: bytes | None = None
        for row in canonical:
            safe_payload, ref_class = _SafePayloadProjector.project(
                event_type=row.event_type,
                payload=row.payload,
            )
            signed, previous_signature = self._sign_row(
                run_id=run_id,
                sequence_no=row.sequence_no,
                event_type=row.event_type.value,
                created_at=row.created_at,
                payload_digest=self._digest(row.payload),
                safe_payload=safe_payload,
                ref_class=ref_class,
                key_id=key_id,
                previous_signature=previous_signature,
            )
            rows.append(signed)

        receipt_payload = receipt.model_dump(mode="json")
        receipt_digest = self._digest(receipt_payload)
        terminal_sequence = canonical[-1].sequence_no + 1 if canonical else 1
        terminal, previous_signature = self._sign_row(
            run_id=run_id,
            sequence_no=terminal_sequence,
            event_type=_Values.TERMINAL_EVENT_TYPE,
            created_at=receipt.generated_at,
            payload_digest=receipt_digest,
            safe_payload=receipt_payload,
            ref_class=ReceiptExportRefClassV2.NONE,
            key_id=key_id,
            previous_signature=previous_signature,
        )
        rows.append(terminal)
        return ReceiptExportV2(
            run_id=run_id,
            generated_at=receipt.generated_at,
            key_id=key_id,
            rows=tuple(rows),
            row_count=len(rows),
            receipt_digest=receipt_digest,
            head_hash=terminal.signature,
        )

    def _sign_row(
        self,
        *,
        run_id: str,
        sequence_no: int,
        event_type: str,
        created_at: str,
        payload_digest: str,
        safe_payload: dict[str, object],
        ref_class: ReceiptExportRefClassV2,
        key_id: str,
        previous_signature: bytes | None,
    ) -> tuple[ReceiptExportV2Row, bytes]:
        payload = _SigningCodec.payload(
            bundle_version=_Values.BUNDLE_VERSION,
            run_id=run_id,
            sequence_no=sequence_no,
            event_type=event_type,
            created_at=created_at,
            payload_digest=payload_digest,
            safe_payload=safe_payload,
            ref_class=ref_class.value,
            key_id=key_id,
        )
        chain_signature = self._signer.sign(
            prev_hash=previous_signature,
            payload=payload,
        )
        return (
            ReceiptExportV2Row(
                sequence_no=sequence_no,
                event_type=event_type,
                created_at=created_at,
                payload_digest=payload_digest,
                safe_payload=safe_payload,
                ref_class=ref_class,
                prev_hash=(
                    chain_signature.prev_hash.hex()
                    if chain_signature.prev_hash is not None
                    else None
                ),
                signature=chain_signature.signature.hex(),
                key_id=key_id,
                key_version=chain_signature.key_version,
            ),
            chain_signature.signature,
        )

    @classmethod
    def _canonical_events(
        cls, events: Sequence[_ExportEventLike | Mapping[str, object]]
    ) -> list[_CanonicalEvent]:
        rows: list[_CanonicalEvent] = []
        for index, event in enumerate(events):
            try:
                raw_event_type = cls._field(event, _Keys.EVENT_TYPE)
                raw_type = getattr(raw_event_type, "value", raw_event_type)
                event_type = LedgerEventType(raw_type)
                sequence_no = cls._positive_int(cls._field(event, _Keys.SEQUENCE_NO))
                if sequence_no is None:
                    continue
                payload = cls._field(event, "payload")
                rows.append(
                    _CanonicalEvent(
                        sequence_no=sequence_no,
                        index=index,
                        event_type=event_type,
                        created_at=cls._timestamp(cls._field(event, _Keys.CREATED_AT)),
                        payload=payload if isinstance(payload, Mapping) else None,
                    )
                )
            except Exception:  # noqa: BLE001 - unknown envelopes are never exported
                continue
        return sorted(rows, key=lambda row: (row.sequence_no, row.index))

    @staticmethod
    def _field(event: _ExportEventLike | Mapping[str, object], name: str) -> object:
        if isinstance(event, Mapping):
            return event.get(name)
        return getattr(event, name, None)

    @staticmethod
    def _positive_int(value: object) -> int | None:
        return (
            value
            if isinstance(value, int)
            and not isinstance(value, bool)
            and 1 <= value <= _MAX_SAFE_INTEGER
            else None
        )

    @staticmethod
    def _timestamp(value: object) -> str:
        if isinstance(value, datetime):
            value = value.isoformat()
        return value if isinstance(value, str) and _TIMESTAMP.fullmatch(value) else ""

    @staticmethod
    def _digest(value: object) -> str:
        try:
            canonical = json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                default=ReceiptExportV2Builder._json_default,
            )
        except (TypeError, ValueError):
            canonical = '"unserializable"'
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _json_default(value: object) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        raw = getattr(value, "value", None)
        if isinstance(raw, str):
            return raw
        return type(value).__name__

    @staticmethod
    def _key_id(key_version: int) -> str:
        return f"{_Values.KEY_ID_PREFIX}{key_version}"


class ReceiptExportV2Verifier:
    """Offline verifier for v2 bundles, with a permanent v1 compatibility path."""

    def __init__(self, *, signer: AuditChainSigner) -> None:
        self._signer = signer

    def verify(self, bundle: Mapping[str, object] | object) -> ChainVerificationResult:
        if not isinstance(bundle, Mapping):
            return self._failure("bundle malformed")
        if bundle.get("export_version") == 1 or bundle.get(_Keys.BUNDLE_VERSION) == 1:
            return self._verify_legacy(bundle)
        if bundle.get(_Keys.BUNDLE_VERSION) != _Values.BUNDLE_VERSION:
            return self._failure("unsupported bundle version")
        try:
            return self._verify_v2(bundle)
        except Exception:  # noqa: BLE001 - untrusted offline bundles are total
            return self._failure("bundle malformed")

    def _verify_v2(self, bundle: Mapping[str, object]) -> ChainVerificationResult:
        run_id = bundle.get(_Keys.RUN_ID)
        generated_at = bundle.get(_Keys.GENERATED_AT)
        key_id = bundle.get(_Keys.KEY_ID)
        raw_rows = bundle.get(_Keys.ROWS)
        if (
            not isinstance(run_id, str)
            or not isinstance(generated_at, str)
            or not isinstance(key_id, str)
            or not self._is_row_sequence(raw_rows)
            or not raw_rows
        ):
            return self._failure("bundle malformed")
        if bundle.get(_Keys.ROW_COUNT) != len(raw_rows):
            return self._failure("row count mismatch")

        chain_rows: list[AuditChainRow] = []
        canonical_rows: list[Mapping[str, object]] = []
        previous_sequence = 0
        terminal_row: Mapping[str, object] | None = None
        for index, raw in enumerate(raw_rows):
            if not isinstance(raw, Mapping):
                return self._failure("row malformed", index + 1)
            parsed = self._parse_row(raw, run_id=run_id, index=index)
            if parsed is None:
                return self._failure("row malformed", index + 1)
            row, chain_row = parsed
            sequence_no = row[_Keys.SEQUENCE_NO]
            # Runtime sequences are normally unique, but a corrupt historical
            # prefix may repeat one. Preserve that signed fact in stable input
            # order; only a decreasing sequence is a reordered export.
            if not isinstance(sequence_no, int) or sequence_no < previous_sequence:
                return self._failure("row order mismatch", index + 1)
            previous_sequence = sequence_no
            is_terminal = row[_Keys.EVENT_TYPE] == _Values.TERMINAL_EVENT_TYPE
            if index == len(raw_rows) - 1:
                if not is_terminal:
                    return self._failure("terminal receipt missing", index + 1)
                terminal_row = row
            elif is_terminal:
                return self._failure("terminal receipt order", index + 1)
            else:
                try:
                    LedgerEventType(row[_Keys.EVENT_TYPE])
                except ValueError:
                    return self._failure("canonical event type invalid", index + 1)
                canonical_rows.append(row)
            chain_rows.append(chain_row)

        assert terminal_row is not None
        expected_terminal_sequence = (
            canonical_rows[-1][_Keys.SEQUENCE_NO] + 1 if canonical_rows else 1
        )
        if terminal_row[_Keys.SEQUENCE_NO] != expected_terminal_sequence:
            return self._failure("terminal receipt sequence", len(raw_rows))
        if terminal_row[_Keys.CREATED_AT] != generated_at:
            return self._failure("generated timestamp mismatch", len(raw_rows))
        if key_id != terminal_row[_Keys.KEY_ID]:
            return self._failure("bundle key mismatch", len(raw_rows))
        if bundle.get(_Keys.HEAD_HASH) != terminal_row[_Keys.SIGNATURE]:
            return self._failure("head hash mismatch", len(raw_rows))

        chain_result = self._signer.verify_chain(chain_rows)
        if not chain_result.ok:
            return chain_result

        safe_receipt = terminal_row[_Keys.SAFE_PAYLOAD]
        if not isinstance(safe_receipt, Mapping):
            return self._failure("terminal receipt malformed", len(raw_rows))
        receipt_digest = ReceiptExportV2Builder._digest(safe_receipt)
        if (
            not self._is_digest(bundle.get(_Keys.RECEIPT_DIGEST))
            or bundle.get(_Keys.RECEIPT_DIGEST) != receipt_digest
            or terminal_row[_Keys.PAYLOAD_DIGEST] != receipt_digest
        ):
            return self._failure("receipt digest mismatch", len(raw_rows))
        try:
            receipt = RunReceiptV2.model_validate(dict(safe_receipt))
        except Exception:  # noqa: BLE001 - verifier returns a safe fixed reason
            return self._failure("terminal receipt malformed", len(raw_rows))
        if receipt.run_id != run_id or receipt.generated_at != generated_at:
            return self._failure("terminal receipt identity", len(raw_rows))

        expected_receipt = _SafeReceiptRehydrator.fold(
            run_id=run_id,
            rows=canonical_rows,
            status=receipt.status,
        )
        if expected_receipt.model_dump(mode="json") != receipt.model_dump(mode="json"):
            return self._failure("receipt fold mismatch", len(raw_rows))
        return ChainVerificationResult(ok=True)

    def _parse_row(
        self,
        raw: Mapping[str, object],
        *,
        run_id: str,
        index: int,
    ) -> tuple[dict[str, object], AuditChainRow] | None:
        sequence_no = raw.get(_Keys.SEQUENCE_NO)
        event_type = raw.get(_Keys.EVENT_TYPE)
        created_at = raw.get(_Keys.CREATED_AT)
        payload_digest = raw.get(_Keys.PAYLOAD_DIGEST)
        safe_payload = raw.get(_Keys.SAFE_PAYLOAD)
        ref_class = raw.get(_Keys.REF_CLASS)
        key_id = raw.get(_Keys.KEY_ID)
        key_version = raw.get(_Keys.KEY_VERSION)
        if (
            not self._is_positive_int(sequence_no)
            or not isinstance(event_type, str)
            or not isinstance(created_at, str)
            or not self._is_digest(payload_digest)
            or not isinstance(safe_payload, Mapping)
            or not isinstance(ref_class, str)
            or not isinstance(key_id, str)
            or not isinstance(key_version, int)
            or isinstance(key_version, bool)
            or key_version < 0
        ):
            return None
        try:
            ref_enum = ReceiptExportRefClassV2(ref_class)
        except ValueError:
            return None
        if key_id != ReceiptExportV2Builder._key_id(key_version):
            return None
        raw_prev_hash = raw.get(_Keys.PREV_HASH)
        if raw_prev_hash is None:
            prev_hash = None
        else:
            prev_hash = self._hex(raw_prev_hash, nullable=False)
            if prev_hash is None:
                return None
        signature = self._hex(raw.get(_Keys.SIGNATURE), nullable=False)
        if signature is None or (index == 0 and prev_hash is not None):
            return None
        row = {
            _Keys.SEQUENCE_NO: sequence_no,
            _Keys.EVENT_TYPE: event_type,
            _Keys.CREATED_AT: created_at,
            _Keys.PAYLOAD_DIGEST: payload_digest,
            _Keys.SAFE_PAYLOAD: dict(safe_payload),
            _Keys.REF_CLASS: ref_enum.value,
            _Keys.KEY_ID: key_id,
            _Keys.KEY_VERSION: key_version,
            _Keys.SIGNATURE: raw.get(_Keys.SIGNATURE),
        }
        signing_payload = _SigningCodec.payload(
            bundle_version=_Values.BUNDLE_VERSION,
            run_id=run_id,
            sequence_no=sequence_no,
            event_type=event_type,
            created_at=created_at,
            payload_digest=payload_digest,
            safe_payload=row[_Keys.SAFE_PAYLOAD],
            ref_class=ref_enum.value,
            key_id=key_id,
        )
        return row, AuditChainRow(
            seq=index + 1,
            payload=signing_payload,
            prev_hash=prev_hash,
            signature=signature,
            key_version=key_version,
        )

    def _verify_legacy(self, bundle: Mapping[str, object]) -> ChainVerificationResult:
        try:
            return ReceiptExportVerifier(signer=self._signer).verify(bundle)
        except Exception:  # noqa: BLE001 - legacy malformed rows must not escape
            return self._failure("legacy bundle malformed")

    @staticmethod
    def _is_row_sequence(value: object) -> bool:
        return isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        )

    @staticmethod
    def _is_positive_int(value: object) -> bool:
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and 1 <= value <= _MAX_SAFE_INTEGER
        )

    @staticmethod
    def _is_digest(value: object) -> bool:
        return isinstance(value, str) and _SHA256.fullmatch(value) is not None

    @classmethod
    def _hex(cls, value: object, *, nullable: bool) -> bytes | None:
        if value is None and nullable:
            return None
        if not cls._is_digest(value):
            return None
        try:
            return bytes.fromhex(value)
        except ValueError:
            return None

    @staticmethod
    def _failure(
        reason: str, sequence_no: int | None = None
    ) -> ChainVerificationResult:
        return ChainVerificationResult(
            ok=False,
            broken_at_seq=sequence_no,
            reason=reason,
        )


__all__ = [
    "ReceiptExportRefClassV2",
    "ReceiptExportV2",
    "ReceiptExportV2Builder",
    "ReceiptExportV2Row",
    "ReceiptExportV2Verifier",
]
