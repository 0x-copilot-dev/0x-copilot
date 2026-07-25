"""Pure HTTP policy for safe artifact downloads and byte ranges."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote

from agent_runtime.artifacts import ArtifactRangeError, ByteRange


class ArtifactContentPolicy:
    """Validate untrusted metadata before it becomes an HTTP header."""

    DEFAULT_MEDIA_TYPE = "application/octet-stream"
    DEFAULT_FILENAME = "artifact"
    _CONTROL = re.compile(r"[\x00-\x1f\x7f]")
    _MEDIA_TYPE = re.compile(
        r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+/"
        r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+"
        r"(?:\s*;\s*[!#$%&'*+\-.^_`|~0-9A-Za-z]+="
        r"(?:[!#$%&'*+\-.^_`|~0-9A-Za-z]+|\"[^\x00-\x1f\x7f\"]*\"))*$"
    )
    _WINDOWS_RESERVED = frozenset(
        {"CON", "PRN", "AUX", "NUL"}
        | {f"COM{index}" for index in range(1, 10)}
        | {f"LPT{index}" for index in range(1, 10)}
    )

    @classmethod
    def media_type(cls, value: str) -> str:
        candidate = value.strip()
        if cls._MEDIA_TYPE.fullmatch(candidate) is None:
            return cls.DEFAULT_MEDIA_TYPE
        return candidate

    @classmethod
    def filename(cls, suggested: str | None, title: str) -> str:
        candidate = suggested or title or cls.DEFAULT_FILENAME
        candidate = cls._CONTROL.sub("_", candidate)
        candidate = candidate.replace("/", "_").replace("\\", "_")
        candidate = candidate.strip().strip(". ")
        if not candidate:
            candidate = cls.DEFAULT_FILENAME
        stem = candidate.split(".", maxsplit=1)[0].upper()
        if stem in cls._WINDOWS_RESERVED:
            candidate = f"_{candidate}"
        encoded = candidate.encode("utf-8")[:240]
        while encoded:
            try:
                return encoded.decode("utf-8")
            except UnicodeDecodeError:
                encoded = encoded[:-1]
        return cls.DEFAULT_FILENAME

    @classmethod
    def content_disposition(cls, filename: str) -> str:
        normalized = unicodedata.normalize("NFKD", filename)
        fallback = normalized.encode("ascii", "ignore").decode("ascii")
        fallback = cls._CONTROL.sub("_", fallback).replace('"', "_")
        fallback = fallback.strip().strip(". ") or cls.DEFAULT_FILENAME
        fallback = fallback[:150]
        encoded = quote(filename, safe="")
        return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"

    @staticmethod
    def etag(content_digest: str) -> str:
        return f'"{content_digest}"'

    @classmethod
    def parse_range(
        cls,
        value: str | None,
        *,
        byte_size: int,
        range_supported: bool,
    ) -> ByteRange | None:
        if value is None:
            return None
        if not range_supported or byte_size <= 0:
            raise ArtifactRangeError()
        unit, separator, raw_range = value.strip().partition("=")
        if unit.lower() != "bytes" or not separator or "," in raw_range:
            raise ArtifactRangeError()
        start_text, dash, end_text = raw_range.strip().partition("-")
        if not dash or (not start_text and not end_text):
            raise ArtifactRangeError()
        try:
            if not start_text:
                suffix = int(end_text)
                if suffix <= 0:
                    raise ArtifactRangeError()
                start = max(0, byte_size - suffix)
                end = byte_size - 1
            else:
                start = int(start_text)
                if start < 0 or start >= byte_size:
                    raise ArtifactRangeError()
                if end_text:
                    requested_end = int(end_text)
                    if requested_end < start:
                        raise ArtifactRangeError()
                    end = min(requested_end, byte_size - 1)
                else:
                    end = byte_size - 1
        except ValueError as exc:
            raise ArtifactRangeError() from exc
        return ByteRange(start=start, end=end)

    @classmethod
    def response_headers(
        cls,
        *,
        media_type: str,
        filename: str,
        content_digest: str,
        byte_size: int,
        range_supported: bool,
        byte_range: ByteRange | None,
    ) -> dict[str, str]:
        headers = {
            "Content-Type": cls.media_type(media_type),
            "Content-Length": str(
                byte_range.length if byte_range is not None else byte_size
            ),
            "Content-Disposition": cls.content_disposition(filename),
            "ETag": cls.etag(content_digest),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        }
        if range_supported:
            headers["Accept-Ranges"] = "bytes"
        if byte_range is not None:
            headers["Content-Range"] = (
                f"bytes {byte_range.start}-{byte_range.end}/{byte_size}"
            )
        return headers


__all__ = ("ArtifactContentPolicy",)
