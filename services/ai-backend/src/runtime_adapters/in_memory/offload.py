"""In-memory offload writer for the test/dev runtime store.

The in-memory store is a supported selection (``RUNTIME_STORE_BACKEND`` defaults
to it for tests and local development), so it needs the same result-admission
bounding as the durable stores. Without a writer here, every test and every dev
run exercised the *unbounded* admission path while the desktop exercised the
bounded one — the two configurations disagreeing about whether an oversized tool
result reaches the model is precisely the asymmetry the cross-store contract
exists to prevent.

Bytes live in a process-local content-addressed dict with the same lifetime as
the store that owns it. That is the correct durability for this backend — the
in-memory runtime store makes the identical trade for conversations, runs, and
events — and it is enough to satisfy the contract's real requirement: the raw
content is retrievable by digest for as long as the run can refer to it.
"""

from __future__ import annotations

from threading import Lock

from runtime_adapters.offload import (
    ContentAddressedOffloadWriter,
    OffloadedPayloadFact,
    OffloadReadError,
)


class InMemoryOffloadWriter(ContentAddressedOffloadWriter):
    """Content-addressed ``OffloadWriter`` backed by a process-local dict.

    Storing identical content twice is idempotent and keeps one copy, matching
    the content-addressed behaviour of the durable stores.
    """

    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}
        # Tools in one turn can run in parallel threads, so the map is guarded
        # even though each store instance is usually single-threaded.
        self._lock = Lock()

    def _persist(self, data: bytes, *, fact: OffloadedPayloadFact) -> str:
        with self._lock:
            self._blobs.setdefault(fact.digest, data)
        return fact.digest

    def read_offloaded(self, reference: str) -> bytes:
        """Return the exact bytes behind ``reference``."""

        digest = self.digest_for(reference)
        if digest is None:
            raise OffloadReadError(
                f"Not a large-tool-result reference: {reference!r}",
            )
        with self._lock:
            data = self._blobs.get(digest)
        if data is None:
            raise OffloadReadError(f"Large tool result not found: {reference!r}")
        return data

    def __len__(self) -> int:
        """Return the number of distinct payloads retained."""

        with self._lock:
            return len(self._blobs)


__all__ = ("InMemoryOffloadWriter",)
