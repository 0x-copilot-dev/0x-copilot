"""Offload writer for the Postgres runtime store.

The Postgres store keeps rows, not blobs: there is no column anywhere in the
runtime schema that holds an oversized tool result, and ``runtime_context_payloads``
is metadata-only by design (``storage_backend`` / ``storage_uri`` / ``sha256`` /
``byte_size``, no content). So the bytes must go to a byte sink the deployment
supplies, and this writer's whole job is to make that requirement *explicit and
fail-closed* rather than implicit.

That matters because a Postgres deployment is multi-node. The API process that
serves a read-back is usually not the worker process that took the offload
decision, so a sink backed by node-local scratch would produce a reference that
resolves on the writing node and 404s everywhere else — an offload that looks
successful and silently loses the result. The runtime already states this rule
for artifact blobs: ``RUNTIME_ARTIFACT_BLOB_ROOT must be an explicit absolute
durable shared root when RUNTIME_STORE_BACKEND=postgres``
(:mod:`runtime_adapters.factory`). The same rule governs offloaded tool results,
so this writer refuses to be constructed without a sink instead of degrading to
unbounded inline admission.

Everything a model or a log can observe — digest, locator, preview, media type —
comes from :class:`~runtime_adapters.offload.ContentAddressedOffloadWriter`, so a
Postgres deployment and the desktop bound the same result identically.
"""

from __future__ import annotations

from runtime_adapters.offload import (
    ContentAddressedOffloadWriter,
    OffloadByteSink,
    OffloadedPayloadFact,
    OffloadReadError,
)


class PostgresOffloadWriter(ContentAddressedOffloadWriter):
    """``OffloadWriter`` parking bytes on a deployment-supplied durable sink.

    ``sink`` must be content-addressed and reachable from every node that can
    serve a read-back —
    :class:`~runtime_adapters.file.object_store.FileObjectStore` over a shared
    volume satisfies this structurally, as does any object-storage adapter with
    the same shape.
    """

    def __init__(self, sink: OffloadByteSink) -> None:
        if sink is None:  # pragma: no cover - defensive, typed non-optional
            raise ValueError(
                "PostgresOffloadWriter requires a durable shared byte sink; "
                "refusing to admit unbounded tool results inline."
            )
        self._sink = sink

    def _persist(self, data: bytes, *, fact: OffloadedPayloadFact) -> str:
        ref = self._sink.put(
            data,
            media_type=fact.media_type,
            preview=fact.preview,
        )
        # The base class verifies this against its own digest and fails closed on
        # a mismatch, so a sink that addresses content differently cannot hand
        # back an unresolvable locator.
        digest = getattr(ref, "sha256", None)
        if isinstance(digest, str):
            return digest
        if isinstance(ref, str):
            return ref
        raise OffloadReadError(
            f"offload sink returned an unusable reference: {type(ref).__name__}"
        )

    def read_offloaded(self, reference: str) -> bytes:
        """Return the exact bytes behind ``reference``."""

        digest = self.digest_for(reference)
        if digest is None:
            raise OffloadReadError(
                f"Not a large-tool-result reference: {reference!r}",
            )
        try:
            return self._sink.get(digest)
        except OffloadReadError:
            raise
        except Exception as exc:  # noqa: BLE001 - sink errors are adapter-specific
            raise OffloadReadError(
                f"Large tool result not found: {reference!r}"
            ) from exc


__all__ = ("PostgresOffloadWriter",)
