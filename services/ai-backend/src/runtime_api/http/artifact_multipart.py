"""Incremental, bounded multipart parsing for artifact uploads."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from fastapi import Request
from starlette.datastructures import FormData
from starlette.formparsers import MultiPartException, MultiPartParser

_SPOOL_MEMORY_BYTES = 1 * 1024 * 1024
_STREAM_CHUNK_BYTES = 64 * 1024


class ArtifactMultipartTooLarge(MultiPartException):
    """Raised before a multipart upload can exceed its absolute server cap."""


class ArtifactMultipartInvalid(MultiPartException):
    """Raised before an unsafe or ambiguous multipart layout can spool bytes."""


class ArtifactUploadAdmission:
    """Process-wide backpressure for concurrently spooled artifact uploads."""

    def __init__(self, maximum_concurrent_uploads: int) -> None:
        if maximum_concurrent_uploads < 1:
            raise ValueError("maximum_concurrent_uploads must be positive")
        self.maximum_concurrent_uploads = maximum_concurrent_uploads
        self._slots = asyncio.BoundedSemaphore(maximum_concurrent_uploads)

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        await self._slots.acquire()
        try:
            yield
        finally:
            self._slots.release()


PROCESS_ARTIFACT_UPLOAD_ADMISSION = ArtifactUploadAdmission(4)


class _BoundedMultiPartParser(MultiPartParser):
    """Starlette parser with an explicit limit for file-part bytes.

    Starlette's ``max_part_size`` only applies to non-file fields. This
    subclass counts file bytes in the parser callback, before they are queued
    for ``UploadFile.write`` and before the spooled file can grow further.
    """

    # Intentional disk-backpressure boundary: each upload retains at most 1 MiB
    # in the Starlette spool before rolling to a temporary file.
    spool_max_size = _SPOOL_MEMORY_BYTES

    def __init__(
        self,
        *args,
        maximum_file_bytes: int,
        kind_file_limits: Mapping[str, int] | None = None,
        content_field_name: str = "content",
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._maximum_file_bytes = maximum_file_bytes
        self._kind_file_limits = dict(kind_file_limits or {})
        self._content_field_name = content_field_name
        self._selected_kind: str | None = None
        self._current_file_bytes = 0

    def on_part_begin(self) -> None:
        super().on_part_begin()
        self._current_file_bytes = 0

    def on_headers_finished(self) -> None:
        super().on_headers_finished()
        if self._current_part.file is None:
            return
        if self._current_part.field_name != self._content_field_name:
            raise ArtifactMultipartInvalid("Unexpected artifact file field.")
        if self._kind_file_limits and self._selected_kind is None:
            raise ArtifactMultipartInvalid(
                "Artifact kind must precede artifact content."
            )

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self._current_part.file is not None:
            self._current_file_bytes += end - start
            if self._current_file_bytes > self._maximum_file_bytes:
                raise ArtifactMultipartTooLarge("Artifact upload is too large.")
        super().on_part_data(data, start, end)

    def on_part_end(self) -> None:
        super().on_part_end()
        if (
            self._current_part.file is not None
            or self._current_part.field_name != "kind"
            or not self._kind_file_limits
        ):
            return
        if self._selected_kind is not None:
            raise ArtifactMultipartInvalid("Artifact kind must be unique.")
        kind = bytes(self._current_part.data).decode(self._charset, errors="replace")
        selected_limit = self._kind_file_limits.get(kind)
        if selected_limit is None:
            raise ArtifactMultipartInvalid("Artifact kind is invalid.")
        self._selected_kind = kind
        self._maximum_file_bytes = selected_limit

    def close_files_on_error(self) -> None:
        """Close every parser-owned spool after disconnect or parser failure."""

        for file in self._files_to_close_on_error:
            file.close()


class ArtifactMultipartReader:
    """Parse one multipart request without buffering or trusting length headers."""

    @classmethod
    async def parse(
        cls,
        request: Request,
        *,
        maximum_file_bytes: int,
        maximum_overhead_bytes: int,
        maximum_files: int,
        maximum_fields: int,
        maximum_field_bytes: int,
        kind_file_limits: Mapping[str, int] | None = None,
    ) -> FormData:
        maximum_request_bytes = maximum_file_bytes + maximum_overhead_bytes
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                declared_bytes = int(declared)
            except ValueError:
                declared_bytes = 0
            if declared_bytes > maximum_request_bytes:
                raise ArtifactMultipartTooLarge("Artifact upload is too large.")

        parser = _BoundedMultiPartParser(
            headers=request.headers,
            stream=cls._bounded_stream(
                request=request,
                maximum_request_bytes=maximum_request_bytes,
            ),
            max_files=maximum_files,
            max_fields=maximum_fields,
            max_part_size=maximum_field_bytes,
            maximum_file_bytes=maximum_file_bytes,
            kind_file_limits=kind_file_limits,
        )
        try:
            return await parser.parse()
        except BaseException:
            # ``MultiPartParser`` closes files for MultiPartException only.
            # ClientDisconnect/cancellation/parser bugs must release spools too.
            parser.close_files_on_error()
            raise

    @staticmethod
    async def _bounded_stream(
        *,
        request: Request,
        maximum_request_bytes: int,
    ) -> AsyncIterator[bytes]:
        consumed = 0
        async for chunk in request.stream():
            consumed += len(chunk)
            if consumed > maximum_request_bytes:
                raise ArtifactMultipartTooLarge("Artifact upload is too large.")
            for offset in range(0, len(chunk), _STREAM_CHUNK_BYTES):
                yield chunk[offset : offset + _STREAM_CHUNK_BYTES]


__all__ = (
    "ArtifactMultipartInvalid",
    "ArtifactMultipartReader",
    "ArtifactMultipartTooLarge",
    "ArtifactUploadAdmission",
    "PROCESS_ARTIFACT_UPLOAD_ADMISSION",
)
