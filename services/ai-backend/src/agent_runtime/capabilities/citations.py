"""Citation ledger — single seam for tool, provider, and replay paths.

PR 1.1 (design at ``docs/new-design/01-citations-live-registry.md``).

Tools, the Anthropic stream adapter, and the OpenAI Responses adapter all
funnel through :meth:`CitationLedger.register` so the per-run idempotency
key ``(connector, doc_id)`` is enforced in one place, ordinals are
allocated monotonically, and exactly one ``source_ingested`` event fires
per unique source.

Tools reach the active ledger via :meth:`CitationLedger.cite` (a class
method that resolves the ContextVar set by the runtime worker before
graph execution). Tools that run outside the worker context (rare) get
``None`` from the contextvar and ``cite`` returns the empty string —
citations are best-effort decoration, never required for correctness.

The ledger is provider-agnostic: it does not know whether a citation
came from a tool result, an Anthropic ``citations_delta`` block, or an
OpenAI ``output_text.done`` annotation. The token format is also
provider-agnostic: ``[c<base36(ordinal)>]`` (e.g. ``[c1]``, ``[czh]``).
"""

from __future__ import annotations

import logging
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
    """Per-run caps. Soft ceilings to keep the SSE channel and registry tiny."""

    PER_RUN_MAX = 50
    BASE36_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


class _Fields:
    """Wire payload field names — kept stable for replay compatibility."""

    CITATION = "citation"
    SOURCE = "source"


class SourceRef(RuntimeContract):
    """Untrusted source descriptor a tool or provider hands to the ledger."""

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
        self._run = run
        self._store = store
        self._producer = producer
        self._source = source
        self._per_run_max = per_run_max
        # (connector, doc_id) -> CitationRecord. Ordinals fall out of insertion
        # order, so the dict's preserved insertion order IS the canonical
        # ordering of the run's citations.
        self._cache: dict[tuple[str, str], CitationRecord] = {}

    @property
    def run_id(self) -> str:
        return self._run.run_id

    def sealed_payloads(self) -> list[dict[str, object]]:
        """Snapshot the current registry for ``final_response.citations``."""

        return [record.to_wire_payload() for record in self._cache.values()]

    async def register(self, source: SourceRef) -> str:
        """Register a source against the run; return its inline token.

        Idempotent on ``(connector, doc_id)``. Emits exactly one
        ``source_ingested`` event per unique source. Silently caps at
        ``per_run_max`` — beyond the cap the source is dropped and the
        empty string is returned so the assistant text can't accumulate
        unresolvable tokens.
        """

        key = (source.source_connector, source.source_doc_id)
        existing = self._cache.get(key)
        if existing is not None:
            return self._token_for(existing.ordinal)

        if len(self._cache) >= self._per_run_max:
            _LOGGER.warning(
                "citation registry cap reached for run %s (cap=%d)",
                self._run.run_id,
                self._per_run_max,
            )
            return ""

        ordinal = len(self._cache) + 1
        citation_id = self._token_id(ordinal)
        record = CitationRecord(
            citation_id=citation_id,
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
        # Persist first so a producer-emit failure doesn't orphan a wire
        # event without a backing row. The store is idempotent on the
        # (run_id, connector, doc_id) unique index, so a concurrent caller
        # racing the same source receives the existing row back.
        persisted = self._store.insert_or_get(record)
        self._cache[key] = persisted
        await self._producer.append_api_event(
            run=self._run,
            source=self._source,
            event_type=RuntimeApiEventType.SOURCE_INGESTED,
            payload={_Fields.CITATION: persisted.to_wire_payload()},
        )
        return self._token_for(persisted.ordinal)

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
        return f"c{CitationLedger._to_base36(ordinal)}"

    @staticmethod
    def _token_for(ordinal: int) -> str:
        return f"[{CitationLedger._token_id(ordinal)}]"

    @staticmethod
    def _to_base36(value: int) -> str:
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
