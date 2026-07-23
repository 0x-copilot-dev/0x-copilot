"""Tamper-evident receipt export — HMAC-chained over the Work Ledger (PRD-E3 D1).

The run receipt (E1) is the run's accountability artifact; this module hardens
it into a durable, tamper-evident **export bundle**. Every row of the export is
HMAC-SHA256 hash-chained with the shared ``packages/audit-chain`` signer, so
flipping one byte anywhere — a ledger event payload, the receipt fold, a
signature, the row order — makes :class:`ReceiptExportVerifier.verify` fail
(SDR §10.6 "audit-chain hashing hardens export in the last wave").

Chain semantics (export-time signing — stateless, works on every runtime
adapter, no migration):

- Rows are the run's ledger events in ``sequence_no`` order, **filtered to the
  Work-Ledger vocabulary** (``event_type in LEDGER_EVENT_TYPES`` — v2 events
  only; model deltas / tool internals are not the accountability record).
- Each row's signing payload is exactly
  ``{run_id, event_type, sequence_no, created_at, payload}`` (JSON-native), and
  ``sig = signer.sign(prev_hash=<prior row's signature bytes>, payload=...)``.
- The **final chained row is synthetic**: ``event_type = "receipt.export"``,
  ``payload = receipt.model_dump(mode="json")`` — so tampering with the receipt
  object itself also breaks verification. It carries no run-stream event, so its
  export-row fields are assigned deterministically (see :meth:`ReceiptExportBuilder.build`).

Layering: like the other v2 folds, this module reads events **structurally**
(``event_type`` / ``sequence_no`` / ``created_at`` / ``payload``) via a
:class:`_ExportEventLike` protocol, so it never imports ``runtime_api``
(``runtime_api`` imports ``agent_runtime``, never the reverse). The synthetic
``"receipt.export"`` row is an export-format construct, NOT a ledger event type —
it is never appended to the run stream nor added to ``LEDGER_EVENT_TYPES``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from copilot_audit_chain import (
    AuditChainRow,
    AuditChainSigner,
    ChainVerificationResult,
)
from copilot_service_contracts.work_ledger import LEDGER_EVENT_TYPES
from pydantic import PositiveInt

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.entities import RunReceipt
from agent_runtime.surfaces_v2.ledger_ids import LedgerIdCodec, LedgerIdFormatError

# The Work-Ledger vocabulary, as a set for O(1) membership on the hot filter.
_LEDGER_EVENT_TYPE_SET: frozenset[str] = frozenset(LEDGER_EVENT_TYPES)


class _Values:
    """Frozen scalar values the export format pins."""

    # The synthetic final-row event type. An export-format construct — NOT a
    # ledger event type (never appended to the run stream, never in
    # ``LEDGER_EVENT_TYPES``).
    SYNTHETIC_EVENT_TYPE = "receipt.export"
    # ``ReceiptExportBundle.export_version`` — bump only on a wire-shape change.
    EXPORT_VERSION = 1


class _Keys:
    """Field-name constants for the signing payload + the stored export row."""

    class Sign:
        """The five keys of a row's signing payload (order-free — canonicalized)."""

        RUN_ID = "run_id"
        EVENT_TYPE = "event_type"
        SEQUENCE_NO = "sequence_no"
        CREATED_AT = "created_at"
        PAYLOAD = "payload"

    class Row:
        """Keys read off a stored (possibly tampered) export row during verify."""

        SEQ = "seq"
        EVENT_TYPE = "event_type"
        SEQUENCE_NO = "sequence_no"
        CREATED_AT = "created_at"
        PAYLOAD = "payload"
        PREV_HASH = "prev_hash"
        SIGNATURE = "signature"
        KEY_VERSION = "key_version"

    class Bundle:
        """Top-level keys read off a stored (possibly tampered) bundle."""

        RUN_ID = "run_id"
        ROWS = "rows"


class _Messages:
    """Safe public messages — never leak ``AUDIT_HMAC_KEY`` state or paths."""

    EXPORT_UNAVAILABLE = "Receipt export is not available in this environment."


class ReceiptExportUnavailable(Exception):
    """Signing material is unavailable (production without ``AUDIT_HMAC_KEY``).

    Carries only a safe, public message — no env/key detail. The export route
    maps it to HTTP 503.
    """

    def __init__(self, message: str = _Messages.EXPORT_UNAVAILABLE) -> None:
        super().__init__(message)


@runtime_checkable
class _ExportEventLike(Protocol):
    """Envelope-lite shape the builder reads (a ``RuntimeEventEnvelope`` fits)."""

    event_type: object
    sequence_no: int
    created_at: object
    payload: Mapping[str, object]


class ReceiptExportRow(RuntimeContract):
    """One signed row of the export chain."""

    seq: PositiveInt  # 1-based position in the export chain
    ledger_id: str  # LedgerIdCodec.format(run_id, sequence_no) -> "r<short>·<seq>"
    event_type: str  # SDR §5 wire value, e.g. "decision.recorded"
    sequence_no: PositiveInt  # the run-stream sequence
    created_at: str  # ISO-8601
    payload: dict[str, object]  # the envelope payload (JSON-native)
    prev_hash: str | None  # hex; None on the first row
    signature: str  # hex HMAC-SHA256
    key_version: int


class ReceiptExportBundle(RuntimeContract):
    """The durable, tamper-evident receipt export."""

    export_version: Literal[1] = _Values.EXPORT_VERSION
    run_id: str
    generated_at: str  # ISO-8601
    receipt: RunReceipt  # E1's fold output, re-folded at export time
    rows: tuple[ReceiptExportRow, ...]
    head_hash: str  # hex of the last row's signature


class _ExportChainCodec:
    """Shared canonicalization helpers for the builder + verifier.

    Class-scoped (no module-level helpers, per service rules) so both the
    signing and verifying paths build the exact same per-row signing payload.
    """

    @staticmethod
    def signing_payload(
        *,
        run_id: object,
        event_type: object,
        sequence_no: object,
        created_at: object,
        payload: object,
    ) -> dict[str, object]:
        """Return the exact dict both build + verify sign, keys per :class:`_Keys.Sign`."""

        return {
            _Keys.Sign.RUN_ID: run_id,
            _Keys.Sign.EVENT_TYPE: event_type,
            _Keys.Sign.SEQUENCE_NO: sequence_no,
            _Keys.Sign.CREATED_AT: created_at,
            _Keys.Sign.PAYLOAD: payload,
        }

    @staticmethod
    def ledger_id(run_id: str, sequence_no: int) -> str:
        """``LedgerIdCodec.format`` with the receipt fold's safe fallback."""

        try:
            return LedgerIdCodec.format(run_id, sequence_no)
        except LedgerIdFormatError:
            return f"r{run_id}·{sequence_no}"

    @staticmethod
    def event_type_value(event_type: object) -> str:
        value = getattr(event_type, "value", event_type)
        return value if isinstance(value, str) else str(event_type)

    @staticmethod
    def created_at_str(value: object) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, datetime):
            return value.isoformat()
        return ""


class ReceiptExportBuilder:
    """Builds a signed :class:`ReceiptExportBundle` from a run's ledger + receipt."""

    def __init__(self, *, signer: AuditChainSigner) -> None:
        self._signer = signer

    def build(
        self,
        *,
        run_id: str,
        events: Sequence[_ExportEventLike],
        receipt: RunReceipt,
    ) -> ReceiptExportBundle:
        """Chain the run's ledger events + a synthetic receipt row into a bundle.

        Rows are the ledger-vocabulary events in ``sequence_no`` order; the final
        synthetic row seals the receipt fold. ``generated_at`` is the receipt's
        own fold timestamp so the bundle is a deterministic function of the run's
        events (refolding the same events yields a byte-identical bundle).
        """

        ledger_rows = self._ledger_rows_in_order(events)
        export_rows: list[ReceiptExportRow] = []
        prev_signature: bytes | None = None

        for index, (event_type, sequence_no, created_at, payload) in enumerate(
            ledger_rows, start=1
        ):
            row, prev_signature = self._sign_row(
                run_id=run_id,
                seq=index,
                event_type=event_type,
                sequence_no=sequence_no,
                created_at=created_at,
                payload=payload,
                prev_signature=prev_signature,
            )
            export_rows.append(row)

        # The synthetic receipt row: no run-stream event, so assign its fields
        # deterministically (PRD-E3 D1). ``sequence_no`` = highest folded event's
        # sequence_no + 1 (or 1 when the export has zero ledger rows).
        generated_at = receipt.generated_at
        synthetic_seq = len(ledger_rows) + 1
        synthetic_sequence_no = (ledger_rows[-1][1] + 1) if ledger_rows else 1
        receipt_row, prev_signature = self._sign_row(
            run_id=run_id,
            seq=synthetic_seq,
            event_type=_Values.SYNTHETIC_EVENT_TYPE,
            sequence_no=synthetic_sequence_no,
            created_at=generated_at,
            payload=receipt.model_dump(mode="json"),
            prev_signature=prev_signature,
        )
        export_rows.append(receipt_row)

        return ReceiptExportBundle(
            run_id=run_id,
            generated_at=generated_at,
            receipt=receipt,
            rows=tuple(export_rows),
            head_hash=receipt_row.signature,
        )

    def _sign_row(
        self,
        *,
        run_id: str,
        seq: int,
        event_type: str,
        sequence_no: int,
        created_at: str,
        payload: dict[str, object],
        prev_signature: bytes | None,
    ) -> tuple[ReceiptExportRow, bytes]:
        """Sign one row against the prior signature; return it + its own signature."""

        signing_payload = _ExportChainCodec.signing_payload(
            run_id=run_id,
            event_type=event_type,
            sequence_no=sequence_no,
            created_at=created_at,
            payload=payload,
        )
        signature = self._signer.sign(prev_hash=prev_signature, payload=signing_payload)
        row = ReceiptExportRow(
            seq=seq,
            ledger_id=_ExportChainCodec.ledger_id(run_id, sequence_no),
            event_type=event_type,
            sequence_no=sequence_no,
            created_at=created_at,
            payload=payload,
            prev_hash=(
                signature.prev_hash.hex() if signature.prev_hash is not None else None
            ),
            signature=signature.signature.hex(),
            key_version=signature.key_version,
        )
        return row, signature.signature

    @staticmethod
    def _ledger_rows_in_order(
        events: Sequence[_ExportEventLike],
    ) -> list[tuple[str, int, str, dict[str, object]]]:
        """Filter to the ledger vocabulary + sort ascending by ``sequence_no``."""

        rows: list[tuple[str, int, str, dict[str, object]]] = []
        for event in events:
            event_type = _ExportChainCodec.event_type_value(event.event_type)
            if event_type not in _LEDGER_EVENT_TYPE_SET:
                continue
            sequence_no = int(event.sequence_no)
            created_at = _ExportChainCodec.created_at_str(event.created_at)
            payload = event.payload
            payload = dict(payload) if isinstance(payload, Mapping) else {}
            rows.append((event_type, sequence_no, created_at, payload))
        rows.sort(key=lambda row: row[1])
        return rows


class ReceiptExportVerifier:
    """Verifies a :class:`ReceiptExportBundle` (or its tampered wire form)."""

    def __init__(self, *, signer: AuditChainSigner) -> None:
        self._signer = signer

    def verify(self, bundle: Mapping[str, object]) -> ChainVerificationResult:
        """Recompute each row's signing payload + return the chain verdict.

        Takes a plain mapping (not the pydantic model) so a byte-flipped JSON
        bundle verifies through the same path a genuine one does — first break
        wins, ``broken_at_seq`` populated with that row's ``seq``.
        """

        run_id = bundle.get(_Keys.Bundle.RUN_ID)
        raw_rows = bundle.get(_Keys.Bundle.ROWS)
        rows: Sequence[object] = raw_rows if isinstance(raw_rows, Sequence) else ()

        chain_rows: list[AuditChainRow] = []
        for raw in rows:
            row = raw if isinstance(raw, Mapping) else {}
            signing_payload = _ExportChainCodec.signing_payload(
                run_id=run_id,
                event_type=row.get(_Keys.Row.EVENT_TYPE),
                sequence_no=row.get(_Keys.Row.SEQUENCE_NO),
                created_at=row.get(_Keys.Row.CREATED_AT),
                payload=row.get(_Keys.Row.PAYLOAD),
            )
            prev_hash_hex = row.get(_Keys.Row.PREV_HASH)
            signature_hex = row.get(_Keys.Row.SIGNATURE)
            chain_rows.append(
                AuditChainRow(
                    seq=int(row.get(_Keys.Row.SEQ, 0)),
                    payload=signing_payload,
                    prev_hash=(
                        bytes.fromhex(prev_hash_hex)
                        if isinstance(prev_hash_hex, str) and prev_hash_hex
                        else None
                    ),
                    signature=(
                        bytes.fromhex(signature_hex)
                        if isinstance(signature_hex, str)
                        else b""
                    ),
                    key_version=int(row.get(_Keys.Row.KEY_VERSION, -1)),
                )
            )
        return self._signer.verify_chain(chain_rows)


__all__ = [
    "ReceiptExportBuilder",
    "ReceiptExportBundle",
    "ReceiptExportRow",
    "ReceiptExportUnavailable",
    "ReceiptExportVerifier",
]
