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
        """Yield parsed objects from a JSONL file, skipping blank/torn tails.

        A trailing partial line (crash mid-append without fsync) is silently
        skipped — the canonical record simply was not durably committed.
        """

        if not path.exists():
            return
        with open(path, encoding="utf-8") as handle:
            for raw in handle:
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    yield json.loads(stripped)
                except json.JSONDecodeError:
                    # Torn final line from an interrupted append — stop here.
                    break


__all__ = ("JsonlIo",)
