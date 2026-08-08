"""Catalog discovery for the Virtuals compute gateway.

Virtuals fronts ~60 models from ten vendors behind one OpenAI-wire endpoint, and
— unlike models.dev or LiteLLM, which are third-party *descriptions* of other
people's catalogs — it publishes its own live inventory at ``/v1/models`` with
context window and real per-Mtok pricing on every row. That is the whole reason
this source exists rather than a hardcoded table: the list is authoritative,
it changes when Virtuals adds a model, and nothing here needs editing when it
does.

Posture is identical to :class:`ModelsDevModelSource` and enforced by the shared
:class:`JsonSnapshotCache`: ``records()`` is synchronous, reads only a local
snapshot, and never touches the network. A stale or absent snapshot schedules a
background refresh and returns whatever is already on disk, so a dead network
degrades the Virtuals rows to "last known" rather than emptying the picker.

Two shapes here are Virtuals-specific and deliberate:

* **Ids are dash-joined** (``anthropic-claude-opus-5``), not ``vendor/model``.
  :class:`VirtualsCatalogPolicy` strips the vendor to recover the product family,
  which is what feeds the size ladder in :class:`ModelSizeTierResolver`.
* **``/v1/models`` is PUBLIC** — it answers 200 with no credential. This source
  therefore proves nothing about a user's key; the credential gate is the
  ``configured`` flag the catalog stamps from BYOK state, and the key itself is
  checked by the backend's live validator against ``/chat/completions``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

from agent_runtime.api.json_snapshot_cache import JsonSnapshotCache
from agent_runtime.api.litellm_model_source import CatalogModelRecord
from agent_runtime.api.models_dev_source import ModelsDevCatalogPolicy
from agent_runtime.execution.models import ModelConfigResolver

_LOGGER = logging.getLogger(__name__)


class VirtualsCatalogCache(JsonSnapshotCache):
    """On-disk snapshot of the Virtuals model inventory."""

    URL: Final[str] = "https://compute.virtuals.io/v1/models"
    LABEL: Final[str] = "virtuals"
    PATH_ENV: Final[str] = "RUNTIME_VIRTUALS_CATALOG_CACHE"
    FILENAME: Final[str] = "virtuals-catalog.json"


class VirtualsCatalogPolicy:
    """What counts as a selectable model, and how ids map to product families."""

    PROVIDER: Final[str] = "virtuals"

    class Fields:
        """Stable ``/v1/models`` field names — pinned so a rename fails here."""

        DATA = "data"
        ID = "id"
        NAME = "name"
        DESCRIPTION = "description"
        CONTEXT_LENGTH = "contextLength"
        PRICING = "pricing"
        INPUT = "input"
        OUTPUT = "output"

    #: Vendor prefixes Virtuals dash-joins onto a model id. Ordered longest-first
    #: at match time so ``x-ai-`` cannot be shadowed by a shorter sibling.
    VENDOR_PREFIXES: Final[tuple[str, ...]] = (
        "anthropic",
        "deepseek",
        "e2ee",
        "google",
        "kimi",
        "minimax",
        "moonshotai",
        "openai",
        "x-ai",
        "z-ai",
    )

    #: Tokens that qualify a release rather than name the product line. Dropped
    #: when deriving the family so ``gemini-3-1-pro-preview`` and
    #: ``claude-opus-4-6-fast`` land on ``gemini-pro`` / ``claude-opus`` — the
    #: families :class:`ModelSizeTierResolver` already knows.
    QUALIFIERS: Final[frozenset[str]] = frozenset(
        {"preview", "fast", "api", "latest", "exp"}
    )

    #: A token carrying any digit is a version marker (``5``, ``56``, ``k3``,
    #: ``v4``, ``m3``), never part of the product line.
    VERSIONED: Final[re.Pattern[str]] = re.compile(r"\d")

    #: Vendor label Virtuals prefixes onto the display name ("Anthropic: …").
    #: Stripped because the picker already groups these rows under Virtuals.
    DISPLAY_VENDOR: Final[re.Pattern[str]] = re.compile(r"^[^:]{1,24}:\s*")

    @classmethod
    def eligible(cls, model_id: str) -> bool:
        """Whether this row is a general chat model the run path can use.

        Reuses :attr:`ModelsDevCatalogPolicy.NICHE` rather than restating it, so
        the codex / realtime / embedding exclusions stay defined once.
        """

        return not ModelsDevCatalogPolicy.NICHE.search(model_id)

    @classmethod
    def strip_vendor(cls, model_id: str) -> str:
        """Drop the dash-joined vendor prefix; unchanged when none matches."""

        normalized = model_id.strip().lower()
        for prefix in sorted(cls.VENDOR_PREFIXES, key=len, reverse=True):
            head = f"{prefix}-"
            if normalized.startswith(head) and len(normalized) > len(head):
                return normalized[len(head) :]
        return normalized

    @classmethod
    def family(cls, model_id: str) -> str | None:
        """The vendor product line, e.g. ``claude-opus`` / ``gpt-terra``.

        This is the size axis :class:`ModelSizeTierResolver` ranks on, and the
        names must match its ``DEFAULT_MAIN_LINE`` families for a Virtuals-hosted
        frontier model to land on the ladder at all. Version and qualifier tokens
        are dropped; what remains is the line.
        """

        bare = cls.strip_vendor(model_id)
        tokens = [
            token
            for token in bare.split("-")
            if token and not cls.VERSIONED.search(token) and token not in cls.QUALIFIERS
        ]
        return "-".join(tokens) or None

    @classmethod
    def display_name(cls, model_id: str, raw_name: Any) -> str:
        """Row label: the published name minus its vendor prefix, else the id."""

        if isinstance(raw_name, str) and raw_name.strip():
            return cls.DISPLAY_VENDOR.sub("", raw_name.strip()) or raw_name.strip()
        return model_id

    @classmethod
    def cost(cls, pricing: Any, key: str) -> float | None:
        """Per-Mtok dollar cost from the ``pricing`` object, or ``None``.

        Virtuals quotes dollars per million tokens already, matching
        :class:`CatalogModelRecord`'s unit — so this validates rather than
        converts. A negative or non-numeric value is discarded instead of
        trusted, because cost is a ranking input for the default model.
        """

        if not isinstance(pricing, Mapping):
            return None
        value = pricing.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value) if value >= 0 else None

    @classmethod
    def context_window(cls, value: Any) -> int | None:
        """Positive context length, or ``None`` when absent/implausible."""

        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value if value > 0 else None

    @classmethod
    def version(cls, model_id: str) -> tuple[int, ...]:
        """Version tuple parsed from the id: ``claude-opus-4-8`` -> ``(4, 8)``.

        This exists because Virtuals publishes NO release date, and
        :class:`ModelSizeTierResolver` picks each family's ladder rung by
        recency. With every date ``None`` its comparison cannot discriminate and
        it keeps whichever record it saw first — which made the Virtuals opus
        rung ``claude-opus-4-5`` while ``claude-opus-5`` sat in the same family,
        and the ``gpt`` rung ``gpt-52`` rather than ``gpt-55``.

        The id is the only recency signal on offer, and for these vendors it is
        a reliable one: the product line is fixed and the digits are the
        version. Sorting on it (see :meth:`VirtualsCatalogParser.sort_key`) makes
        the resolver's first-wins land on the newest rather than the arbitrary.
        """

        return tuple(
            int(token)
            for token in cls.strip_vendor(model_id).split("-")
            if token.isdigit()
        )


class VirtualsCatalogParser:
    """Turn one ``/v1/models`` payload into trusted catalog records."""

    @classmethod
    def parse(cls, payload: Mapping[str, Any] | None) -> tuple[CatalogModelRecord, ...]:
        """Normalize the payload. Malformed rows are skipped, never raised on.

        The payload is an UNTRUSTED external document: every field is checked
        before use, and a row that fails any check is dropped rather than
        surfaced with invented values.
        """

        if payload is None:
            return ()
        entries = payload.get(VirtualsCatalogPolicy.Fields.DATA)
        if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
            return ()
        records: list[CatalogModelRecord] = []
        for entry in entries:
            record = cls._record(entry)
            if record is not None:
                records.append(record)
        # Newest-version-first WITHIN each family, so the tier resolver's
        # first-wins fallback selects the current release. See
        # :meth:`VirtualsCatalogPolicy.version` for why the id is the signal.
        return tuple(sorted(records, key=cls.sort_key))

    @classmethod
    def sort_key(cls, record: CatalogModelRecord) -> tuple[str, tuple[int, ...], float]:
        """Order: family, then newest version, then cheapest.

        The cost tiebreak decides between same-version variants —
        ``claude-opus-5`` and ``claude-opus-5-fast`` carry the same version, and
        the plain one is half the output price. A ladder rung is a DEFAULT, so
        the cheaper of two equally-current variants is the right representative.
        """

        return (
            record.family or record.model_id,
            cls._newest_first(VirtualsCatalogPolicy.version(record.model_id)),
            record.output_cost_per_mtok
            if record.output_cost_per_mtok is not None
            else float("inf"),
        )

    #: Version components compared when ordering a family. Padding to a fixed
    #: width is what makes the descending sort correct: negating a RAGGED tuple
    #: puts the shorter one first, so ``glm-5`` (5,) would outrank ``glm-5-2``
    #: (5, 2) — the newest release losing to its own base version.
    _VERSION_WIDTH: Final[int] = 4

    @classmethod
    def _newest_first(cls, version: tuple[int, ...]) -> tuple[int, ...]:
        """Negate a zero-padded version so ascending sort yields newest-first."""

        padded = (version + (0,) * cls._VERSION_WIDTH)[: cls._VERSION_WIDTH]
        return tuple(-part for part in padded)

    @classmethod
    def _record(cls, entry: Any) -> CatalogModelRecord | None:
        fields = VirtualsCatalogPolicy.Fields
        if not isinstance(entry, Mapping):
            return None
        model_id = entry.get(fields.ID)
        if not isinstance(model_id, str) or not model_id.strip():
            return None
        model_id = model_id.strip()
        if not VirtualsCatalogPolicy.eligible(model_id):
            return None
        pricing = entry.get(fields.PRICING)
        return CatalogModelRecord(
            provider=VirtualsCatalogPolicy.PROVIDER,
            model_id=model_id,
            display_name=VirtualsCatalogPolicy.display_name(
                model_id, entry.get(fields.NAME)
            ),
            context_window=VirtualsCatalogPolicy.context_window(
                entry.get(fields.CONTEXT_LENGTH)
            ),
            input_cost_per_mtok=VirtualsCatalogPolicy.cost(pricing, fields.INPUT),
            output_cost_per_mtok=VirtualsCatalogPolicy.cost(pricing, fields.OUTPUT),
            # The run path's own predicate, so the catalog can never advertise
            # reasoning the builder would decline to request (or vice versa).
            supports_reasoning=ModelConfigResolver.model_supports_reasoning(
                VirtualsCatalogPolicy.PROVIDER, model_id
            ),
            # Virtuals is an agent-compute gateway: every listed model is
            # reachable over chat-completions with tools. It publishes no
            # per-model tool flag to narrow this further.
            supports_tools=True,
            supports_attachments=False,
            # No release date is published; consumers must read a missing date
            # as "unknown", never "old" (see CatalogModelRecord).
            release_date=None,
            family=VirtualsCatalogPolicy.family(model_id),
        )


class VirtualsModelSource:
    """:class:`CatalogModelSource` over the Virtuals gateway inventory.

    Emits nothing when no snapshot exists yet — the first boot shows the other
    providers and picks Virtuals up on the next refresh. There is deliberately
    no bundled fallback list: a stale hardcoded copy of someone else's catalog
    is exactly what this source exists to avoid.
    """

    def __init__(self, *, cache: VirtualsCatalogCache | None = None) -> None:
        self._cache = cache if cache is not None else VirtualsCatalogCache()

    def records(self) -> tuple[CatalogModelRecord, ...]:
        """Trusted records from the cached snapshot. Never performs network I/O."""

        return VirtualsCatalogParser.parse(self._cache.payload())


__all__ = [
    "VirtualsCatalogCache",
    "VirtualsCatalogParser",
    "VirtualsCatalogPolicy",
    "VirtualsModelSource",
]
