"""Structural output-shape hashing for the SurfaceSpec cache key (PRD-07, plan D10).

The spec cache key includes an ``output_shape_hash`` so that a change in a tool's
output *structure* — a new field, a type change, a nesting change — misses the
cache and triggers a fresh generation, while two records of the *same* shape (two
different Linear issues, say) share one spec.

Two invariants make this safe:

* **Values are never hashed.** Only the KEY SKELETON is walked: mapping keys
  (sorted, so ordering is irrelevant), the *type* of each value, and — for
  arrays — the shape of the first element (arrays are homogeneous in practice).
  Hashing values would leak payload contents into a cache key and would make the
  key unstable across records, defeating the cache. This is the privacy +
  stability guarantee.
* **Depth is capped.** Deeply nested or self-referential payloads collapse to a
  sentinel past a fixed depth, so a hostile or pathological output cannot drive
  unbounded recursion.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

# How deep the skeleton walk descends before collapsing to a sentinel. Real
# connector payloads are shallow; this only guards pathological / hostile input.
_MAX_DEPTH = 8

# Length of the returned hex digest slice. 16 hex chars = 64 bits of structural
# discrimination — ample for the per-(server, tool) shape space, and short
# enough to read in a cache key / filename.
_DIGEST_LEN = 16


class OutputShapeHasher:
    """Derive a stable, value-free structural hash of a tool output mapping."""

    # Single-char skeleton tokens for scalar types. ``bool`` is checked before
    # ``int`` because ``bool`` is a subclass of ``int`` in Python.
    _SCALAR_STR = "s"
    _SCALAR_BOOL = "b"
    _SCALAR_INT = "i"
    _SCALAR_FLOAT = "f"
    _SCALAR_NULL = "n"
    _SCALAR_OTHER = "o"
    _EMPTY_ARRAY = "[]"
    _DEPTH_CAPPED = "*"

    @classmethod
    def hash(cls, output: object) -> str:
        """Return the truncated hex digest of ``output``'s key skeleton.

        Accepts any object; a non-mapping top-level value still produces a
        stable token-based hash so callers never special-case scalars.
        """

        skeleton = cls._skeleton(output, depth=0)
        digest = hashlib.sha256(skeleton.encode("utf-8")).hexdigest()
        return digest[:_DIGEST_LEN]

    @classmethod
    def _skeleton(cls, value: object, *, depth: int) -> str:
        if depth >= _MAX_DEPTH:
            return cls._DEPTH_CAPPED
        if isinstance(value, Mapping):
            return cls._mapping_skeleton(value, depth=depth)
        # ``str``/``bytes`` are Sequences but must be treated as scalars, never
        # walked element-by-element.
        if isinstance(value, (str, bytes)):
            return cls._SCALAR_STR
        if isinstance(value, Sequence):
            return cls._sequence_skeleton(value, depth=depth)
        return cls._scalar_token(value)

    @classmethod
    def _mapping_skeleton(cls, value: Mapping[object, object], *, depth: int) -> str:
        parts = [
            f"{key}:{cls._skeleton(value[key], depth=depth + 1)}"
            for key in sorted(value, key=str)
        ]
        return "{" + ",".join(parts) + "}"

    @classmethod
    def _sequence_skeleton(cls, value: Sequence[object], *, depth: int) -> str:
        if not value:
            return cls._EMPTY_ARRAY
        # Array-first-element recursion: connector arrays are homogeneous, so the
        # first element's shape represents the collection. Hashing every element
        # would leak count/order without adding structural information.
        return "[" + cls._skeleton(value[0], depth=depth + 1) + "]"

    @classmethod
    def _scalar_token(cls, value: object) -> str:
        if value is None:
            return cls._SCALAR_NULL
        if isinstance(value, bool):
            return cls._SCALAR_BOOL
        if isinstance(value, int):
            return cls._SCALAR_INT
        if isinstance(value, float):
            return cls._SCALAR_FLOAT
        return cls._SCALAR_OTHER


def output_shape_hash(output: object) -> str:
    """Return a stable structural hash of ``output`` (keys + types only)."""

    return OutputShapeHasher.hash(output)


__all__ = ["OutputShapeHasher", "output_shape_hash"]
