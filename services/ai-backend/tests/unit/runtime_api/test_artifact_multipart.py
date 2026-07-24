"""Adversarial tests for bounded incremental artifact multipart parsing."""

from __future__ import annotations

import asyncio
import inspect
from tempfile import SpooledTemporaryFile

import pytest
from starlette.datastructures import Headers, UploadFile
from starlette.formparsers import MultiPartParser

from runtime_api.http.artifact_multipart import (
    ArtifactMultipartInvalid,
    ArtifactMultipartReader,
    ArtifactMultipartTooLarge,
    ArtifactUploadAdmission,
    _BoundedMultiPartParser,
)


class _ChunkedRequest:
    def __init__(
        self,
        *,
        body: bytes,
        boundary: str,
        declared_length: str | None = None,
        chunk_bytes: int = 7,
    ) -> None:
        raw = [(b"content-type", f"multipart/form-data; boundary={boundary}".encode())]
        if declared_length is not None:
            raw.append((b"content-length", declared_length.encode()))
        self.headers = Headers(raw=raw)
        self._chunks = tuple(
            body[offset : offset + chunk_bytes]
            for offset in range(0, len(body), chunk_bytes)
        )
        self.consumed_chunks = 0
        self.stream_called = False

    async def stream(self):
        self.stream_called = True
        for chunk in self._chunks:
            self.consumed_chunks += 1
            yield chunk


class MultipartFixture:
    @staticmethod
    def body(
        *,
        boundary: str,
        content: bytes,
        kind: str = "file",
        content_first: bool = False,
    ) -> bytes:
        kind_part = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="kind"\r\n\r\n'
            f"{kind}\r\n"
        ).encode()
        content_part = (
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="content"; filename="x.bin"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode()
            + content
            + b"\r\n"
        )
        return (
            content_part + kind_part if content_first else kind_part + content_part
        ) + f"--{boundary}--\r\n".encode()


class TestArtifactMultipartReader(MultipartFixture):
    def test_starlette_private_file_cleanup_and_callback_canary(self) -> None:
        """Fail loudly if the private Starlette seam this cap hardens changes."""

        async def empty_stream():
            if False:
                yield b""

        parser = _BoundedMultiPartParser(
            headers=Headers({"content-type": "multipart/form-data; boundary=canary"}),
            stream=empty_stream(),
            maximum_file_bytes=1,
        )
        upstream = inspect.getsource(MultiPartParser.on_part_data)

        assert "on_part_data" in MultiPartParser.__dict__
        assert "_current_part.file is None" in upstream
        assert "max_part_size" in upstream
        assert "_files_to_close_on_error" in vars(parser)
        assert isinstance(parser._files_to_close_on_error, list)
        assert parser.spool_max_size == 1024 * 1024
        assert "on_headers_finished" in MultiPartParser.__dict__
        assert "on_part_end" in MultiPartParser.__dict__

    @pytest.mark.asyncio
    async def test_multichunk_file_is_incremental_and_never_uses_body_helpers(
        self,
    ) -> None:
        boundary = "incremental"
        content = b"0123456789" * 10
        request = _ChunkedRequest(
            body=self.body(boundary=boundary, content=content),
            boundary=boundary,
            chunk_bytes=9,
        )

        form = await ArtifactMultipartReader.parse(
            request,  # type: ignore[arg-type] - deliberately only exposes stream()
            maximum_file_bytes=len(content),
            maximum_overhead_bytes=4096,
            maximum_files=1,
            maximum_fields=2,
            maximum_field_bytes=128,
            kind_file_limits={"file": len(content)},
        )
        upload = form["content"]
        assert isinstance(upload, UploadFile)
        assert await upload.read() == content
        assert request.stream_called is True
        assert request.consumed_chunks > 3
        await form.close()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("declared_length", [None, "1"])
    async def test_selected_kind_cap_closes_spool_and_never_writes_past_cap(
        self,
        monkeypatch: pytest.MonkeyPatch,
        declared_length: str | None,
    ) -> None:
        boundary = "cleanup"
        content = b"x" * 96
        request = _ChunkedRequest(
            body=self.body(boundary=boundary, content=content),
            boundary=boundary,
            declared_length=declared_length,
            chunk_bytes=8,
        )
        created: list[SpooledTemporaryFile[bytes]] = []
        written = 0
        real_spool = SpooledTemporaryFile
        real_write = UploadFile.write

        def tracking_spool(*args, **kwargs):
            spool = real_spool(*args, **kwargs)
            created.append(spool)
            return spool

        async def tracking_write(upload: UploadFile, data: bytes) -> None:
            nonlocal written
            written += len(data)
            await real_write(upload, data)

        monkeypatch.setattr(
            "starlette.formparsers.SpooledTemporaryFile",
            tracking_spool,
        )
        monkeypatch.setattr(UploadFile, "write", tracking_write)

        with pytest.raises(ArtifactMultipartTooLarge):
            await ArtifactMultipartReader.parse(
                request,  # type: ignore[arg-type]
                maximum_file_bytes=96,
                maximum_overhead_bytes=4096,
                maximum_files=1,
                maximum_fields=2,
                maximum_field_bytes=128,
                kind_file_limits={"file": 24},
            )

        assert request.stream_called is True
        assert request.consumed_chunks < len(request._chunks)
        assert written <= 24
        assert len(created) == 1
        assert created[0].closed is True

    @pytest.mark.asyncio
    async def test_content_before_kind_is_rejected_before_any_file_write(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        boundary = "content-first"
        request = _ChunkedRequest(
            body=self.body(
                boundary=boundary,
                content=b"must-not-spool",
                content_first=True,
            ),
            boundary=boundary,
            chunk_bytes=11,
        )
        created: list[SpooledTemporaryFile[bytes]] = []
        writes = 0
        real_spool = SpooledTemporaryFile
        real_write = UploadFile.write

        def tracking_spool(*args, **kwargs):
            spool = real_spool(*args, **kwargs)
            created.append(spool)
            return spool

        async def tracking_write(upload: UploadFile, data: bytes) -> None:
            nonlocal writes
            writes += len(data)
            await real_write(upload, data)

        monkeypatch.setattr(
            "starlette.formparsers.SpooledTemporaryFile",
            tracking_spool,
        )
        monkeypatch.setattr(UploadFile, "write", tracking_write)

        with pytest.raises(
            ArtifactMultipartInvalid,
            match="kind must precede",
        ):
            await ArtifactMultipartReader.parse(
                request,  # type: ignore[arg-type]
                maximum_file_bytes=96,
                maximum_overhead_bytes=4096,
                maximum_files=1,
                maximum_fields=2,
                maximum_field_bytes=128,
                kind_file_limits={"file": 24},
            )

        assert writes == 0
        assert len(created) == 1
        assert created[0].closed is True

    @pytest.mark.asyncio
    async def test_spool_rolls_to_disk_with_bounded_parser_writes_and_cleanup(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        boundary = "disk-backpressure"
        content = b"x" * (1024 * 1024 + 128 * 1024)
        request = _ChunkedRequest(
            body=self.body(boundary=boundary, content=content),
            boundary=boundary,
            chunk_bytes=len(content) + 4096,
        )
        largest_write = 0
        real_write = UploadFile.write

        async def tracking_write(upload: UploadFile, data: bytes) -> None:
            nonlocal largest_write
            largest_write = max(largest_write, len(data))
            await real_write(upload, data)

        monkeypatch.setattr(UploadFile, "write", tracking_write)
        form = await ArtifactMultipartReader.parse(
            request,  # type: ignore[arg-type]
            maximum_file_bytes=2 * 1024 * 1024,
            maximum_overhead_bytes=4096,
            maximum_files=1,
            maximum_fields=2,
            maximum_field_bytes=128,
            kind_file_limits={"file": 2 * 1024 * 1024},
        )
        upload = form["content"]
        assert isinstance(upload, UploadFile)
        assert upload.size == len(content)
        assert getattr(upload.file, "_rolled") is True
        assert largest_write <= 64 * 1024

        await form.close()
        assert upload.file.closed is True

    @pytest.mark.asyncio
    async def test_upload_admission_applies_process_wide_backpressure(self) -> None:
        admission = ArtifactUploadAdmission(1)
        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        second_entered = asyncio.Event()

        async def first() -> None:
            async with admission.slot():
                first_entered.set()
                await release_first.wait()

        async def second() -> None:
            await first_entered.wait()
            async with admission.slot():
                second_entered.set()

        first_task = asyncio.create_task(first())
        second_task = asyncio.create_task(second())
        await first_entered.wait()
        await asyncio.sleep(0)
        assert second_entered.is_set() is False

        release_first.set()
        await asyncio.gather(first_task, second_task)
        assert second_entered.is_set() is True
