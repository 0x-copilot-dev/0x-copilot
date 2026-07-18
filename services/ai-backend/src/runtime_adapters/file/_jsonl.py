"""JSONL append / read / atomic-rewrite primitives for the file store.

All writes go through here so the durability policy lives in one place:

* ``append_line`` opens ``a``, writes one ``\\n``-terminated JSON line, flushes,
  and — when ``fsync=True`` — ``os.fsync``s the file descriptor before close so
  an important record survives a crash immediately after the call returns.
* ``rewrite_json`` / ``rewrite_lines`` write to a sibling temp file, fsync it,
  then ``os.replace`` (atomic on POSIX) so a reader never sees a torn file.

Serialization is deterministic ``json.dumps`` with ``sort_keys`` off (Pydantic
already emits a stable field order) and ``ensure_ascii=False`` so the files stay
human-readable / greppable — the whole point of the plaintext-JSONL design.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from pathlib import Path

from runtime_adapters.file._paths import FileStoreLayout


class JsonlCorruptionError(RuntimeError):
    """A JSONL file has an **interior** malformed line — committed data follows it.

    The read path fails closed by raising this instead of silently returning a
    truncated prefix. A malformed line is only tolerated when it is the *torn
    final line* of the file — an incomplete last append interrupted by a crash
    before ``fsync``, which was never durably committed. A malformed line with
    any content line after it means real corruption (bit-rot, a partial disk
    write in the middle, tampering): the records after it are genuine and must
    not be dropped, so callers surface a "needs repair" failure rather than lose
    history.
    """

    def __init__(self, path: Path, line_number: int) -> None:
        self.path = path
        self.line_number = line_number
        super().__init__(
            f"Interior corruption in {path} at line {line_number}: a malformed "
            "line has valid records after it. Refusing to return a truncated "
            "prefix; this file needs repair."
        )


class JsonlIo:
    """Stateless helpers for JSON-lines files. All methods are synchronous.

    Callers serialize concurrent access with their own ``asyncio.Lock`` (the
    store is single-writer, in-process); these helpers only own the bytes.
    """

    @staticmethod
    def dumps(obj: object) -> str:
        """Serialize one record to a compact single-line JSON string."""

        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def append_line(cls, path: Path, obj: object, *, fsync: bool = True) -> None:
        """Append one JSON object as a line, creating parent dirs on demand."""

        FileStoreLayout.ensure_dir(path.parent)
        existed = path.exists()
        line = cls.dumps(obj)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.write("\n")
            handle.flush()
            if fsync:
                os.fsync(handle.fileno())
        if not existed:
            FileStoreLayout.restrict_file(path)

    @classmethod
    def append_lines(
        cls, path: Path, objs: Iterable[object], *, fsync: bool = True
    ) -> None:
        """Append many JSON objects under one open handle + one fsync."""

        objs = list(objs)
        if not objs:
            return
        FileStoreLayout.ensure_dir(path.parent)
        existed = path.exists()
        with open(path, "a", encoding="utf-8") as handle:
            for obj in objs:
                handle.write(cls.dumps(obj))
                handle.write("\n")
            handle.flush()
            if fsync:
                os.fsync(handle.fileno())
        if not existed:
            FileStoreLayout.restrict_file(path)

    @classmethod
    def rewrite_json(cls, path: Path, obj: object, *, fsync: bool = True) -> None:
        """Atomically replace ``path`` with a single pretty JSON object.

        Used for ``conversation.json`` metadata, which is mutated in place
        (lifecycle PATCHes) rather than appended.
        """

        FileStoreLayout.ensure_dir(path.parent)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(obj, handle, ensure_ascii=False, indent=2)
            handle.flush()
            if fsync:
                os.fsync(handle.fileno())
        os.replace(tmp, path)
        FileStoreLayout.restrict_file(path)

    @classmethod
    def rewrite_lines(
        cls, path: Path, objs: Iterable[object], *, fsync: bool = True
    ) -> None:
        """Atomically replace a JSONL file with ``objs`` (used for compaction)."""

        FileStoreLayout.ensure_dir(path.parent)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            for obj in objs:
                handle.write(cls.dumps(obj))
                handle.write("\n")
            handle.flush()
            if fsync:
                os.fsync(handle.fileno())
        os.replace(tmp, path)
        FileStoreLayout.restrict_file(path)

    @staticmethod
    def read_json(path: Path) -> dict | None:
        """Return a single JSON object from ``path``, or ``None`` if absent."""

        if not path.exists():
            return None
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    @staticmethod
    def iter_lines(path: Path) -> Iterator[dict]:
        """Yield parsed objects from a JSONL file, failing closed on corruption.

        Distinguishes the two ways a JSONL line can fail to parse:

        * **Torn final line** — an incomplete last append interrupted by a crash
          before ``fsync``. The record was never durably committed, so the
          partial trailing line (with only blank lines, if any, after it) is
          silently dropped, exactly as before.
        * **Interior corruption** — a malformed line with a committed content
          line after it. Silently stopping there would truncate real history, so
          this raises :class:`JsonlCorruptionError` and the caller fails closed.

        A malformed line is deferred rather than acted on immediately: only once
        a subsequent content line proves data follows it is it interior
        corruption; if EOF (or only blanks) follows, it was a torn tail.
        """

        if not path.exists():
            return
        with open(path, encoding="utf-8") as handle:
            pending_bad_line: int | None = None
            for line_number, raw in enumerate(handle, start=1):
                stripped = raw.strip()
                if not stripped:
                    continue
                if pending_bad_line is not None:
                    # A malformed earlier line has committed content after it:
                    # interior corruption, not a torn tail. Fail closed before
                    # yielding anything past the corruption point.
                    raise JsonlCorruptionError(path, pending_bad_line)
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError:
                    # Might be the torn final line; defer the verdict until we
                    # know whether any content line follows it.
                    pending_bad_line = line_number
                    continue
                yield parsed


__all__ = ("JsonlIo", "JsonlCorruptionError")
