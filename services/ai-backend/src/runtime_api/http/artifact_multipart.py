"""Incremental, bounded multipart parsing for artifact uploads."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from starlette.datastructures import FormData
from starlette.formparsers import MultiPartException, MultiPartParser


class ArtifactMultipartTooLarge(MultiPartException):
    """Raised before a multipart upload can exceed its absolute server cap."""


class _BoundedMultiPartParser(MultiPartParser):
    """Starlette parser with an explicit limit for file-part bytes.

    Starlette's ``max_part_size`` only applies to non-file fields. This
    subclass counts file bytes in the parser callback, before they are queued
    for ``UploadFile.write`` and before the spooled file can grow further.
    """

    def __init__(self, *args, maximum_file_bytes: int, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._maximum_file_bytes = maximum_file_bytes
        self._current_file_bytes = 0

    def on_part_begin(self) -> None:
        super().on_part_begin()
        self._current_file_bytes = 0

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self._current_part.file is not None:
            self._current_file_bytes += end - start
            if self._current_file_bytes > self._maximum_file_bytes:
                raise ArtifactMultipartTooLarge("Artifact upload is too large.")
        super().on_part_data(data, start, end)

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
            yield chunk


__all__ = ("ArtifactMultipartReader", "ArtifactMultipartTooLarge")
