"""RuntimeWorker prefers an injected citation store, else the legacy fallback.

The composed ``RuntimePorts`` now carry a backend-correct ``citation_store``;
the worker uses it when provided (durable file store on desktop) and otherwise
reproduces its historical resolution (Postgres persistence, else an in-memory
sibling) so every existing direct-construction call site is unchanged.
"""

from __future__ import annotations

from agent_runtime.persistence.ports import CitationStorePort
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_adapters.in_memory.citation_store import InMemoryCitationStore
from runtime_worker.loop import RuntimeWorker


class WorkerBuilderMixin:
    @staticmethod
    def _worker(*, citation_store=None) -> RuntimeWorker:
        store = InMemoryRuntimeApiStore()
        return RuntimeWorker(
            persistence=store,
            event_store=store,
            queue=store,
            citation_store=citation_store,
        )


class TestWorkerCitationStore(WorkerBuilderMixin):
    def test_uses_the_injected_citation_store(self) -> None:
        injected = InMemoryCitationStore()
        worker = self._worker(citation_store=injected)
        assert worker.run_handler.citation_store is injected

    def test_falls_back_to_legacy_resolution_when_absent(self) -> None:
        worker = self._worker(citation_store=None)
        resolved = worker.run_handler.citation_store
        # A concrete CitationStorePort is always resolved (never None), matching
        # the pre-refactor behavior for direct-construction call sites.
        assert resolved is not None
        assert isinstance(resolved, CitationStorePort)
