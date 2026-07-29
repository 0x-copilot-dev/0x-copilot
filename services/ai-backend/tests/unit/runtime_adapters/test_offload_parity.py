"""Cross-store offload parity: the claims the sibling suite does not make.

``test_offload_writer_parity.py`` already pins that every store's writer exists,
produces the same locator, and bounds the same oversized result identically. That
suite bites — mutating any adapter's digest, preview length, persistence, or
fail-closed guard turns it red. This file does not repeat it.

What is added here is the part of the claim that was still taken on trust:

* **Raw-free by seeded secret, not by field name.** The sibling asserts the whole
  200k body is absent from the fact, which is true the moment anything is
  bounded. Here a secret is planted at a known offset and looked for across
  *every* field value, so the assertion cannot pass by inspecting the fields the
  author happened to think of.
* **The preview is a real, deliberate leak.** Content inside the first
  ``PREVIEW_CHARS`` reaches the persisted fact on every adapter. The fact is
  raw-free; it is *not* secret-free. Pinned so nobody treats it as redacted.
* **Errors match, not merely occur.** The sibling asserts the typed class per
  adapter; a store swap is only safe if the *message* agrees too.
* **Fail-closed against a real sink**, not a monkeypatched ``_persist`` — and the
  Postgres sink-contract branches (bare-digest, unusable reference) that nothing
  exercised.

**The wiring gap.** :class:`TestWorkerWiringDoesNotYetSelectAWriter` is the
finding, not a formality. The writers are equivalent *as classes*, but
``OffloadWriterResolver`` — the mechanism that was supposed to make bounding a
property of the runtime instead of the desktop — is called by no production code.
``FileStoreWorkerWiring`` still hardcodes ``FileOffloadWriter`` behind a file-store
gate, so on the in-memory and Postgres backends ``tool_result_admission()`` is
still ``None`` and an oversized result is still admitted inline, unbounded. Those
tests pin that live gap and fail the moment it is closed.
"""

from __future__ import annotations

import inspect
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
    OffloadedPayloadFact,
    OffloadReadError,
    OffloadWriterResolver,
)
from runtime_adapters.postgres.offload import PostgresOffloadWriter
from runtime_worker.file_store_wiring import FileStoreWorkerWiring

ADAPTERS = ("in_memory", "file", "postgres")


class OffloadAdapterMixin:
    """Builders, fakes, and seeded payloads shared by every parity class."""

    #: Planted in tool output; must never surface in a raw-free fact.
    SECRET = "s3cr3t-AKIA0BSTELLARSECRET42"
    #: Comfortably past the inline budget so the offload branch always runs.
    FILLER = "x" * 200_000
    #: Offset past *both* leading windows — the 200-char fact preview and the
    #: 2000-char excerpt the model is shown. A secret nearer the start is
    #: retained deliberately; see ``TestLeadingContentIsNotRedacted``.
    DEEP_OFFSET = 60_000

    @property
    def deep_secret_content(self) -> str:
        """Oversized output hiding the secret beyond every leading window."""

        return ("lead" * (self.DEEP_OFFSET // 4)) + self.SECRET + self.FILLER

    @property
    def leading_secret_content(self) -> str:
        """Oversized output with the secret in the first characters."""

        return self.SECRET + self.FILLER

    @staticmethod
    def build_writer(name: str, root: Path) -> ContentAddressedOffloadWriter:
        """Return the offload writer for one selectable store adapter."""

        if name == "in_memory":
            return InMemoryOffloadWriter()
        if name == "file":
            return FileOffloadWriter(FileObjectStore(FileStoreLayout(root / "file")))
        if name == "postgres":
            # What a Postgres deployment must supply: a content-addressed sink on
            # a durable shared root. No database is involved in the decision.
            return PostgresOffloadWriter(
                FileObjectStore(FileStoreLayout(root / "shared"))
            )
        raise AssertionError(f"unknown adapter {name!r}")

    @staticmethod
    def field_values(fact: OffloadedPayloadFact) -> list[str]:
        """Every field value as text, so no field name is privileged."""

        return [str(value) for value in fact.model_dump().values()]

    @pytest.fixture(params=ADAPTERS)
    def adapter_name(self, request: pytest.FixtureRequest) -> str:
        """Name the selectable runtime store adapter under test."""

        return request.param

    @pytest.fixture
    def writer(
        self, adapter_name: str, tmp_path: Path
    ) -> ContentAddressedOffloadWriter:
        """Yield the result-admission writer for each store adapter."""

        return self.build_writer(adapter_name, tmp_path)


class FakeSinkMixin:
    """Deployment-supplied sinks that misbehave in the ways that matter."""

    class ExplodingSink:
        """A durable sink that is simply unavailable."""

        def put(self, data: bytes, *, media_type: str = "", preview: str | None = None):
            raise OSError("disk full")

        def get(self, ref: object) -> bytes:
            raise OSError("sink unreachable")

    class BareDigestSink:
        """A sink returning the digest as a plain string rather than a ref."""

        def __init__(self) -> None:
            self.blobs: dict[str, bytes] = {}

        def put(
            self, data: bytes, *, media_type: str = "", preview: str | None = None
        ) -> str:
            from hashlib import sha256

            digest = sha256(data).hexdigest()
            self.blobs[digest] = data
            return digest

        def get(self, ref: object) -> bytes:
            return self.blobs[str(ref)]

    class UnusableReferenceSink:
        """A sink returning something that addresses nothing."""

        def put(
            self, data: bytes, *, media_type: str = "", preview: str | None = None
        ) -> object:
            return object()

        def get(self, ref: object) -> bytes:
            raise AssertionError("never reached")


class TestPersistedFactIsRawFree(OffloadAdapterMixin):
    """A seeded secret, hunted across every field value on every adapter."""

    def test_secret_beyond_the_preview_window_is_absent_from_the_fact(
        self, writer: ContentAddressedOffloadWriter
    ) -> None:
        """The bounded fact must not carry content it only references."""

        fact = writer.offload(self.deep_secret_content)

        assert self.SECRET not in fact.model_dump_json()

    def test_secret_is_absent_from_every_field_not_only_the_expected_ones(
        self, writer: ContentAddressedOffloadWriter
    ) -> None:
        """Sweep all field values, so a new leaky field cannot slip through."""

        fact = writer.offload(self.deep_secret_content)

        leaked = [value for value in self.field_values(fact) if self.SECRET in value]
        assert leaked == []
        assert self.SECRET not in repr(fact)

    def test_the_fact_carries_only_a_digest_and_a_size_for_the_body(
        self, writer: ContentAddressedOffloadWriter
    ) -> None:
        """What replaces the content must still address it exactly."""

        content = self.deep_secret_content
        fact = writer.offload(content)

        assert fact.byte_size == len(content.encode("utf-8"))
        assert fact.reference.endswith(fact.digest)
        assert writer.read_offloaded(fact.reference).decode("utf-8") == content

    def test_model_content_never_carries_the_secret_on_any_adapter(
        self, writer: ContentAddressedOffloadWriter
    ) -> None:
        """The point of bounding: the body does not reach the model."""

        admission = ToolResultAdmissionAdapter(writer).admit(
            self.deep_secret_content, trace_id="trace-secret"
        )

        assert admission.strategy is ContextCompressionStrategy.OFFLOAD
        assert self.SECRET not in admission.model_content


class TestLeadingContentIsNotRedacted(OffloadAdapterMixin):
    """Bounding truncates; it does not redact — pinned on every adapter.

    Two distinct leading windows survive an offload, and conflating them is easy:
    the persisted fact keeps ``PREVIEW_CHARS`` (200) characters, while the model
    is shown a much larger excerpt (2000 characters inside a 4096-char budget).
    Content in either window is retained verbatim, by design, so neither the fact
    nor the model context may be treated as a redacted record. Asserted rather
    than assumed so a future redaction change has to come here and restate it.
    """

    def test_leading_characters_reach_the_persisted_fact_through_the_preview(
        self, writer: ContentAddressedOffloadWriter
    ) -> None:
        """A secret in the first 200 characters *is* persisted, everywhere."""

        fact = writer.offload(self.leading_secret_content)

        assert fact.preview is not None
        assert self.SECRET in fact.preview
        assert self.SECRET in fact.model_dump_json()

    def test_leading_characters_also_reach_the_model_excerpt(
        self, writer: ContentAddressedOffloadWriter
    ) -> None:
        """The offload notice carries a head excerpt, not a redaction."""

        admission = ToolResultAdmissionAdapter(writer).admit(
            self.leading_secret_content, trace_id="trace-leading"
        )

        assert admission.strategy is ContextCompressionStrategy.OFFLOAD
        assert self.SECRET in admission.model_content

    def test_every_adapter_retains_exactly_the_same_fact_prefix(
        self, tmp_path: Path
    ) -> None:
        """The retention is identical across stores, not merely present."""

        previews = {
            name: self.build_writer(name, tmp_path / name)
            .offload(self.leading_secret_content)
            .preview
            for name in ADAPTERS
        }

        assert len(set(previews.values())) == 1, previews
        assert len(next(iter(previews.values()))) == (
            ContentAddressedOffloadWriter.PREVIEW_CHARS
        )

    def test_every_adapter_shows_the_model_the_same_bounded_excerpt(
        self, tmp_path: Path
    ) -> None:
        """A store swap must not widen or narrow what the model is shown."""

        admissions = {
            name: ToolResultAdmissionAdapter(
                self.build_writer(name, tmp_path / name)
            ).admit(self.leading_secret_content, trace_id="trace-leading")
            for name in ADAPTERS
        }

        assert len({a.model_content for a in admissions.values()}) == 1
        for admission in admissions.values():
            assert len(admission.model_content) <= admission.model_content_limit_chars


class TestReadErrorParity(OffloadAdapterMixin):
    """A store swap is safe only if failures read identically too."""

    MISSING = "/large_tool_results/" + "0" * 64

    def test_missing_payload_raises_the_same_typed_error_and_message_everywhere(
        self, tmp_path: Path
    ) -> None:
        """One absent reference, one typed error, one message."""

        seen = {}
        for name in ADAPTERS:
            writer = self.build_writer(name, tmp_path / name)
            with pytest.raises(OffloadReadError) as excinfo:
                writer.read_offloaded(self.MISSING)
            seen[name] = str(excinfo.value)

        assert len(set(seen.values())) == 1, seen
        assert "Large tool result not found" in next(iter(seen.values()))

    def test_malformed_reference_raises_the_same_typed_error_and_message_everywhere(
        self, tmp_path: Path
    ) -> None:
        """A non-locator is rejected identically, never resolved."""

        seen = {}
        for name in ADAPTERS:
            writer = self.build_writer(name, tmp_path / name)
            with pytest.raises(OffloadReadError) as excinfo:
                writer.read_offloaded("/etc/passwd")
            seen[name] = str(excinfo.value)

        assert len(set(seen.values())) == 1, seen
        assert "Not a large-tool-result reference" in next(iter(seen.values()))

    def test_empty_reference_is_rejected_rather_than_treated_as_a_miss(
        self, writer: ContentAddressedOffloadWriter
    ) -> None:
        """Empty input is malformed, not an absent payload."""

        with pytest.raises(OffloadReadError, match="Not a large-tool-result reference"):
            writer.read_offloaded("")

    def test_read_error_message_does_not_leak_sink_internals(
        self, adapter_name: str, tmp_path: Path
    ) -> None:
        """The safe public message names the reference, never the storage path."""

        writer = self.build_writer(adapter_name, tmp_path)

        with pytest.raises(OffloadReadError) as excinfo:
            writer.read_offloaded(self.MISSING)

        message = str(excinfo.value)
        assert str(tmp_path) not in message
        assert "Traceback" not in message
        assert "objects/sha256" not in message


class TestFailClosedWithARealSink(OffloadAdapterMixin, FakeSinkMixin):
    """Fail-closed proven through the sink contract, not by patching internals."""

    def test_failing_sink_propagates_instead_of_degrading_to_inline_admission(
        self,
    ) -> None:
        """An unavailable sink must not quietly admit the raw result."""

        writer = PostgresOffloadWriter(self.ExplodingSink())

        with pytest.raises(OSError, match="disk full"):
            ToolResultAdmissionAdapter(writer).admit(
                self.deep_secret_content, trace_id="trace-sink"
            )

    def test_sink_returning_a_bare_digest_string_is_accepted(self) -> None:
        """The documented non-``ObjectRef`` sink shape still resolves."""

        sink = self.BareDigestSink()
        writer = PostgresOffloadWriter(sink)

        reference = writer(self.deep_secret_content)

        assert reference.startswith("/large_tool_results/")
        assert writer.read_offloaded(reference).decode("utf-8") == (
            self.deep_secret_content
        )

    def test_sink_returning_an_unusable_reference_raises_a_typed_error(self) -> None:
        """A locator the runtime cannot resolve is refused at write time."""

        writer = PostgresOffloadWriter(self.UnusableReferenceSink())

        with pytest.raises(OffloadReadError, match="unusable reference"):
            writer(self.deep_secret_content)

    def test_unreachable_sink_read_is_mapped_to_the_typed_offload_error(self) -> None:
        """Adapter-specific sink failures never escape as raw sink errors."""

        writer = PostgresOffloadWriter(self.ExplodingSink())

        with pytest.raises(OffloadReadError, match="Large tool result not found"):
            writer.read_offloaded("/large_tool_results/" + "a" * 64)


class TestBoundaryContentParity(OffloadAdapterMixin):
    """The edges must agree too, not just the comfortable middle."""

    def test_empty_content_produces_an_identical_fact_on_every_adapter(
        self, tmp_path: Path
    ) -> None:
        """Zero-length output is bounded the same way everywhere."""

        facts = [
            self.build_writer(name, tmp_path / name).offload("") for name in ADAPTERS
        ]

        assert len({fact.model_dump_json() for fact in facts}) == 1
        assert facts[0].preview is None
        assert facts[0].byte_size == 0

    def test_round_trip_survives_non_ascii_payloads_on_every_adapter(
        self, writer: ContentAddressedOffloadWriter
    ) -> None:
        """Digest and read-back are byte-exact for multi-byte content."""

        content = ("héllo-🌍-" * 40_000) + self.SECRET

        reference = writer(content)

        assert writer.read_offloaded(reference).decode("utf-8") == content


class TestResolverPrecedence(OffloadAdapterMixin):
    """Pin which writer wins when more than one arm could match."""

    def test_a_shared_sink_outranks_the_in_memory_store(self, tmp_path: Path) -> None:
        """Supplying a durable sink selects the durable writer.

        Precedence worth pinning: an in-memory store *with* a shared sink
        resolves to the Postgres writer, not the in-memory one, because a
        deployment that went to the trouble of supplying durable shared storage
        should not have its bytes parked in a process-local dict.
        """

        resolved = OffloadWriterResolver.for_store(
            InMemoryRuntimeApiStore(),
            shared_object_sink=FileObjectStore(FileStoreLayout(tmp_path / "sink")),
        )

        assert isinstance(resolved, PostgresOffloadWriter)

    def test_the_file_store_ignores_a_shared_sink_and_keeps_its_own_bytes(
        self, tmp_path: Path
    ) -> None:
        """A store that owns its bytes is never redirected."""

        class _FileStoreShape:
            layout = FileStoreLayout(tmp_path / "f")
            object_store = FileObjectStore(FileStoreLayout(tmp_path / "f"))

        resolved = OffloadWriterResolver.for_store(
            _FileStoreShape(),
            shared_object_sink=FileObjectStore(FileStoreLayout(tmp_path / "sink")),
        )

        assert isinstance(resolved, FileOffloadWriter)


class TestWorkerWiringDoesNotYetSelectAWriter(OffloadAdapterMixin):
    """The gap between equivalent writers and a bounded runtime.

    Everything above proves the three writers are interchangeable. None of it
    proves the runtime *uses* them. It does not: ``OffloadWriterResolver`` is
    referenced by no production code, and the worker still reaches for
    ``FileOffloadWriter`` behind a file-store gate. On the in-memory and Postgres
    backends the admission adapter is still ``None``, which is the original
    unbounded-inline behaviour the cross-store contract was written to end.

    These tests assert the defect as it currently stands, so they go red the
    moment the resolver is wired in — at which point the claim that bounding is a
    property of the runtime can finally be made, and this class deleted.
    """

    class PostgresShapedStore:
        """An event store exposing neither ``object_store`` nor ``layout``."""

    class FileShapedStore:
        """Duck-typed as the desktop file store."""

        def __init__(self, root: Path) -> None:
            self.layout = FileStoreLayout(root)
            self.object_store = FileObjectStore(self.layout)

    def test_the_file_store_is_the_only_backend_that_gets_bounded(
        self, tmp_path: Path
    ) -> None:
        """The asymmetry the implementation set out to remove is still live."""

        file_wiring = FileStoreWorkerWiring(self.FileShapedStore(tmp_path / "f"))
        memory_wiring = FileStoreWorkerWiring(InMemoryRuntimeApiStore())
        postgres_wiring = FileStoreWorkerWiring(self.PostgresShapedStore())

        assert isinstance(
            file_wiring.tool_result_admission(), ToolResultAdmissionAdapter
        )
        assert memory_wiring.tool_result_admission() is None
        assert postgres_wiring.tool_result_admission() is None

    def test_non_file_backends_get_no_tool_result_offloader_either(self) -> None:
        """Nothing downstream picks the bounding back up."""

        assert (
            FileStoreWorkerWiring(InMemoryRuntimeApiStore()).tool_result_offloader()
            is None
        )
        assert (
            FileStoreWorkerWiring(self.PostgresShapedStore()).tool_result_offloader()
            is None
        )

    def test_the_worker_wiring_cannot_be_given_a_shared_sink(self) -> None:
        """The Postgres writer's one requirement cannot even be supplied.

        ``PostgresOffloadWriter`` refuses construction without a durable shared
        sink. ``FileStoreWorkerWiring`` takes an event store and nothing else, so
        there is no seam through which a deployment could pass one — structural
        proof that the Postgres arm of the resolver is unreachable in the worker
        as wired today.
        """

        parameters = inspect.signature(FileStoreWorkerWiring.__init__).parameters

        assert set(parameters) == {"self", "event_store"}

    def test_the_resolver_is_not_consulted_by_the_worker_wiring(self) -> None:
        """The selection mechanism is orphaned; the hardcoded writer is used."""

        source = inspect.getsource(FileStoreWorkerWiring)

        assert "OffloadWriterResolver" not in source
        assert "FileOffloadWriter" in source
