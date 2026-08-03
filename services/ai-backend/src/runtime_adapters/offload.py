"""Cross-store contract for the result-admission offload writer.

``ToolResultAdmissionAdapter`` bounds a tool result before it reaches the model
by handing oversized content to an ``OffloadWriter`` — the
``Callable[[str], str]`` alias declared in
:mod:`agent_runtime.context.memory.summarization`. Until now exactly one store
supplied that callable (:class:`~runtime_adapters.file.offload.FileOffloadWriter`),
so bounding was a property of the *desktop* deployment rather than a property of
the runtime. On the in-memory and Postgres stores the writer resolved to
``None`` and an oversized result was admitted inline, unbounded.

That asymmetry is the defect this module removes. Bounding must not depend on
which store happens to be selected, so the parts of the decision that must not
diverge live here, once:

* the digest is ``sha256`` over the UTF-8 encoding of the content;
* the reference is ``/large_tool_results/<sha256>``;
* the stored preview is the first :attr:`ContentAddressedOffloadWriter.PREVIEW_CHARS`
  characters, and is never authoritative;
* the writer returns only that reference — never the raw content.

Subclasses choose *where the bytes land* and nothing else. A new store cannot
silently produce a different locator or a different digest, because it does not
own that code: it implements :meth:`ContentAddressedOffloadWriter._persist` and
inherits the rest.

Read-back is deliberately part of the same contract
(:meth:`ContentAddressedOffloadWriter.read_offloaded`). A writer that stores
bytes nobody can resolve has not offloaded a result, it has dropped one, and the
only way to prove the raw content survived is to fetch it back by digest.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from hashlib import sha256
import re
from typing import Protocol, runtime_checkable

from agent_runtime.api.constants import Values
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.hyperparameters.contracts import ContextHyperparameters

# Accepts the full ``/large_tool_results/<sha>`` locator as well as the forms a
# composite backend leaves after stripping its own prefix (``/<sha>``, ``<sha>``).
_REFERENCE = re.compile(r"^/?(?:large_tool_results/)?(?P<sha>[0-9a-f]{64})/?$")


class OffloadedPayloadFact(RuntimeContract):
    """The raw-free record of one offload decision.

    Every field is safe to log, persist, or compare across stores: the content
    itself is represented only by its digest and its byte size. ``preview`` is a
    bounded, non-authoritative peek retained so a store can show *something*
    without re-reading the blob.
    """

    reference: str
    digest: str
    byte_size: int
    media_type: str
    preview: str | None = None


@runtime_checkable
class OffloadByteSink(Protocol):
    """Content-addressed byte storage behind an offload writer.

    Structurally satisfied by
    :class:`~runtime_adapters.file.object_store.FileObjectStore`, so a Postgres
    deployment can park bytes on its durable shared root without this module
    importing the desktop-only file adapter.
    """

    def put(
        self,
        data: bytes,
        *,
        media_type: str = ...,
        preview: str | None = ...,
    ) -> object:
        """Store ``data`` and return a ref exposing ``.sha256``."""

    def get(self, ref: object) -> bytes:
        """Return the bytes previously stored under ``ref``."""


class ContentAddressedOffloadWriter(ABC):
    """Base ``OffloadWriter`` fixing every cross-store-visible decision.

    Instances are callable with the full content and return the virtual
    reference, matching the ``OffloadWriter = Callable[[str], str]`` alias that
    ``ContextPayloadManager.prepare_tool_output`` invokes.
    """

    #: Characters of inline peek persisted beside the blob. Never authoritative.
    #: Owned by the ``context`` section of ``hyperparameters.json``.
    PREVIEW_CHARS = ContextHyperparameters().offload_preview_chars
    #: Media type recorded for offloaded tool results on every store.
    TEXT_MEDIA_TYPE = "text/plain; charset=utf-8"

    def __call__(self, content: str) -> str:
        """Store ``content`` and return its ``/large_tool_results/<sha256>`` ref."""

        return self.offload(content).reference

    def offload(self, content: str) -> OffloadedPayloadFact:
        """Store ``content`` and return the raw-free fact describing it.

        The richer sibling of :meth:`__call__`, for callers that want the digest
        and size without re-hashing. Both paths take the same decision; only the
        return shape differs.
        """

        data = content.encode("utf-8")
        digest = sha256(data).hexdigest()
        preview = content[: self.PREVIEW_CHARS] or None
        fact = OffloadedPayloadFact(
            reference=self.reference_for(digest),
            digest=digest,
            byte_size=len(data),
            media_type=self.TEXT_MEDIA_TYPE,
            preview=preview,
        )
        stored = self._persist(data, fact=fact)
        if stored != digest:
            # A sink that content-addresses differently would hand back a
            # locator this runtime cannot resolve. Fail closed rather than
            # return a reference that reads back as "not found" later.
            raise OffloadDigestMismatch(
                f"offload sink stored {stored!r}, expected {digest!r}"
            )
        return fact

    @abstractmethod
    def _persist(self, data: bytes, *, fact: OffloadedPayloadFact) -> str:
        """Store ``data`` durably and return the digest it was stored under."""

    @abstractmethod
    def read_offloaded(self, reference: str) -> bytes:
        """Return the exact bytes behind ``reference``.

        Raises :class:`OffloadReadError` when the reference is malformed or the
        payload is absent, so a caller can never mistake a miss for empty
        content.
        """

    @staticmethod
    def reference_for(digest: str) -> str:
        """Return the virtual locator for a stored digest."""

        return f"{Values.VirtualPath.LARGE_TOOL_RESULTS_PREFIX}{digest}"

    @staticmethod
    def digest_for(reference: str) -> str | None:
        """Extract the digest from a locator, or ``None`` if it is not one."""

        if not reference:
            return None
        match = _REFERENCE.match(reference.strip())
        return match.group("sha") if match is not None else None


class OffloadError(RuntimeError):
    """Base class for offload-writer failures."""


class OffloadDigestMismatch(OffloadError):
    """A sink stored content under a digest other than the computed one."""


class OffloadReadError(OffloadError):
    """An offloaded payload could not be resolved back to its bytes."""


class OffloadWriterResolver:
    """Select the result-admission writer matching a runtime store.

    The worker holds a store, not a backend name, so resolution is duck-typed on
    what the store exposes — the same discipline
    ``FileStoreWorkerWiring.file_store`` already uses, and the reason the desktop
    adapter's sqlite/object-store dependencies stay unimported on other images.

    Returning ``None`` is reserved for a store this runtime does not know how to
    bound. It is not a fallback to inline admission: a caller that receives
    ``None`` should treat the configuration as unsupported rather than admit an
    unbounded result.
    """

    @staticmethod
    def for_store(
        store: object,
        *,
        shared_object_sink: OffloadByteSink | None = None,
    ) -> ContentAddressedOffloadWriter | None:
        """Return the writer for ``store``, or ``None`` if none applies.

        ``shared_object_sink`` is accepted for signature stability and ignored:
        every remaining store owns its own bytes.
        """

        if hasattr(store, "object_store") and hasattr(store, "layout"):
            # Desktop file runtime — imported lazily so an in-memory process
            # never loads the file adapter.
            from runtime_adapters.file.offload import FileOffloadWriter  # noqa: PLC0415

            return FileOffloadWriter(store.object_store)
        from runtime_adapters.in_memory import (  # noqa: PLC0415
            InMemoryRuntimeApiStore,
        )

        if isinstance(store, InMemoryRuntimeApiStore):
            from runtime_adapters.in_memory.offload import (  # noqa: PLC0415
                InMemoryOffloadWriter,
            )

            return InMemoryOffloadWriter()
        return None


__all__ = (
    "ContentAddressedOffloadWriter",
    "OffloadByteSink",
    "OffloadDigestMismatch",
    "OffloadError",
    "OffloadReadError",
    "OffloadWriterResolver",
    "OffloadedPayloadFact",
)
