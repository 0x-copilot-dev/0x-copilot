"""Pure security tests for artifact content headers and Range parsing."""

from __future__ import annotations

import pytest

from agent_runtime.artifacts import ArtifactRangeError
from runtime_api.http.artifact_content import ArtifactContentPolicy


class TestArtifactContentPolicy:
    def test_invalid_media_type_falls_back(self) -> None:
        assert (
            ArtifactContentPolicy.media_type("text/html\r\nX-Evil: yes")
            == "application/octet-stream"
        )

    def test_reserved_and_unicode_filename_is_safe(self) -> None:
        filename = ArtifactContentPolicy.filename("../CON\r\né.md", "ignored")
        disposition = ArtifactContentPolicy.content_disposition(filename)
        assert "/" not in filename
        assert "\r" not in disposition
        assert "\n" not in disposition
        assert "filename*=UTF-8''" in disposition

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("bytes=0-9", (0, 9)),
            ("bytes=90-", (90, 99)),
            ("bytes=-10", (90, 99)),
            ("bytes=90-999", (90, 99)),
        ],
    )
    def test_range_forms(self, value: str, expected: tuple[int, int]) -> None:
        parsed = ArtifactContentPolicy.parse_range(
            value, byte_size=100, range_supported=True
        )
        assert parsed is not None
        assert (parsed.start, parsed.end) == expected

    @pytest.mark.parametrize(
        "value",
        ["bytes=", "bytes=-0", "bytes=3-2", "bytes=a-b", "bytes=0-1,4-5"],
    )
    def test_rejects_ambiguous_ranges(self, value: str) -> None:
        with pytest.raises(ArtifactRangeError) as exc_info:
            ArtifactContentPolicy.parse_range(
                value, byte_size=100, range_supported=True
            )
        assert exc_info.value.safe_message == (
            "Requested byte range is not satisfiable."
        )
