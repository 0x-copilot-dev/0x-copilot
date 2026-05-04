"""C9 normalizer: per-source row → ``NormalizedEvent``.

Each audit source has a slightly different row shape. The normalizer
collapses them to ``NormalizedEvent`` so exporters never know which
table the event came from.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend_app.siem_export.interface import NormalizedEvent, SiemExportSource


class EventNormalizer:
    """Stateless converter."""

    @classmethod
    def from_mcp_audit(cls, row: dict[str, Any]) -> NormalizedEvent:
        return NormalizedEvent(
            composite_id=cls._composite(row.get("org_id"), row["id"]),
            source=SiemExportSource.MCP_AUDIT,
            org_id=cls._optional_str(row, "org_id"),
            user_id=cls._optional_str(row, "user_id"),
            event_type=str(row.get("event_type") or "mcp.audit"),
            timestamp=cls._coerce_datetime(row["created_at"]),
            severity=str(row.get("severity") or "INFO"),
            payload=cls._dict(row.get("metadata")),
            raw=cls._safe_raw(row),
        )

    @classmethod
    def from_identity_audit(cls, row: dict[str, Any]) -> NormalizedEvent:
        return NormalizedEvent(
            composite_id=cls._composite(row.get("org_id"), row["id"]),
            source=SiemExportSource.IDENTITY_AUDIT,
            org_id=cls._optional_str(row, "org_id"),
            user_id=cls._optional_str(row, "user_id"),
            event_type=str(row.get("event_type") or "identity.audit"),
            timestamp=cls._coerce_datetime(row["created_at"]),
            severity=str(row.get("severity") or "INFO"),
            payload=cls._dict(row.get("metadata")),
            raw=cls._safe_raw(row),
        )

    @classmethod
    def from_runtime_audit(cls, row: dict[str, Any]) -> NormalizedEvent:
        # ai-backend ``runtime_audit_log`` carries an ``outcome`` enum;
        # exporters that care can read it from ``raw``.
        return NormalizedEvent(
            composite_id=cls._composite(row.get("org_id"), row["id"]),
            source=SiemExportSource.RUNTIME_AUDIT_REMOTE,
            org_id=cls._optional_str(row, "org_id"),
            user_id=cls._optional_str(row, "user_id"),
            event_type=str(row.get("event_type") or "runtime.audit"),
            timestamp=cls._coerce_datetime(row["created_at"]),
            severity=cls._severity_from_outcome(row.get("outcome")),
            payload=cls._dict(row.get("metadata_json_redacted")),
            raw=cls._safe_raw(row),
        )

    @staticmethod
    def _composite(org_id: object, event_id: object) -> str:
        org_part = "global" if org_id is None else str(org_id)
        return f"{org_part}:{event_id}"

    @staticmethod
    def _optional_str(row: dict[str, Any], key: str) -> str | None:
        value = row.get(key)
        return None if value is None else str(value)

    @staticmethod
    def _dict(value: object) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        return {}

    @staticmethod
    def _coerce_datetime(value: object) -> datetime:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        raise ValueError(f"unrecognized datetime value: {value!r}")

    @staticmethod
    def _severity_from_outcome(outcome: object) -> str:
        if outcome is None:
            return "INFO"
        text = str(outcome).lower()
        if text in {"failure", "denied", "error"}:
            return "WARNING"
        return "INFO"

    @staticmethod
    def _safe_raw(row: dict[str, Any]) -> dict[str, Any]:
        # Drop any binary blobs / connection objects; everything else is
        # passed through. Pydantic validates downstream.
        return {
            key: value
            for key, value in row.items()
            if isinstance(value, (str, int, float, bool, dict, list)) or value is None
        }
