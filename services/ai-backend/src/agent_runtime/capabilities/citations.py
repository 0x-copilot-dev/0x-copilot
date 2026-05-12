"""Per-run idempotent citation registry shared by tools, stream adapters, and replay.

Manages the (connector, doc_id) → CitationRecord cache, monotonic ordinal allocation,
persistence via CitationStorePort, and event emission (source_ingested / sources_ingested).
The active ledger is accessed via a ContextVar so tools call CitationLedger.cite() anywhere
in the run's call stack without threading context through signatures. Token format is
provider-agnostic: ``[c<base36(ordinal)>]``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from contextvars import ContextVar
from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import Field

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.persistence.records import CitationRecord
from runtime_api.schemas import RuntimeApiEventType

if TYPE_CHECKING:  # pragma: no cover - typing-only imports
    from agent_runtime.api.events import RuntimeEventProducer
    from agent_runtime.execution.contracts import StreamEventSource
    from agent_runtime.persistence.ports import CitationStorePort
    from runtime_api.schemas import RunRecord


_LOGGER = logging.getLogger(__name__)


class _Limits:
    """Per-run source caps to keep the SSE channel and in-memory registry bounded."""

    PER_RUN_MAX = 50
    BASE36_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


class _Fields:
    """source_ingested / sources_ingested payload field names, stable for replay."""

    CITATION = "citation"
    CITATIONS = "citations"
    SOURCE = "source"


class SourceRef(RuntimeContract):
    """Untrusted source descriptor that a tool or provider submits to the ledger."""

    source_connector: str = Field(min_length=1, max_length=64)
    source_doc_id: str = Field(min_length=1, max_length=512)
    title: str = Field(min_length=1, max_length=512)
    source_url: str | None = Field(default=None, max_length=2048)
    snippet: str | None = Field(default=None, max_length=1024)
    freshness_at: datetime | None = None
    source_tool_call_id: str | None = Field(default=None, max_length=128)


class CitationLedger:
    """Per-run idempotent citation registry.

    The runtime worker creates one ledger instance per run, binds it via
    :meth:`bind_for_run`, and clears it via :meth:`unbind` on teardown.
    The ledger owns:

    - the in-memory cache (``(connector, doc_id) -> CitationRecord``),
    - ordinal allocation (1-based, monotonic per run),
    - persistence through :class:`CitationStorePort`,
    - one ``source_ingested`` event per unique source through the producer.
    """

    def __init__(
        self,
        *,
        run: "RunRecord",
        store: "CitationStorePort",
        producer: "RuntimeEventProducer",
        source: "StreamEventSource",
        per_run_max: int = _Limits.PER_RUN_MAX,
    ) -> None:
        """Initialise the ledger bound to a run record, citation store, event producer, and stream source."""
        self._run = run
        self._store = store
        self._producer = producer
        self._source = source
        self._per_run_max = per_run_max
        # Insertion order is the canonical citation ordering for the run;
        # dict preserves it, and ordinals are allocated by insertion position.
        self._cache: dict[tuple[str, str], CitationRecord] = {}

    @property
    def run_id(self) -> str:
        """Return the run id this ledger is scoped to."""
        return self._run.run_id

    def sealed_payloads(self) -> list[dict[str, object]]:
        """Snapshot the current registry for ``final_response.citations``."""

        return [record.to_wire_payload() for record in self._cache.values()]

    async def register(self, source: SourceRef) -> str:
        """Register a single source against the run; return its inline token.

        Idempotent on ``(connector, doc_id)``. Emits exactly one
        ``source_ingested`` event when the source is newly inserted (no
        event on cache hit). Silently caps at ``per_run_max`` — beyond
        the cap the source is dropped and the empty string is returned
        so the assistant text can't accumulate unresolvable tokens.
        """

        tokens, new_records = await self._register_internal([source])
        if new_records:
            await self._producer.append_api_event(
                run=self._run,
                source=self._source,
                event_type=RuntimeApiEventType.SOURCE_INGESTED,
                payload={_Fields.CITATION: new_records[0].to_wire_payload()},
            )
        return tokens[0]

    async def register_many(self, sources: Sequence[SourceRef]) -> list[str]:
        """Register N sources in one batch; return N inline tokens in input order.

        Batched ingestion path used by callers that produce many sources at once
        (notably :class:`CitationProjector`). The new sources go through one bulk
        ``insert_many_or_get`` call and emit a single ``sources_ingested`` event
        carrying the ordered list of newly-inserted citations. Cache hits return
        existing tokens without a DB round trip; cap-dropped sources return ``""``.

        Output ordering is 1:1 with the input ``sources`` sequence so callers can
        splice tokens back into the same positions in the result text they came from.
        """

        if not sources:
            return []
        tokens, new_records = await self._register_internal(list(sources))
        if new_records:
            await self._producer.append_api_event(
                run=self._run,
                source=self._source,
                event_type=RuntimeApiEventType.SOURCES_INGESTED,
                payload={
                    _Fields.CITATIONS: [
                        record.to_wire_payload() for record in new_records
                    ]
                },
            )
        return tokens

    async def _register_internal(
        self,
        sources: Sequence[SourceRef],
    ) -> tuple[list[str], list[CitationRecord]]:
        """Cache-check, dedup, cap-enforce, and bulk-persist a batch of sources.

        Returns ``(tokens, newly_inserted)`` where tokens are 1:1 with input
        order (``""`` for cap-dropped sources) and newly_inserted contains only
        the records that were actually persisted (cache hits excluded). Caller
        emits the appropriate event for newly_inserted.
        """

        if not sources:
            return [], []

        tokens: list[str] = [""] * len(sources)
        new_records: list[CitationRecord] = []
        new_indices: list[int] = []
        # Tracks sources seen within this batch so repeated (connector, doc_id)
        # pairs collapse to the same ordinal without producing duplicate inserts.
        in_batch: dict[tuple[str, str], CitationRecord] = {}

        for idx, source in enumerate(sources):
            key = (source.source_connector, source.source_doc_id)
            existing = self._cache.get(key)
            if existing is not None:
                tokens[idx] = self._token_for(existing.ordinal)
                continue
            already_in_batch = in_batch.get(key)
            if already_in_batch is not None:
                tokens[idx] = self._token_for(already_in_batch.ordinal)
                continue
            # Cap counts the cache + the records we're about to insert in
            # this call — otherwise a single batch could blow past the cap.
            if len(self._cache) + len(new_records) >= self._per_run_max:
                _LOGGER.warning(
                    "citation registry cap reached for run %s (cap=%d)",
                    self._run.run_id,
                    self._per_run_max,
                )
                # tokens[idx] stays "" → assistant text drops the marker.
                continue
            ordinal = len(self._cache) + len(new_records) + 1
            record = CitationRecord(
                citation_id=self._token_id(ordinal),
                run_id=self._run.run_id,
                conversation_id=self._run.conversation_id,
                org_id=self._run.org_id,
                ordinal=ordinal,
                source_connector=source.source_connector,
                source_doc_id=source.source_doc_id,
                source_url=source.source_url,
                title=source.title,
                snippet=source.snippet,
                freshness_at=source.freshness_at,
                source_tool_call_id=source.source_tool_call_id,
            )
            new_records.append(record)
            new_indices.append(idx)
            in_batch[key] = record

        if not new_records:
            return tokens, []

        # Persist before emitting so a producer failure never orphans a
        # wire event without a backing row. The store is idempotent on
        # (run_id, connector, doc_id), so concurrent callers racing the
        # same source get the existing row back in input order.
        persisted = list(await self._store.insert_many_or_get(new_records))
        for offset, persisted_record in enumerate(persisted):
            idx = new_indices[offset]
            key = (
                persisted_record.source_connector,
                persisted_record.source_doc_id,
            )
            self._cache[key] = persisted_record
            tokens[idx] = self._token_for(persisted_record.ordinal)
        return tokens, persisted

    @classmethod
    async def cite(cls, source: SourceRef) -> str:
        """Resolve the active ledger from the ContextVar and register a source.

        Returns the empty string when no ledger is bound — citations are
        best-effort decoration, never required for tool correctness. This
        preserves DRY: tools call ``await CitationLedger.cite(...)`` from
        anywhere in the run's call stack without the runtime context being
        threaded through tool signatures.
        """

        ledger = _CITATION_LEDGER_CTX.get(None)
        if ledger is None:
            return ""
        return await ledger.register(source)

    @classmethod
    def bind_for_run(cls, ledger: "CitationLedger") -> object:
        """Set the active ledger; return the previous token for restoration."""

        return _CITATION_LEDGER_CTX.set(ledger)

    @classmethod
    def unbind(cls, token: object) -> None:
        """Restore the previous ledger token. Safe to call with the bind result."""

        _CITATION_LEDGER_CTX.reset(token)  # type: ignore[arg-type]

    @classmethod
    def active(cls) -> "CitationLedger | None":
        """Return the active ledger or ``None`` (test helper / debugging)."""

        return _CITATION_LEDGER_CTX.get(None)

    @staticmethod
    def _token_id(ordinal: int) -> str:
        """Return the raw token id string, e.g. ``"c1"`` or ``"czh"``."""
        return f"c{CitationLedger._to_base36(ordinal)}"

    @staticmethod
    def _token_for(ordinal: int) -> str:
        """Return the bracketed inline token, e.g. ``"[c1]"``."""
        return f"[{CitationLedger._token_id(ordinal)}]"

    @staticmethod
    def _to_base36(value: int) -> str:
        """Convert a positive integer to a base-36 string using 0-9a-z."""
        if value <= 0:
            raise ValueError("ordinal must be positive")
        digits: list[str] = []
        n = value
        while n > 0:
            digits.append(_Limits.BASE36_ALPHABET[n % 36])
            n //= 36
        return "".join(reversed(digits))


_CITATION_LEDGER_CTX: ContextVar[CitationLedger | None] = ContextVar(
    "citation_ledger",
    default=None,
)
