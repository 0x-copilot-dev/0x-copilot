"""Postgres-backed ``SourceStorePort`` that aggregates citation rows into per-source summaries."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from agent_runtime.persistence.encryption import FieldCodec
from agent_runtime.persistence.records import SourceAggregate


_TABLE = "runtime_citations"


class _CitationRowDecoder:
    """Translate one Postgres row into a :class:`SourceAggregate`."""

    @classmethod
    def decode(
        cls,
        *,
        row: dict[str, object],
        codec: FieldCodec,
        org_id: str,
    ) -> SourceAggregate:
        """Decrypt encrypted text columns and map the aggregated row to a :class:`SourceAggregate`."""
        title_cipher = row.get("title")
        snippet_cipher = row.get("snippet")
        title = (
            codec.decrypt_text(
                title_cipher,
                table=_TABLE,
                column="title",
                org_id=org_id,
            )
            if title_cipher is not None
            else None
        )
        snippet = (
            codec.decrypt_text(
                snippet_cipher,
                table=_TABLE,
                column="snippet",
                org_id=org_id,
            )
            if snippet_cipher is not None
            else None
        )
        return SourceAggregate(
            citation_id=str(row["citation_id"]),
            conversation_id=str(row["conversation_id"]),
            org_id=str(row["org_id"]),
            source_connector=str(row["source_connector"]),
            source_doc_id=str(row["source_doc_id"]),
            source_url=cls._optional_text(row.get("source_url")),
            title=title,
            snippet=snippet,
            freshness_at=cls._optional_datetime(row.get("freshness_at")),
            citation_count=int(row["citation_count"]),
            last_cited_at=cls._coerce_datetime(row["last_cited_at"]),
        )

    @staticmethod
    def _optional_text(value: object) -> str | None:
        """Return value as a non-empty string, or ``None`` if absent or blank."""
        if isinstance(value, str) and value.strip():
            return value
        return None

    @staticmethod
    def _optional_datetime(value: object) -> datetime | None:
        """Coerce value to a datetime, returning ``None`` when the column is NULL."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))

    @staticmethod
    def _coerce_datetime(value: object) -> datetime:
        """Coerce a non-null column value to a datetime; raises if unparseable."""
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))


class PostgresSourceStore:
    """Postgres-backed read port. Composes a ``PostgresRuntimeApiStore``."""

    _LIMIT_HARD_CAP = 500

    def __init__(self, parent: object) -> None:
        self._parent = parent

    @property
    def _codec(self) -> FieldCodec:
        """Return the parent store's FieldCodec for decrypting text columns."""
        return self._parent._codec  # type: ignore[attr-defined]

    async def aggregate_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
        run_id: str | None,
        limit: int,
    ) -> Sequence[SourceAggregate]:
        """Aggregate citations into per-source summaries, ranked by citation count."""
        capped = max(1, min(limit, self._LIMIT_HARD_CAP))
        sql = """
            SELECT
              source_connector,
              source_doc_id,
              MAX(org_id)             AS org_id,
              MAX(conversation_id)    AS conversation_id,
              (ARRAY_AGG(citation_id  ORDER BY created_at DESC))[1] AS citation_id,
              (ARRAY_AGG(source_url   ORDER BY created_at DESC))[1] AS source_url,
              (ARRAY_AGG(title        ORDER BY created_at DESC))[1] AS title,
              (ARRAY_AGG(snippet      ORDER BY created_at DESC))[1] AS snippet,
              MAX(freshness_at)       AS freshness_at,
              COUNT(*)::int           AS citation_count,
              MAX(created_at)         AS last_cited_at
            FROM runtime_citations
            WHERE org_id = %s
              AND conversation_id = %s
              AND (%s::text IS NULL OR run_id = %s)
            GROUP BY source_connector, source_doc_id
            ORDER BY citation_count DESC, last_cited_at DESC
            LIMIT %s
        """
        async with self._parent._tenant_connection(org_id=org_id) as conn:  # type: ignore[attr-defined]
            cur = await conn.execute(
                sql,
                (org_id, conversation_id, run_id, run_id, capped),
            )
            rows = await cur.fetchall()
        codec = self._codec
        return tuple(
            _CitationRowDecoder.decode(row=dict(row), codec=codec, org_id=org_id)
            for row in rows
        )


__all__ = ("PostgresSourceStore",)
