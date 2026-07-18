"""Signed, tamper-evident manifests for the file store's sensitive operations.

The desktop ``single_user_desktop`` file store already hash-chains every audit
row through :class:`copilot_audit_chain.AuditChainSigner` (HMAC-SHA256 over the
row's canonical JSON linked to the prior row's signature). This module is the
thin, single-source-of-truth layer on top of that primitive that makes the
chain a *manifest* of the sensitive operations a regulated buyer cares about —
physical deletion (``#8``), conversation export (``#9``), and host write-through
(AC5) — and lets the chain be verified independently and exported to a customer
SIEM.

A manifest entry records only ``{operation, subject ids, path(s), content
hash(es), actor, timestamp-from-caller}`` plus the chain fields
``{seq, prev_hash, signature, key_version}``. It carries **no secret values**:
never a token, broker credential, ``run_capability_context``, host-absolute
path, or file bytes — only ids, root-relative virtual paths, and content
digests. Tampering (an altered field, a reordered row, a dropped row, or a row
replayed from another chain) breaks the HMAC chain and :meth:`verify` reports
the first broken sequence number.

The two builders (:meth:`export_record`, :meth:`workspace_write_record`) and the
one canonicalizer (:meth:`signing_payload`) live here so the emission sites and
the verifier can never drift from the bytes that were actually signed.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from copilot_audit_chain import (
    AuditChainRow,
    AuditChainSigner,
    ChainVerificationResult,
)


class AuditManifest:
    """Vocabulary + record builders for the file store's signed manifest chain."""

    #: Chain-envelope keys that are NOT part of the signable payload. ``seq`` /
    #: ``prev_hash`` / ``signature`` / ``key_version`` are the chain fields;
    #: ``event_type`` is carried out-of-band (it is folded into the payload as
    #: ``__event_type__`` instead, mirroring the signer's canonical form).
    CHAIN_FIELDS: frozenset[str] = frozenset(
        {"seq", "prev_hash", "signature", "key_version", "event_type"}
    )

    #: The ``__event_type__`` marker the signer folds the operation kind into.
    EVENT_TYPE_MARKER: str = "__event_type__"

    #: Operation kinds (``event_type``) for the sensitive-op manifest entries.
    EVENT_CONVERSATION_EXPORT: str = "runtime_conversation_exported"
    EVENT_WORKSPACE_WRITE: str = "runtime_workspace_write"

    #: Record-field keys shared by the manifest entries (kept as constants so a
    #: field name can never be misspelled at an emission site or in a test).
    F_AUDIT_EVENT_ID: str = "audit_event_id"
    F_ORG_ID: str = "org_id"
    F_USER_ID: str = "user_id"
    F_CREATED_AT: str = "created_at"
    F_CONVERSATION_ID: str = "conversation_id"
    F_PARTS_DIGEST: str = "parts_digest"
    F_PART_COUNT: str = "part_count"
    F_COUNTS: str = "counts"
    F_OP: str = "op"
    F_MOUNT: str = "mount"
    F_PATH: str = "path"
    F_OBJECT_SHA256: str = "object_sha256"
    F_SIZE: str = "size"
    F_RUN_ID: str = "run_id"

    @classmethod
    def signing_payload(
        cls, *, event_type: str, record: dict[str, Any]
    ) -> dict[str, Any]:
        """Reconstruct the exact signable payload a row was (or will be) signed over.

        Strips the chain-envelope fields and folds the operation kind in as
        ``__event_type__``. This is the ONE canonicalization shared by the
        emission path (sign) and the verifier (recompute), so they cannot drift.
        """

        signable = {k: v for k, v in record.items() if k not in cls.CHAIN_FIELDS}
        signable[cls.EVENT_TYPE_MARKER] = event_type
        return signable

    @classmethod
    def export_record(
        cls,
        *,
        audit_event_id: str,
        org_id: str,
        user_id: str,
        conversation_id: str,
        exported_at: str,
        parts_digest: str,
        part_count: int,
        counts: dict[str, int],
    ) -> dict[str, Any]:
        """Build the manifest record for a conversation export (``#9``).

        ``parts_digest`` is a single content hash binding the whole archive
        (a digest over every part's SHA-256), so an export's integrity is
        pinned into the tamper-evident chain without copying any bytes. No
        destination host path is recorded (it is not needed and would be a
        host-path leak); the subject is the conversation id.
        """

        return {
            cls.F_AUDIT_EVENT_ID: audit_event_id,
            cls.F_ORG_ID: org_id,
            cls.F_USER_ID: user_id,
            cls.F_CONVERSATION_ID: conversation_id,
            cls.F_CREATED_AT: exported_at,
            cls.F_PARTS_DIGEST: parts_digest,
            cls.F_PART_COUNT: part_count,
            cls.F_COUNTS: dict(counts),
        }

    @classmethod
    def workspace_write_record(
        cls,
        *,
        audit_event_id: str,
        org_id: str,
        user_id: str | None,
        run_id: str,
        op: str,
        mount: str,
        path: str,
        object_sha256: str,
        size: int,
        created_at: str,
    ) -> dict[str, Any]:
        """Build the manifest record for a host write-through mutation (AC5).

        ``path`` is the route-relative virtual path (``/<mount>/<relative>``) —
        never a host-absolute path — and ``object_sha256`` is the content hash of
        the durably-snapshotted PRE-IMAGE. The per-run ``run_capability_context``
        (an opaque authority handle) is deliberately NOT recorded.
        """

        return {
            cls.F_AUDIT_EVENT_ID: audit_event_id,
            cls.F_ORG_ID: org_id,
            cls.F_USER_ID: user_id,
            cls.F_RUN_ID: run_id,
            cls.F_OP: op,
            cls.F_MOUNT: mount,
            cls.F_PATH: path,
            cls.F_OBJECT_SHA256: object_sha256,
            cls.F_SIZE: size,
            cls.F_CREATED_AT: created_at,
        }


class AuditManifestVerifier:
    """Independently verify a signed manifest chain and locate any tampering.

    Wraps :meth:`copilot_audit_chain.AuditChainSigner.verify_chain`: it turns the
    file store's ``(event_type, record)`` entries — the same shape
    ``list_audit_log_for_export`` emits (each carrying ``event_type``) — back into
    :class:`AuditChainRow`s using :meth:`AuditManifest.signing_payload`, orders
    them by ``seq``, and returns the chain result. A flipped field, a reordered
    row, or a dropped row all surface as ``ok=False`` with ``broken_at_seq``.
    """

    def __init__(self, signer: AuditChainSigner) -> None:
        self._signer = signer

    def verify(
        self, entries: Iterable[tuple[str, dict[str, Any]]]
    ) -> ChainVerificationResult:
        """Verify ``(event_type, record)`` entries as one ordered chain."""

        rows = [self._to_row(event_type, record) for event_type, record in entries]
        rows.sort(key=lambda r: r.seq)
        return self._signer.verify_chain(rows)

    def verify_rows(self, rows: Sequence[dict[str, Any]]) -> ChainVerificationResult:
        """Verify exported row dicts (each carrying an ``event_type`` field)."""

        return self.verify((str(row.get("event_type", "")), dict(row)) for row in rows)

    def _to_row(self, event_type: str, record: dict[str, Any]) -> AuditChainRow:
        prev_hex = record.get("prev_hash")
        sig_hex = record.get("signature")
        return AuditChainRow(
            seq=int(record.get("seq") or 0),
            payload=AuditManifest.signing_payload(event_type=event_type, record=record),
            prev_hash=bytes.fromhex(prev_hex) if isinstance(prev_hex, str) else None,
            signature=bytes.fromhex(sig_hex) if isinstance(sig_hex, str) else b"",
            key_version=int(record.get("key_version") or 0),
        )


__all__ = ("AuditManifest", "AuditManifestVerifier")
