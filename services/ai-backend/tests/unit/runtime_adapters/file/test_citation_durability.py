"""Citations are durable and read/write-consistent under the file backend.

Regression guard for the AC2b cutover: before the fix, the worker resolved its
citation store to an ephemeral ``InMemoryCitationStore`` under the file backend
(``FileRuntimeApiStore`` does not satisfy ``CitationStorePort``), while the
read-side ``source_store`` projected over a *different*, never-written
``FileCitationStore``. Result: run citations were lost on restart and the
Sources pane read an always-empty ledger. The factory now wires ONE durable
``FileCitationStore`` as the first-class ``citation_store`` port that both the
write path and ``source_store`` share.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agent_runtime.persistence.records import CitationRecord
from agent_runtime.settings import RuntimeSettings
from copilot_service_contracts.deployment_profile import ENV_DEPLOYMENT_PROFILE
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.file.citation_store import FileCitationStore
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_adapters.in_memory.citation_store import InMemoryCitationStore

_ORG = "org_acme"
_CONV = "conv_launch"
_RUN = "run_alpha"


def _citation(ordinal: int = 1) -> CitationRecord:
    return CitationRecord(
        citation_id=f"c{ordinal:03d}",
        run_id=_RUN,
        conversation_id=_CONV,
        org_id=_ORG,
        ordinal=ordinal,
        source_connector="notion",
        source_doc_id=f"doc_{ordinal}",
        source_url=f"https://example.invalid/doc_{ordinal}",
        title="Approved Positioning v3",
        snippet="Agentic search for every desk.",
        freshness_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _file_settings(root) -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_STORE_BACKEND": "file",
            "RUNTIME_FILE_STORE_ROOT": str(root),
        }
    )


class TestFileCitationDurability:
    def test_citation_store_is_the_durable_file_store(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setenv(ENV_DEPLOYMENT_PROFILE, "single_user_desktop")
        ports = RuntimeAdapterFactory.from_settings(_file_settings(tmp_path / "s"))
        # Not the ephemeral in-memory sibling the old worker fell back to.
        assert isinstance(ports.citation_store, FileCitationStore)

    def test_write_side_and_read_side_share_one_instance(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setenv(ENV_DEPLOYMENT_PROFILE, "single_user_desktop")
        ports = RuntimeAdapterFactory.from_settings(_file_settings(tmp_path / "s"))
        # The write-side port and the read-side projector MUST be the same
        # object, or a run's citations never reach the Sources pane.
        assert ports.citation_store is ports.source_store._citations

    async def test_citations_survive_a_store_reopen(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setenv(ENV_DEPLOYMENT_PROFILE, "single_user_desktop")
        root = tmp_path / "store"

        ports = RuntimeAdapterFactory.from_settings(_file_settings(root))
        await ports.lifecycle.open()
        try:
            await ports.citation_store.insert_many_or_get([_citation(1)])
        finally:
            await ports.lifecycle.close()

        # Fresh process/store at the SAME root: the durable ledger reloads.
        reopened = RuntimeAdapterFactory.from_settings(_file_settings(root))
        await reopened.lifecycle.open()
        try:
            rows = await reopened.citation_store.list_for_run(org_id=_ORG, run_id=_RUN)
        finally:
            await reopened.lifecycle.close()
        assert [r.source_doc_id for r in rows] == ["doc_1"]


class TestNonFileCitationWiringUnchanged:
    """The Postgres/in-memory backends keep their historical wiring."""

    def test_in_memory_shares_one_citation_instance(self) -> None:
        settings = RuntimeSettings.load(
            environ={"OPENAI_API_KEY": "sk-test", "RUNTIME_STORE_BACKEND": "in_memory"}
        )
        ports = RuntimeAdapterFactory.from_settings(settings)
        assert isinstance(ports.citation_store, InMemoryCitationStore)
        assert ports.citation_store is ports.source_store._citations

    def test_from_store_shares_one_citation_instance(self) -> None:
        ports = RuntimeAdapterFactory.from_store(InMemoryRuntimeApiStore())
        assert isinstance(ports.citation_store, InMemoryCitationStore)
        assert ports.citation_store is ports.source_store._citations
