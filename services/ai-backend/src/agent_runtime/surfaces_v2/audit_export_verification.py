"""Safe, durable contracts for D7/D12 audit-export verification sampling.

The receipt-export endpoints intentionally return a signed bundle to an
authorized caller and do not retain the caller's downloaded bytes.  A periodic
verification job therefore needs a separate *safe manifest* of each issued
bundle.  This module owns that manifest and the job's durable outcome contract.

The manifest never stores a legacy v1 row payload (v1 rows can contain a
private ledger payload).  It stores only the row envelope/signature metadata
and a digest.  A worker rehydrates a v1 bundle from the authoritative event
stream in memory, verifies that it still matches the manifest digest, then
passes it to the canonical verifier.  V2 rows are already a safe projection,
so their signed safe payload is retained directly.

Nothing here exports a body, opens a filesystem path, creates an approval, or
executes an effect.  Adapters persist only opaque refs, signed safe metadata,
and closed verification outcomes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
import re
from typing import Literal, Protocol, runtime_checkable

from pydantic import Field, field_validator, model_validator

from agent_runtime.execution.contracts import RuntimeContract


_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ISO_TIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_MAX_ROWS = 10_000
_MAX_SAFE_PAYLOAD_BYTES = 16_384
_PRIVATE_KEYS = frozenset(
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


class AuditExportFormat(StrEnum):
    """Closed, verifier-supported receipt export wire formats."""

    RECEIPT_V1 = "receipt_v1"
    RECEIPT_V2 = "receipt_v2"


AuditExportLegacyVersionKey = Literal["export_version", "bundle_version"]


class AuditExportVerificationOutcome(StrEnum):
    """The only durable outcomes of a sample attempt."""

    VERIFIED = "verified"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class AuditExportVerificationFailureClass(StrEnum):
    """Content-safe result classification; never persist verifier prose."""

    NONE = "none"
    CHAIN_INVALID = "chain_invalid"
    BUNDLE_MALFORMED = "bundle_malformed"
    SOURCE_MISMATCH = "source_mismatch"
    SOURCE_UNAVAILABLE = "source_unavailable"
    SIGNING_MATERIAL_UNAVAILABLE = "signing_material_unavailable"
    KEY_VERSION_UNAVAILABLE = "key_version_unavailable"
    INTERNAL_ERROR = "internal_error"


class AuditExportVerificationStateError(RuntimeError):
    """Fail-closed store error without a path, tenant, or source detail."""

    def __init__(self) -> None:
        super().__init__("audit export verification state is unavailable")


def _opaque(value: str) -> str:
    if _OPAQUE_ID.fullmatch(value) is None:
        raise ValueError("identifier must be a safe opaque token")
    return value


def audit_export_bundle_digest(value: object) -> str:
    """Return a stable digest without retaining a caller-provided body."""

    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=_json_default,
        ).encode("utf-8")
    except (TypeError, ValueError):
        encoded = b'"unserializable"'
    return hashlib.sha256(encoded).hexdigest()


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raw = getattr(value, "value", None)
    if isinstance(raw, str):
        return raw
    return type(value).__name__


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(timezone.utc)


def _safe_payload(value: object) -> bool:
    """Reject a catalog record that would persist an obviously private key."""

    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).lower() in _PRIVATE_KEYS or not _safe_payload(nested):
                return False
        return True
    if isinstance(value, (list, tuple)):
        return all(_safe_payload(item) for item in value)
    return value is None or isinstance(value, (str, int, float, bool))


def _safe_payload_size(value: Mapping[str, object]) -> bool:
    """Bound the signed safe projection even if a future builder regresses."""

    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    except (TypeError, ValueError):
        return False
    return len(encoded) <= _MAX_SAFE_PAYLOAD_BYTES


class AuditExportEvidenceRow(RuntimeContract):
    """Signed envelope evidence for one row, never a v1 raw payload."""

    ordinal: int = Field(ge=1, le=_MAX_ROWS)
    sequence_no: int = Field(ge=1)
    event_type: str = Field(min_length=1, max_length=128)
    created_at: str = Field(min_length=1, max_length=64)
    payload_digest: str = Field(min_length=64, max_length=64)
    prev_hash: str | None = Field(default=None, min_length=64, max_length=64)
    signature: str = Field(min_length=64, max_length=64)
    key_version: int = Field(ge=0)
    ledger_id: str | None = Field(default=None, min_length=1, max_length=256)
    key_id: str | None = Field(default=None, min_length=1, max_length=64)
    ref_class: str | None = Field(default=None, min_length=1, max_length=64)
    safe_payload: dict[str, object] | None = None

    @field_validator("payload_digest", "signature", "prev_hash")
    @classmethod
    def _hex_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _SHA256.fullmatch(value) is None:
            raise ValueError("digest must be sha256 hex")
        return value

    @field_validator("created_at")
    @classmethod
    def _time(cls, value: str) -> str:
        if _ISO_TIME.fullmatch(value) is None:
            raise ValueError("created_at must be an ISO timestamp")
        return value

    @field_validator("ledger_id")
    @classmethod
    def _ledger_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        # Legacy ledger ids use the visible middle-dot separator
        # (``r<run-short>·<sequence>``), so they are deliberately not run
        # through the generic ASCII opaque-token validator. They are still a
        # bounded identifier, never a URI/path/body field.
        if len(value) > 256 or "/" in value or "\x00" in value:
            raise ValueError("ledger_id must be a bounded ledger identifier")
        return value

    @model_validator(mode="after")
    def _safe_v2_projection(self) -> "AuditExportEvidenceRow":
        if self.safe_payload is not None and (
            not _safe_payload(self.safe_payload)
            or not _safe_payload_size(self.safe_payload)
        ):
            raise ValueError("safe_payload is not a bounded safe projection")
        return self


class AuditExportBundleManifest(RuntimeContract):
    """A safe catalog entry for one actually issued receipt export bundle."""

    manifest_version: int = Field(default=1, ge=1, le=1)
    bundle_ref: str = Field(min_length=1, max_length=256)
    org_id: str = Field(min_length=1, max_length=256)
    run_id: str = Field(min_length=1, max_length=256)
    format: AuditExportFormat
    bundle_digest: str = Field(min_length=64, max_length=64)
    generated_at: str = Field(min_length=1, max_length=64)
    captured_at: datetime
    key_id: str | None = Field(default=None, min_length=1, max_length=64)
    legacy_version_key: AuditExportLegacyVersionKey | None = None
    head_hash: str = Field(min_length=64, max_length=64)
    receipt_digest: str | None = Field(default=None, min_length=64, max_length=64)
    rows: tuple[AuditExportEvidenceRow, ...] = Field(min_length=1, max_length=_MAX_ROWS)

    @field_validator("bundle_ref", "org_id", "run_id")
    @classmethod
    def _ids(cls, value: str) -> str:
        return _opaque(value)

    @field_validator("bundle_digest", "head_hash", "receipt_digest")
    @classmethod
    def _digests(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _SHA256.fullmatch(value) is None:
            raise ValueError("digest must be sha256 hex")
        return value

    @field_validator("generated_at")
    @classmethod
    def _generated_at(cls, value: str) -> str:
        if _ISO_TIME.fullmatch(value) is None:
            raise ValueError("generated_at must be an ISO timestamp")
        return value

    @field_validator("captured_at")
    @classmethod
    def _captured_at(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def _shape_matches_format(self) -> "AuditExportBundleManifest":
        ordinals = tuple(row.ordinal for row in self.rows)
        if ordinals != tuple(range(1, len(self.rows) + 1)):
            raise ValueError("manifest rows must have contiguous ordinals")
        if self.format is AuditExportFormat.RECEIPT_V1:
            if (
                self.key_id is not None
                or self.receipt_digest is not None
                or self.legacy_version_key is None
            ):
                raise ValueError("legacy manifest carries only legacy evidence")
            if any(
                row.safe_payload is not None
                or row.key_id is not None
                or row.ref_class is not None
                for row in self.rows
            ):
                raise ValueError("legacy manifest must not persist row payloads")
        else:
            if (
                self.key_id is None
                or self.receipt_digest is None
                or self.legacy_version_key is not None
            ):
                raise ValueError("v2 manifest requires bundle key and receipt digest")
            if any(
                row.safe_payload is None or row.key_id is None or row.ref_class is None
                for row in self.rows
            ):
                raise ValueError("v2 manifest requires complete safe row evidence")
        return self

    @classmethod
    def from_v1_bundle(
        cls,
        *,
        org_id: str,
        bundle: Mapping[str, object],
        captured_at: datetime,
    ) -> "AuditExportBundleManifest":
        """Capture a v1 bundle without retaining its raw event payloads."""

        rows = bundle.get("rows")
        run_id = bundle.get("run_id")
        generated_at = bundle.get("generated_at")
        head_hash = bundle.get("head_hash")
        legacy_version_key: AuditExportLegacyVersionKey | None = (
            "export_version" if bundle.get("export_version") == 1 else None
        )
        if legacy_version_key is None and bundle.get("bundle_version") == 1:
            legacy_version_key = "bundle_version"
        if (
            not isinstance(rows, Sequence)
            or isinstance(rows, (str, bytes, bytearray))
            or legacy_version_key is None
            or not isinstance(run_id, str)
            or not isinstance(generated_at, str)
            or not isinstance(head_hash, str)
        ):
            raise ValueError("receipt export bundle is malformed")
        evidence: list[AuditExportEvidenceRow] = []
        for ordinal, raw in enumerate(rows, start=1):
            if not isinstance(raw, Mapping):
                raise ValueError("receipt export bundle is malformed")
            payload = raw.get("payload")
            if not isinstance(payload, Mapping):
                raise ValueError("receipt export bundle is malformed")
            evidence.append(
                AuditExportEvidenceRow(
                    ordinal=ordinal,
                    sequence_no=raw.get("sequence_no"),
                    event_type=raw.get("event_type"),
                    created_at=raw.get("created_at"),
                    payload_digest=audit_export_bundle_digest(payload),
                    prev_hash=raw.get("prev_hash"),
                    signature=raw.get("signature"),
                    key_version=raw.get("key_version"),
                    ledger_id=raw.get("ledger_id"),
                )
            )
        digest = audit_export_bundle_digest(bundle)
        return cls(
            bundle_ref=f"aev_{digest[:48]}",
            org_id=org_id,
            run_id=run_id,
            format=AuditExportFormat.RECEIPT_V1,
            bundle_digest=digest,
            generated_at=generated_at,
            captured_at=captured_at,
            legacy_version_key=legacy_version_key,
            head_hash=head_hash,
            rows=tuple(evidence),
        )

    @classmethod
    def from_v2_bundle(
        cls,
        *,
        org_id: str,
        bundle: Mapping[str, object],
        captured_at: datetime,
    ) -> "AuditExportBundleManifest":
        """Capture the already-safe v2 wire representation verbatim."""

        rows = bundle.get("rows")
        run_id = bundle.get("run_id")
        generated_at = bundle.get("generated_at")
        key_id = bundle.get("key_id")
        head_hash = bundle.get("head_hash")
        receipt_digest = bundle.get("receipt_digest")
        if (
            not isinstance(rows, Sequence)
            or isinstance(rows, (str, bytes, bytearray))
            or bundle.get("bundle_version") != 2
            or not isinstance(run_id, str)
            or not isinstance(generated_at, str)
            or not isinstance(key_id, str)
            or not isinstance(head_hash, str)
            or not isinstance(receipt_digest, str)
        ):
            raise ValueError("receipt export bundle is malformed")
        evidence: list[AuditExportEvidenceRow] = []
        for ordinal, raw in enumerate(rows, start=1):
            if not isinstance(raw, Mapping):
                raise ValueError("receipt export bundle is malformed")
            safe_payload = raw.get("safe_payload")
            if not isinstance(safe_payload, Mapping):
                raise ValueError("receipt export bundle is malformed")
            evidence.append(
                AuditExportEvidenceRow(
                    ordinal=ordinal,
                    sequence_no=raw.get("sequence_no"),
                    event_type=raw.get("event_type"),
                    created_at=raw.get("created_at"),
                    payload_digest=raw.get("payload_digest"),
                    prev_hash=raw.get("prev_hash"),
                    signature=raw.get("signature"),
                    key_version=raw.get("key_version"),
                    key_id=raw.get("key_id"),
                    ref_class=raw.get("ref_class"),
                    safe_payload=dict(safe_payload),
                )
            )
        digest = audit_export_bundle_digest(bundle)
        return cls(
            bundle_ref=f"aev_{digest[:48]}",
            org_id=org_id,
            run_id=run_id,
            format=AuditExportFormat.RECEIPT_V2,
            bundle_digest=digest,
            generated_at=generated_at,
            captured_at=captured_at,
            key_id=key_id,
            legacy_version_key=None,
            head_hash=head_hash,
            receipt_digest=receipt_digest,
            rows=tuple(evidence),
        )

    def v2_wire(self) -> dict[str, object]:
        """Rehydrate an exact safe v2 wire bundle for the canonical verifier."""

        if self.format is not AuditExportFormat.RECEIPT_V2:
            raise AuditExportVerificationStateError()
        return {
            "bundle_version": 2,
            "run_id": self.run_id,
            "generated_at": self.generated_at,
            "key_id": self.key_id,
            "rows": [
                {
                    "sequence_no": row.sequence_no,
                    "event_type": row.event_type,
                    "created_at": row.created_at,
                    "payload_digest": row.payload_digest,
                    "safe_payload": row.safe_payload,
                    "ref_class": row.ref_class,
                    "prev_hash": row.prev_hash,
                    "signature": row.signature,
                    "key_id": row.key_id,
                    "key_version": row.key_version,
                }
                for row in self.rows
            ],
            "row_count": len(self.rows),
            "receipt_digest": self.receipt_digest,
            "head_hash": self.head_hash,
        }

    def same_capture_as(self, other: "AuditExportBundleManifest") -> bool:
        """Compare immutable issued-bundle evidence, ignoring first-capture time.

        A user can request the same deterministic terminal receipt repeatedly.
        It must remain one catalog row, not turn a harmless repeat request into
        a conflicting manifest solely because its capture observation time
        differs.
        """

        return self.model_dump(exclude={"captured_at"}) == other.model_dump(
            exclude={"captured_at"}
        )


class AuditExportVerificationCursor(RuntimeContract):
    """Exclusive global keyset cursor; every row remains tenant-scoped."""

    after_captured_at: datetime
    after_org_id: str = Field(min_length=1, max_length=256)
    after_bundle_ref: str = Field(min_length=1, max_length=256)

    @field_validator("after_captured_at")
    @classmethod
    def _time(cls, value: datetime) -> datetime:
        return _utc(value)

    @field_validator("after_org_id", "after_bundle_ref")
    @classmethod
    def _ids(cls, value: str) -> str:
        return _opaque(value)


class AuditExportVerificationRecord(RuntimeContract):
    """Persisted safe sample outcome, never a bundle body or error message."""

    org_id: str = Field(min_length=1, max_length=256)
    bundle_ref: str = Field(min_length=1, max_length=256)
    bundle_digest: str = Field(min_length=64, max_length=64)
    format: AuditExportFormat
    outcome: AuditExportVerificationOutcome
    failure_class: AuditExportVerificationFailureClass
    broken_at_seq: int | None = Field(default=None, ge=1)
    sampled_at: datetime
    attempts: int = Field(default=1, ge=1)

    @field_validator("org_id", "bundle_ref")
    @classmethod
    def _ids(cls, value: str) -> str:
        return _opaque(value)

    @field_validator("bundle_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        if _SHA256.fullmatch(value) is None:
            raise ValueError("bundle_digest must be sha256 hex")
        return value

    @field_validator("sampled_at")
    @classmethod
    def _time(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def _outcome_consistency(self) -> "AuditExportVerificationRecord":
        if self.outcome is AuditExportVerificationOutcome.VERIFIED:
            if self.failure_class is not AuditExportVerificationFailureClass.NONE:
                raise ValueError("verified outcome cannot carry a failure class")
        elif self.failure_class is AuditExportVerificationFailureClass.NONE:
            raise ValueError("non-verified outcome requires a failure class")
        return self


@runtime_checkable
class AuditExportVerificationStore(Protocol):
    """Durable catalog, scan state, lease, and safe outcome port."""

    async def record_manifest(self, *, manifest: AuditExportBundleManifest) -> None:
        """Idempotently retain safe evidence for one issued export bundle."""

    async def list_manifests_after(
        self,
        *,
        cursor: AuditExportVerificationCursor | None,
        limit: int,
    ) -> Sequence[AuditExportBundleManifest]:
        """Return a stable keyset page of existing issued bundles."""

    async def load_scan_cursor(self) -> AuditExportVerificationCursor | None:
        """Read the global worker cursor."""

    async def advance_scan_cursor(
        self,
        *,
        expected: AuditExportVerificationCursor | None,
        next_cursor: AuditExportVerificationCursor | None,
    ) -> bool:
        """Compare-and-swap the source cursor after all samples persist."""

    async def acquire_lease(
        self,
        *,
        owner_id: str,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        """Acquire a bounded worker lease without exposing another owner."""

    async def release_lease(self, *, owner_id: str) -> None:
        """Release this worker's lease; an expired lease is harmless."""

    async def record_outcome(
        self, *, record: AuditExportVerificationRecord
    ) -> AuditExportVerificationRecord:
        """Upsert a safe sample outcome and increment its attempt counter."""

    async def list_outcomes(
        self, *, org_id: str, bundle_ref: str
    ) -> Sequence[AuditExportVerificationRecord]:
        """Read safe outcomes for one tenant-local bundle reference."""


__all__ = (
    "AuditExportBundleManifest",
    "AuditExportEvidenceRow",
    "AuditExportFormat",
    "AuditExportVerificationCursor",
    "AuditExportVerificationFailureClass",
    "AuditExportVerificationOutcome",
    "AuditExportVerificationRecord",
    "AuditExportVerificationStateError",
    "AuditExportVerificationStore",
    "audit_export_bundle_digest",
)
