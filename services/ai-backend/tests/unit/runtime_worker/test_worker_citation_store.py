"""RuntimeWorker prefers an injected citation store, else the legacy fallback.

The composed ``RuntimePorts`` now carry a backend-correct ``citation_store``;
the worker uses it when provided (durable file store on desktop) and otherwise
reproduces its historical resolution (Postgres persistence, else an in-memory
sibling) so every existing direct-construction call site is unchanged.
"""

from __future__ import annotations

from agent_runtime.api.model_invocation_store import EventJournalModelInvocationStore
from agent_runtime.persistence.ports import CitationStorePort
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_adapters.in_memory.citation_store import InMemoryCitationStore
from runtime_worker.loop import RuntimeWorker


class WorkerBuilderMixin:
    @staticmethod
    def _worker(*, citation_store=None, model_invocation_store=None) -> RuntimeWorker:
        store = InMemoryRuntimeApiStore()
        return RuntimeWorker(
            persistence=store,
            event_store=store,
            queue=store,
            citation_store=citation_store,
            model_invocation_store=model_invocation_store,
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

    def test_threads_one_model_invocation_journal_to_initial_and_resume_paths(
        self,
    ) -> None:
        journal = object()
        worker = self._worker(model_invocation_store=journal)

        assert worker.run_handler._model_invocation_composer._journal is journal
        assert worker.approval_handler._model_invocation_composer._journal is journal

    def test_defaults_model_invocation_journal_to_the_canonical_event_store(
        self,
    ) -> None:
        worker = self._worker()
        journal = worker.run_handler._model_invocation_composer._journal

        assert isinstance(journal, EventJournalModelInvocationStore)
        assert worker.approval_handler._model_invocation_composer._journal is journal
