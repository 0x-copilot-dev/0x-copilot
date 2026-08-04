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

The floor PRD §3.4 promotes the same walk to a *lookup* key.
:class:`ShapeSkeleton` exposes the key-path set the hasher already computes, so
a payload can be compared against a curated spec's bound paths and reuse a
template written for a differently-named tool. The hash answers "is this the
same shape"; the skeleton answers "is this shape close enough".
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

# How deep the skeleton walk descends before collapsing to a sentinel. Real
# connector payloads are shallow; this only guards pathological / hostile input.
_MAX_DEPTH = 8

# Length of the returned hex digest slice. 16 hex chars = 64 bits of structural
# discrimination — ample for the per-(server, tool) shape space, and short
# enough to read in a cache key / filename.
_DIGEST_LEN = 16

# Ceiling on the number of distinct key paths collected from one payload. A wide
# machine-generated output (hundreds of sibling keys) must not turn a render into
# a set operation over thousands of strings; the cap only ever *reduces* the
# similarity score, so it can never manufacture a match.
_MAX_PATHS = 512

# --- the shape-match threshold -----------------------------------------------
#
# How much of a template's bound paths a payload must supply before that
# template is reused for it. Biased HIGH on purpose: a false match is strictly
# worse than falling through to rung 0. Rung 0 is correct-if-plain — it derives
# columns from the payload in front of it and cannot bind a path that is not
# there. A wrong template is *confidently* wrong: it renders a curated title
# over the wrong record and silently drops every column whose path missed, and
# the user has no way to tell that from a connector returning empty fields.
#
# 0.8 admits at most one missing path in a five-path template, which is the
# real-world case this exists for (``list_my_issues`` returning the curated
# ``list_issues`` rows without the enclosing ``team`` header). It rejects the
# "half the fields line up" neighbourhood, where the payload is a different
# resource that happens to share a container name.
SHAPE_MATCH_MIN_COVERAGE = 0.8

# A template binding fewer paths than this is not evidence of anything: two
# shared paths out of two is a coverage of 1.0 over a coincidence. Curated specs
# bind five or more, so this only ever excludes a degenerate template.
SHAPE_MATCH_MIN_TEMPLATE_PATHS = 3


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


@dataclass(frozen=True)
class ShapeSkeleton:
    """The value-free set of key PATHS a payload exposes (floor PRD §3.4).

    The same walk :class:`OutputShapeHasher` folds into one digest, kept open
    instead. The digest is an equality key — two payloads either have the same
    shape or they do not — which is why it cannot answer the question the
    naming brittleness actually poses: Linear's real create tool is
    ``save_issue``, not the catalogued ``create_issue``, and its payload is
    *nearly* the curated one. Nearness needs a set, not a digest.

    Paths use the spec's own dot-grammar plus one addition: ``[]`` marks a
    collection, so ``issues[].title`` is "the title of a row in the issues
    list". That is deliberately the notation a :class:`SurfaceSpec` binds
    against — ``items_path`` + a column path — so a template's requirements and
    a payload's contents are comparable without translating between two
    grammars.

    Values never enter a path, and neither do types. Types are right for the
    *hash* (a ``count`` that turned into a string is a different shape and must
    miss the cache) and wrong here: a spec declares no types at all, so scoring
    a spec against a typed skeleton would score every template at zero.
    """

    paths: frozenset[str]

    # Marks a collection in a path (``issues[]``), so the row-relative paths a
    # table spec binds can be expanded into the payload's absolute path space.
    COLLECTION_MARKER = "[]"
    SEPARATOR = "."

    @classmethod
    def of(cls, value: object) -> "ShapeSkeleton":
        """Collect the key-path set of ``value``; total over any input."""

        collected: set[str] = set()
        cls._walk(value, prefix="", depth=0, into=collected)
        return cls(paths=frozenset(collected))

    def coverage_of(self, required: frozenset[str]) -> float:
        """Fraction of ``required`` this payload supplies, in ``[0.0, 1.0]``.

        **Asymmetric containment, not Jaccard** — and the asymmetry is the whole
        point. The two sides are different kinds of object: a curated spec binds
        a deliberate *subset* of a payload's keys (a spec that bound all of
        GitHub's forty issue fields would be a bad spec), so ``|payload \\
        template|`` is large even on a perfect match. Jaccard divides by that
        union and lands a textbook-correct match somewhere near 0.3 — inside the
        same band as a coincidence, leaving nowhere to put a threshold.

        Containment asks the question that actually decides the render: *can
        this template draw this payload* — does every path it binds resolve.
        That is the Szymkiewicz–Simpson overlap coefficient with the template
        fixed as the denominator, and it degrades the way a renderer degrades:
        one missing path is one blank cell, and the score drops by exactly one
        path's worth.

        An empty ``required`` scores 0.0: a template that demands nothing has
        not matched anything, it has merely failed to disagree.
        """

        if not required:
            return 0.0
        return len(required & self.paths) / len(required)

    def has(self, path: str) -> bool:
        """Whether ``path`` is present in this payload's skeleton."""

        return path in self.paths

    @classmethod
    def _walk(cls, value: object, *, prefix: str, depth: int, into: set[str]) -> None:
        if depth >= _MAX_DEPTH or len(into) >= _MAX_PATHS:
            return
        if isinstance(value, Mapping):
            cls._walk_mapping(value, prefix=prefix, depth=depth, into=into)
            return
        # ``str``/``bytes`` are Sequences but carry no structure to walk.
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            return
        cls._walk_sequence(value, prefix=prefix, depth=depth, into=into)

    @classmethod
    def _walk_mapping(
        cls, value: Mapping[object, object], *, prefix: str, depth: int, into: set[str]
    ) -> None:
        for key in sorted(value, key=str):
            if len(into) >= _MAX_PATHS:
                return
            path = f"{prefix}{cls.SEPARATOR}{key}" if prefix else str(key)
            into.add(path)
            cls._walk(value[key], prefix=path, depth=depth + 1, into=into)

    @classmethod
    def _walk_sequence(
        cls, value: Sequence[object], *, prefix: str, depth: int, into: set[str]
    ) -> None:
        # The collection marker is recorded even for an empty list: a spec's
        # ``items_path`` pointing at an empty result set still matched — it just
        # has no rows to draw — and that must not read as a structural miss.
        marker = f"{prefix}{cls.COLLECTION_MARKER}"
        into.add(marker)
        if value:
            # First element only, matching the hasher: connector arrays are
            # homogeneous, and walking all of them would let payload *length*
            # leak into a structural key.
            cls._walk(value[0], prefix=marker, depth=depth + 1, into=into)


def output_shape_hash(output: object) -> str:
    """Return a stable structural hash of ``output`` (keys + types only)."""

    return OutputShapeHasher.hash(output)


__all__ = [
    "SHAPE_MATCH_MIN_COVERAGE",
    "SHAPE_MATCH_MIN_TEMPLATE_PATHS",
    "OutputShapeHasher",
    "ShapeSkeleton",
    "output_shape_hash",
]
