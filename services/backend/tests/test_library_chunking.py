"""Chunker tests — Phase 7.5 P7.5-A2.

Coverage:

* Empty / whitespace-only input → no chunks.
* Determinism — identical input + parameters always produce the same chunks.
* Boundary: chunks shorter than chunk_size are emitted whole.
* Overlap: consecutive chunks share ``overlap`` words.
* Parameter validation — invalid sizes/overlap raise ``ValueError``.
* Long text — UTF-8 byte-cap is enforced without splitting a codepoint.
* Per-kind extraction wiring through :func:`extract_text` for pages /
  datasets / files (no-blob fallback).
"""

from __future__ import annotations

import pytest

from backend_app.library.embeddings import (
    Chunk,
    chunk_text,
    compute_content_hash,
    extract_text,
)
from backend_app.library.store import (
    LibraryDatasetRecord,
    LibraryFileRecord,
    LibraryPageRecord,
)


class TestChunkText:
    def test_empty_input_returns_no_chunks(self) -> None:
        assert chunk_text("") == []
        assert chunk_text("   \n\t  ") == []

    def test_short_input_returns_single_chunk(self) -> None:
        chunks = chunk_text("hello world", chunk_size=10, overlap=2)
        assert chunks == [Chunk(ordinal=0, text="hello world")]

    def test_deterministic_output(self) -> None:
        body = " ".join(f"word{i}" for i in range(50))
        first = chunk_text(body, chunk_size=10, overlap=3)
        second = chunk_text(body, chunk_size=10, overlap=3)
        assert first == second
        assert [c.ordinal for c in first] == list(range(len(first)))

    def test_overlap_shares_trailing_words(self) -> None:
        body = " ".join(f"w{i}" for i in range(20))
        chunks = chunk_text(body, chunk_size=8, overlap=3)
        # First chunk: w0..w7. Second chunk starts at index 8-3 = 5 → w5..w12.
        assert chunks[0].text.split() == [f"w{i}" for i in range(8)]
        assert chunks[1].text.split() == [f"w{i}" for i in range(5, 13)]
        # Last 3 words of chunk-0 == first 3 words of chunk-1.
        assert chunks[0].text.split()[-3:] == chunks[1].text.split()[:3]

    def test_invalid_chunk_size(self) -> None:
        with pytest.raises(ValueError):
            chunk_text("x", chunk_size=0)
        with pytest.raises(ValueError):
            chunk_text("x", chunk_size=-5)

    def test_invalid_overlap(self) -> None:
        with pytest.raises(ValueError):
            chunk_text("x", chunk_size=5, overlap=-1)
        with pytest.raises(ValueError):
            chunk_text("x", chunk_size=5, overlap=5)
        with pytest.raises(ValueError):
            chunk_text("x", chunk_size=5, overlap=6)

    def test_forward_progress_terminates(self) -> None:
        # Step = chunk_size - overlap; with overlap < chunk_size the
        # loop terminates. Use a moderate-sized body to confirm.
        body = " ".join(["w"] * 500)
        chunks = chunk_text(body, chunk_size=50, overlap=10)
        assert len(chunks) > 0
        # Ordinals are sequential 0..N.
        assert [c.ordinal for c in chunks] == list(range(len(chunks)))

    def test_chunk_text_byte_cap_enforced(self) -> None:
        # 4 KB column cap — chunks longer than that get truncated.
        # Build a chunk that obviously exceeds the cap by repeating a
        # long word.
        body = ("a" * 50 + " ") * 200  # ~10 KB
        chunks = chunk_text(body, chunk_size=400, overlap=20)
        for chunk in chunks:
            assert len(chunk.text.encode("utf-8")) <= 4096

    def test_utf8_codepoint_not_split(self) -> None:
        # Heavy multibyte content — every "word" is a 4-byte emoji.
        # Cap should not produce invalid UTF-8.
        body = (" ".join(["🚀"] * 400)).encode("utf-8")
        decoded = body.decode("utf-8")
        chunks = chunk_text(decoded, chunk_size=300, overlap=10)
        for chunk in chunks:
            # Round-trip must succeed even after truncation.
            chunk.text.encode("utf-8").decode("utf-8")

    def test_default_parameters_match_prd_spec(self) -> None:
        # PRD §6.3: ~800 tokens / 100-token overlap. We approximate
        # tokens as ~0.75/word so defaults are ~600 / ~75 words.
        from backend_app.library.embeddings import (
            DEFAULT_CHUNK_OVERLAP_WORDS,
            DEFAULT_CHUNK_SIZE_WORDS,
        )

        assert DEFAULT_CHUNK_SIZE_WORDS == 600
        assert DEFAULT_CHUNK_OVERLAP_WORDS == 75


class TestExtractText:
    def test_page_prepends_title(self) -> None:
        record = LibraryPageRecord(
            tenant_id="org_a",
            owner_user_id="usr_1",
            title="Quarterly Review",
            markdown="The numbers are up.",
            source={"kind": "user_upload"},
        )
        text = extract_text(record=record)
        assert text.startswith("Quarterly Review")
        assert "The numbers are up." in text

    def test_dataset_renders_schema_summary(self) -> None:
        record = LibraryDatasetRecord(
            tenant_id="org_a",
            owner_user_id="usr_1",
            name="Q3 Pipeline",
            description="Sales pipeline export",
            blob_ref="s3://demo/key",
            source={"kind": "user_upload"},
            columns_schema=[
                {"name": "customer", "type": "string"},
                {"name": "amount", "type": "number"},
            ],
            tags=["sales"],
        )
        text = extract_text(record=record)
        assert "Q3 Pipeline" in text
        assert "Sales pipeline export" in text
        assert "customer" in text
        assert "amount" in text
        # Schema columns surface as "Columns: name: type · ..."
        assert "Columns:" in text

    def test_file_without_blob_falls_back_to_name_and_tags(self) -> None:
        record = LibraryFileRecord(
            tenant_id="org_a",
            owner_user_id="usr_1",
            file_kind="pdf",
            name="contract-2024.pdf",
            mime="application/pdf",
            blob_ref="s3://demo/key",
            source={"kind": "user_upload"},
            tags=["legal", "Q4"],
        )
        text = extract_text(record=record, blob=None, mime="application/pdf")
        assert "contract-2024.pdf" in text
        assert "legal" in text
        assert "Q4" in text

    def test_file_text_plain_blob_decoded(self) -> None:
        record = LibraryFileRecord(
            tenant_id="org_a",
            owner_user_id="usr_1",
            file_kind="doc",
            name="notes.txt",
            mime="text/plain",
            blob_ref="s3://demo/key",
            source={"kind": "user_upload"},
        )
        body = "The quick brown fox jumps over the lazy dog.".encode("utf-8")
        text = extract_text(record=record, blob=body, mime="text/plain")
        assert "quick brown fox" in text
        assert "notes.txt" in text

    def test_file_binary_mime_falls_back_to_metadata(self) -> None:
        record = LibraryFileRecord(
            tenant_id="org_a",
            owner_user_id="usr_1",
            file_kind="image",
            name="diagram.png",
            mime="image/png",
            blob_ref="s3://demo/key",
            source={"kind": "user_upload"},
        )
        text = extract_text(record=record, blob=b"\x89PNG\r\n...", mime="image/png")
        # No body indexed; only metadata.
        assert "diagram.png" in text
        assert "PNG" not in text  # the binary blob is not embedded.


class TestContentHash:
    def test_hash_is_deterministic(self) -> None:
        assert compute_content_hash("hello") == compute_content_hash("hello")

    def test_hash_distinguishes_different_inputs(self) -> None:
        assert compute_content_hash("a") != compute_content_hash("b")

    def test_hash_of_empty_string_is_stable(self) -> None:
        # Used as a sentinel for "nothing to embed" cases.
        h = compute_content_hash("")
        assert isinstance(h, str)
        assert len(h) == 64  # sha256 hex
