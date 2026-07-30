"""Result-admission writer parity across every selectable runtime store.

``ToolResultAdmissionAdapter`` bounds a tool result before the model sees it, but
it can only do so through the ``OffloadWriter`` its store supplies. For one
release exactly one store supplied that writer, so bounding was a property of the
desktop deployment rather than of the runtime: on the in-memory and Postgres
stores an oversized result was admitted inline, unbounded. A guarantee that holds
in dev and not in production (or the reverse) is not a guarantee.

These tests therefore assert the property *per adapter* rather than once. Each
invariant runs against every writer via the ``writer`` fixture, and
:class:`TestCrossAdapterEquivalence` additionally pins the writers against *each
other* — same digest, same locator, same bounded model content, byte for byte.
Present-on-every-adapter is the weaker claim; identical-on-every-adapter is the
one that makes a store swap safe, so that is what is checked.

**Postgres scope.** The writer is exercised here for real, against a
content-addressed sink on a shared root — which is exactly what a Postgres
deployment supplies, since the runtime schema has no column for blob content
(``runtime_context_payloads`` is metadata-only) and the runtime already requires
``RUNTIME_ARTIFACT_BLOB_ROOT`` to be a durable shared root under
``RUNTIME_STORE_BACKEND=postgres``. No database is contacted and none is needed:
nothing in the admission decision reaches SQL. What a live database would
additionally prove is stated in ``test_postgres_ledger_row_is_not_yet_written``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_runtime.context.memory.contracts import ContextCompressionStrategy
from agent_runtime.context.tool_result_admission import ToolResultAdmissionAdapter
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.object_store import FileObjectStore
from runtime_adapters.file.offload import FileOffloadWriter
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_adapters.in_memory.offload import InMemoryOffloadWriter
from runtime_adapters.offload import (
    ContentAddressedOffloadWriter,
    OffloadDigestMismatch,
    OffloadReadError,
    OffloadWriterResolver,
)
from runtime_adapters.postgres.offload import PostgresOffloadWriter

# Comfortably past the default inline budget (8k tokens ≈ 32k chars), so every
# adapter must take the offload branch rather than sitting near the threshold.
_OVERSIZED = "needle-" + ("x" * 200_000)
_SMALL = "small tool result"

ADAPTERS = ("in_memory", "file", "postgres")


def _build_writer(name: str, root: Path) -> ContentAddressedOffloadWriter:
    """Return the offload writer for one adapter name."""

    if name == "in_memory":
        return InMemoryOffloadWriter()
    if name == "file":
        return FileOffloadWriter(FileObjectStore(FileStoreLayout(root / "file")))
    if name == "postgres":
        # A Postgres deployment parks bytes on its durable shared root; the sink
        # is the same content-addressed store, reachable from every node.
        return PostgresOffloadWriter(FileObjectStore(FileStoreLayout(root / "shared")))
    raise AssertionError(f"unknown adapter {name!r}")


@pytest.fixture(params=ADAPTERS)
def adapter_name(request) -> str:
    """Name the runtime store adapter under test."""

    return request.param


@pytest.fixture
def writer(adapter_name: str, tmp_path: Path) -> ContentAddressedOffloadWriter:
    """Yield the result-admission writer for each selectable store adapter."""

    return _build_writer(adapter_name, tmp_path)


class TestEveryAdapterHasAWriter:
    """A store with no writer admits unbounded results — none may lack one."""

    def test_writer_exists_and_is_callable_as_the_offload_alias(
        self, writer: ContentAddressedOffloadWriter
    ) -> None:
        """Each adapter's writer satisfies ``Callable[[str], str]``."""

        reference = writer(_OVERSIZED)

        assert isinstance(reference, str)
        assert reference.startswith("/large_tool_results/")

    def test_resolver_selects_a_writer_for_the_file_store(self, tmp_path: Path) -> None:
        """The desktop store resolves to its object-store writer."""

        class _FileStoreShape:
            object_store = FileObjectStore(FileStoreLayout(tmp_path / "f"))
            layout = FileStoreLayout(tmp_path / "f")

        resolved = OffloadWriterResolver.for_store(_FileStoreShape())

        assert isinstance(resolved, FileOffloadWriter)

    def test_resolver_selects_a_writer_for_the_in_memory_store(self) -> None:
        """The in-memory store no longer resolves to ``None``."""

        resolved = OffloadWriterResolver.for_store(InMemoryRuntimeApiStore())

        assert isinstance(resolved, InMemoryOffloadWriter)

    def test_resolver_selects_the_postgres_writer_when_a_sink_is_supplied(
        self, tmp_path: Path
    ) -> None:
        """A shared sink is what makes the Postgres store bounded."""

        resolved = OffloadWriterResolver.for_store(
            object(),
            shared_object_sink=FileObjectStore(FileStoreLayout(tmp_path / "s")),
        )

        assert isinstance(resolved, PostgresOffloadWriter)


class TestAdmissionIsBoundedOnEveryAdapter:
    """The model-bound content must be bounded identically on each store."""

    def test_oversized_result_is_offloaded_not_inlined(
        self, writer: ContentAddressedOffloadWriter
    ) -> None:
        """Every adapter takes the offload branch for the same input."""

        admission = ToolResultAdmissionAdapter(writer).admit(
            _OVERSIZED, trace_id="trace-parity"
        )

        assert admission.strategy is ContextCompressionStrategy.OFFLOAD
        assert admission.output_ref is not None

    def test_model_content_never_carries_the_raw_result(
        self, writer: ContentAddressedOffloadWriter
    ) -> None:
        """The oversized body must not reach model context on any adapter."""

        admission = ToolResultAdmissionAdapter(writer).admit(
            _OVERSIZED, trace_id="trace-parity"
        )

        assert len(admission.model_content) <= admission.model_content_limit_chars
        assert len(admission.model_content) < len(_OVERSIZED)
        assert _OVERSIZED not in admission.model_content

    def test_small_result_stays_inline_on_every_adapter(
        self, writer: ContentAddressedOffloadWriter
    ) -> None:
        """Parity covers the negative case too: no needless offload."""

        admission = ToolResultAdmissionAdapter(writer).admit(
            _SMALL, trace_id="trace-parity"
        )

        assert admission.strategy is ContextCompressionStrategy.INLINE
        assert admission.model_content == _SMALL
        assert admission.output_ref is None


class TestRawContentSurvivesOnEveryAdapter:
    """A bounded admission is only correct if the raw bytes are retrievable."""

    def test_offloaded_bytes_read_back_exactly(
        self, writer: ContentAddressedOffloadWriter
    ) -> None:
        """Each adapter returns the original content verbatim by reference."""

        admission = ToolResultAdmissionAdapter(writer).admit(
            _OVERSIZED, trace_id="trace-parity"
        )
        assert admission.output_ref is not None

        assert writer.read_offloaded(admission.output_ref).decode("utf-8") == (
            _OVERSIZED
        )

    def test_reference_digest_matches_the_admission_source_digest(
        self, writer: ContentAddressedOffloadWriter
    ) -> None:
        """The locator addresses the very bytes the admission describes."""

        admission = ToolResultAdmissionAdapter(writer).admit(
            _OVERSIZED, trace_id="trace-parity"
        )
        assert admission.output_ref is not None

        assert admission.output_ref.endswith(admission.source_digest)

    def test_storing_identical_content_twice_is_idempotent(
        self, writer: ContentAddressedOffloadWriter
    ) -> None:
        """Content addressing must not fork a second copy on any adapter."""

        first = writer(_OVERSIZED)
        second = writer(_OVERSIZED)

        assert first == second
        assert writer.read_offloaded(second).decode("utf-8") == _OVERSIZED

    def test_unknown_reference_raises_rather_than_returning_empty(
        self, writer: ContentAddressedOffloadWriter
    ) -> None:
        """A miss must never be mistaken for empty content."""

        with pytest.raises(OffloadReadError):
            writer.read_offloaded("/large_tool_results/" + "0" * 64)

    def test_malformed_reference_raises_on_every_adapter(
        self, writer: ContentAddressedOffloadWriter
    ) -> None:
        """Non-locator input is rejected identically everywhere."""

        with pytest.raises(OffloadReadError):
            writer.read_offloaded("/etc/passwd")


class TestCrossAdapterEquivalence:
    """Present on every adapter is not enough — the results must match."""

    def test_all_adapters_produce_the_same_reference_and_digest(
        self, tmp_path: Path
    ) -> None:
        """One input, one locator, regardless of which store is selected."""

        references = {
            name: _build_writer(name, tmp_path / name)(_OVERSIZED) for name in ADAPTERS
        }

        assert len(set(references.values())) == 1, references

    def test_all_adapters_produce_byte_identical_model_content(
        self, tmp_path: Path
    ) -> None:
        """A store swap must not change what the model is shown."""

        admissions = {
            name: ToolResultAdmissionAdapter(
                _build_writer(name, tmp_path / name)
            ).admit(_OVERSIZED, trace_id="trace-parity")
            for name in ADAPTERS
        }

        assert len({a.model_content for a in admissions.values()}) == 1
        assert len({a.source_digest for a in admissions.values()}) == 1
        assert len({a.preview for a in admissions.values()}) == 1
        assert len({a.strategy for a in admissions.values()}) == 1

    def test_all_adapters_record_the_same_raw_free_fact(self, tmp_path: Path) -> None:
        """Digest, size, media type and preview agree across stores."""

        facts = [
            _build_writer(name, tmp_path / name).offload(_OVERSIZED)
            for name in ADAPTERS
        ]

        assert len({f.model_dump_json() for f in facts}) == 1
        assert all(_OVERSIZED not in f.model_dump_json() for f in facts)


class TestFailClosedOnEveryAdapter:
    """Bounding must fail loudly; a silent fallback is the original defect."""

    def test_writer_failure_propagates_instead_of_inlining_raw_output(
        self, writer: ContentAddressedOffloadWriter, monkeypatch
    ) -> None:
        """A broken sink must not degrade to unbounded admission."""

        def _explode(*args: object, **kwargs: object) -> str:
            raise RuntimeError("sink unavailable")

        monkeypatch.setattr(writer, "_persist", _explode)

        with pytest.raises(RuntimeError, match="sink unavailable"):
            ToolResultAdmissionAdapter(writer).admit(
                _OVERSIZED, trace_id="trace-parity"
            )

    def test_sink_disagreeing_on_the_digest_is_rejected(
        self, writer: ContentAddressedOffloadWriter, monkeypatch
    ) -> None:
        """A locator the runtime cannot resolve is never handed back."""

        monkeypatch.setattr(writer, "_persist", lambda *a, **k: "f" * 64)

        with pytest.raises(OffloadDigestMismatch):
            writer(_OVERSIZED)

    def test_postgres_writer_refuses_construction_without_a_shared_sink(
        self,
    ) -> None:
        """Multi-node deployments must not offload to node-local scratch."""

        with pytest.raises(ValueError, match="durable shared byte sink"):
            PostgresOffloadWriter(None)  # type: ignore[arg-type]

    def test_resolver_returns_none_rather_than_guessing_a_writer(self) -> None:
        """An unknown store is unsupported, not silently unbounded."""

        assert OffloadWriterResolver.for_store(object()) is None


class TestPostgresCoverageBoundary:
    """State plainly what this suite does and does not prove for Postgres."""

    def test_postgres_ledger_row_is_not_yet_written(self, tmp_path: Path) -> None:
        """Pin the honest scope of the Postgres writer.

        The writer bounds the result and stores the bytes — proven above without
        a database, because no part of that decision reaches SQL. What is *not*
        built is the durable raw-free ``runtime_context_payloads`` row
        (``kind='tool_result'``, ``redaction_state='offloaded'``), the remaining
        ARQ-008 clause. Proving that would need ``TEST_DATABASE_URL`` pointed at
        a migrated database, as the DB-gated suite under
        ``tests/unit/runtime_adapters/postgres/`` already does.

        This test fails the moment a ledger is added, forcing the claim above to
        be re-stated rather than silently going stale.
        """

        writer = _build_writer("postgres", tmp_path)

        assert not hasattr(writer, "ledger")
        assert not hasattr(writer, "record_payload")
