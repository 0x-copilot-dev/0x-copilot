"""Catalog discovery backed by models.dev, falling back to LiteLLM.

Why a second source at all: LiteLLM's bundled ``model_cost`` table carries no
release date (only a sparse ``deprecation_date``), no product-line grouping, and
no display names — so a picker built on it cannot order by recency, cannot
derive a size ladder, and must synthesise labels from the slug (``claude-haiku-
4-5-20251001`` -> "Claude Haiku 4.5.20251001"). models.dev carries all three
(``release_date``, ``family``, ``name``) plus explicit ``modalities``, which is
the only reliable way to drop audio models: LiteLLM labels ``gpt-audio`` as
``mode: "chat"`` and — in the *bundled offline* table this service pins — even
labels ``gpt-realtime`` that way.

Posture: models.dev is primary, LiteLLM is the fallback, and the network is
never on the request path.

* ``records()`` is synchronous (the :class:`CatalogModelSource` contract) and
  only ever reads a local cache file. It never performs I/O against the network.
* A stale or missing cache schedules a **background daemon-thread refresh**,
  rate-limited by :attr:`ModelsDevCatalogCache.RETRY_INTERVAL_SECONDS`, and the
  call returns immediately from whatever is already available.
* With no usable cache — first boot, or permanently offline — every call falls
  through to :class:`LitellmModelSource`, so the picker degrades to today's
  behaviour instead of emptying out.

That ordering means a fresh install shows the LiteLLM catalog for the first few
seconds and the models.dev catalog from the next fetch onward, and an offline
desktop keeps working forever on the last good snapshot.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

from agent_runtime.api.litellm_model_source import (
    CatalogModelRecord,
    CatalogModelSource,
    LitellmModelSource,
)
from agent_runtime.api.json_snapshot_cache import (
    JsonSnapshotCache,
    JsonSnapshotFetcher,
)
from agent_runtime.execution.contracts import ModelReasoningEffort

_LOGGER = logging.getLogger(__name__)


class ModelsDevCatalogCache(JsonSnapshotCache):
    """On-disk snapshot of ``models.dev/api.json`` with a background refresh.

    All behaviour lives in :class:`JsonSnapshotCache`; this subclass only names
    the upstream. The cache file is the only thing
    :meth:`ModelsDevModelSource.records` reads, so a slow or dead network can
    never delay a catalog request.
    """

    URL: Final[str] = "https://models.dev/api.json"
    LABEL: Final[str] = "models.dev"
    #: Env override for the snapshot location; otherwise derived.
    PATH_ENV: Final[str] = "RUNTIME_MODEL_CATALOG_CACHE"
    FILENAME: Final[str] = "models-dev-catalog.json"


#: Back-compat alias — the fetcher was models.dev-specific before Virtuals
#: needed the same machinery. Kept so existing construction sites read the same.
ModelsDevFetcher = JsonSnapshotFetcher


class ModelsDevCatalogPolicy:
    """What counts as a selectable chat model, and how ids are normalized."""

    #: models.dev provider key -> this product's provider slug. Mirrors
    #: ``ModelConfigResolver``'s allowlist (``google`` normalizes to ``gemini``).
    PROVIDERS: Final[Mapping[str, str]] = {
        "anthropic": "anthropic",
        "openai": "openai",
        "google": "gemini",
        "openrouter": "openrouter",
    }
    DEAD_STATUSES: Final[frozenset[str]] = frozenset({"deprecated", "retired"})
    #: Trailing dated-snapshot suffix: ``-20251001`` or ``-2025-10-01``.
    DATED_SUFFIX: Final[re.Pattern[str]] = re.compile(r"-(\d{8}|\d{4}-\d{2}-\d{2})$")
    #: Purpose-specific lines that are not general chat models. Matched against
    #: the id AND the family so a renamed variant can't slip through one of them.
    #: These are *product lines*, not versions, so the list stays stable as new
    #: releases ship — a new ``gpt-5.7-codex`` is excluded without a code change.
    NICHE: Final[re.Pattern[str]] = re.compile(
        r"(codex|realtime|live|image|video|tts|audio|speech|search|embed|rerank"
        r"|guard|moderat|robotics|computer-use|translate|deep-research"
        r"|:free|:exacto)",
        re.IGNORECASE,
    )

    @classmethod
    def eligible(cls, model: Mapping[str, Any]) -> bool:
        """Whether a models.dev row is a general chat model the run path can use."""

        modalities = model.get("modalities")
        modalities = modalities if isinstance(modalities, Mapping) else {}
        outputs = cls._string_list(modalities.get("output"))
        inputs = cls._string_list(modalities.get("input"))
        # Text-ONLY output is the decisive audio/image filter. `mode`-style flags
        # cannot do this job: LiteLLM calls `gpt-audio` a chat model, and it is —
        # it just also speaks, which the composer has no way to render.
        if outputs != ("text",) or "text" not in inputs:
            return False
        if not model.get("tool_call"):
            return False
        # NB: `experimental` is an OPTIONS OBJECT on some rows (Claude Opus 5
        # carries its "fast" mode there), so only an explicit True means the
        # model itself is experimental. Truthiness would drop the flagship.
        if model.get("experimental") is True:
            return False
        status = model.get("status")
        if isinstance(status, str) and status.strip().lower() in cls.DEAD_STATUSES:
            return False
        if not cls.release_date(model):
            return False
        identifier = model.get("id")
        family = model.get("family")
        return not (
            (isinstance(identifier, str) and cls.NICHE.search(identifier))
            or (isinstance(family, str) and cls.NICHE.search(family))
        )

    @classmethod
    def release_date(cls, model: Mapping[str, Any]) -> str | None:
        value = model.get("release_date")
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value or None

    @classmethod
    def prefer_stable_over_snapshot(
        cls, models: Mapping[str, Mapping[str, Any]]
    ) -> tuple[Mapping[str, Any], ...]:
        """Drop ``claude-haiku-4-5-20251001`` when ``claude-haiku-4-5`` exists.

        Both describe one product; the stable alias is what a user should pin.
        A dated id with no stable sibling is kept — some models ship dated-only.
        """

        kept: list[Mapping[str, Any]] = []
        for identifier, model in models.items():
            base = cls.DATED_SUFFIX.sub("", identifier)
            if base != identifier and base in models:
                continue
            kept.append(model)
        return tuple(kept)

    @staticmethod
    def _string_list(value: object) -> tuple[str, ...]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return ()
        return tuple(item for item in value if isinstance(item, str))

    @classmethod
    def reasoning_efforts(
        cls, model: Mapping[str, Any]
    ) -> tuple[ModelReasoningEffort, ...]:
        """Return the effort rungs this model accepts, in source order.

        models.dev carries the ladder in a key that is a *sibling* of
        ``reasoning``, not nested inside it::

            "reasoning": true,
            "reasoning_options": [
              {"type": "effort", "values": ["none","low","medium","high","xhigh","max"]}
            ]

        ``reasoning`` really is a boolean, which is why reading it alone looks
        correct and still loses the ladder.

        ``reasoning_options`` is a *typed option list* — entries also appear with
        ``budget_tokens`` and ``toggle`` types. Only ``effort`` is mapped here;
        the others earn their own typed fields once their wire shape is verified,
        rather than a ``dict`` bag that would outlive the verification.

        Unrecognised rungs are dropped rather than fatal. models.dev adds rungs on
        the vendor's schedule (``xhigh`` and ``max`` both arrived after this
        parser's vocabulary was written), and an unknown rung must cost that rung,
        never the whole model.
        """

        options = model.get(cls._Reasoning.OPTIONS)
        if not isinstance(options, Sequence) or isinstance(options, (str, bytes)):
            return ()
        efforts: list[ModelReasoningEffort] = []
        for option in options:
            if not isinstance(option, Mapping):
                continue
            if option.get(cls._Reasoning.TYPE) != cls._Reasoning.TYPE_EFFORT:
                continue
            for value in cls._string_list(option.get(cls._Reasoning.VALUES)):
                try:
                    effort = ModelReasoningEffort(value)
                except ValueError:
                    continue
                if effort not in efforts:
                    efforts.append(effort)
        return tuple(efforts)

    class _Reasoning:
        """models.dev key names for the reasoning option list."""

        OPTIONS: Final[str] = "reasoning_options"
        TYPE: Final[str] = "type"
        TYPE_EFFORT: Final[str] = "effort"
        VALUES: Final[str] = "values"


class ModelsDevCatalogParser:
    """Map the models.dev payload onto :class:`CatalogModelRecord` values."""

    @classmethod
    def parse(cls, payload: Mapping[str, Any]) -> tuple[CatalogModelRecord, ...]:
        """Return every eligible record across the providers we can execute."""

        records: list[CatalogModelRecord] = []
        for source_key, provider in ModelsDevCatalogPolicy.PROVIDERS.items():
            entry = payload.get(source_key)
            if not isinstance(entry, Mapping):
                continue
            models = entry.get("models")
            if not isinstance(models, Mapping):
                continue
            eligible = {
                identifier: model
                for identifier, model in models.items()
                if isinstance(identifier, str)
                and isinstance(model, Mapping)
                and ModelsDevCatalogPolicy.eligible(model)
            }
            for model in ModelsDevCatalogPolicy.prefer_stable_over_snapshot(eligible):
                record = cls._record(provider=provider, model=model)
                if record is not None:
                    records.append(record)
        # Deterministic order (provider asc, id asc), matching LitellmModelSource
        # so a source swap never reshuffles the picker on its own.
        return tuple(sorted(records, key=lambda r: (r.provider, r.model_id)))

    @classmethod
    def _record(
        cls, *, provider: str, model: Mapping[str, Any]
    ) -> CatalogModelRecord | None:
        identifier = model.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            return None
        name = model.get("name")
        limit = model.get("limit")
        limit = limit if isinstance(limit, Mapping) else {}
        cost = model.get("cost")
        cost = cost if isinstance(cost, Mapping) else {}
        family = model.get("family")
        return CatalogModelRecord(
            provider=provider,
            model_id=identifier.strip(),
            # models.dev ships curated labels ("Claude Opus 5", "GPT-5.4 nano"),
            # which is the whole reason the picker stops showing slug-derived
            # strings like "Claude Haiku 4.5.20251001".
            display_name=name.strip()
            if isinstance(name, str) and name.strip()
            else identifier.strip(),
            context_window=cls._int(limit.get("context")),
            max_output_tokens=cls._int(limit.get("output")),
            input_cost_per_mtok=cls._float(cost.get("input")),
            output_cost_per_mtok=cls._float(cost.get("output")),
            supports_reasoning=bool(model.get("reasoning")),
            reasoning_efforts=ModelsDevCatalogPolicy.reasoning_efforts(model),
            supports_tools=bool(model.get("tool_call")),
            supports_attachments=cls._has_attachment_input(model),
            release_date=ModelsDevCatalogPolicy.release_date(model),
            family=family.strip()
            if isinstance(family, str) and family.strip()
            else None,
        )

    @staticmethod
    def _has_attachment_input(model: Mapping[str, Any]) -> bool:
        if model.get("attachment"):
            return True
        modalities = model.get("modalities")
        modalities = modalities if isinstance(modalities, Mapping) else {}
        inputs = ModelsDevCatalogPolicy._string_list(modalities.get("input"))
        return any(kind in inputs for kind in ("image", "pdf"))

    @staticmethod
    def _int(value: object) -> int | None:
        return int(value) if isinstance(value, (int, float)) and value > 0 else None

    @staticmethod
    def _float(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)


class ModelsDevModelSource:
    """models.dev-backed :class:`CatalogModelSource` with a LiteLLM fallback."""

    def __init__(
        self,
        *,
        cache: ModelsDevCatalogCache | None = None,
        fallback: CatalogModelSource | None = None,
    ) -> None:
        self._cache = cache if cache is not None else ModelsDevCatalogCache()
        self._fallback = fallback if fallback is not None else LitellmModelSource()

    def records(self) -> tuple[CatalogModelRecord, ...]:
        """Eligible records from the cached snapshot, else the LiteLLM fallback.

        An empty parse counts as a failure: a payload whose shape we no longer
        understand must degrade to LiteLLM, not to an empty picker.
        """

        payload = self._cache.payload()
        if payload is not None:
            records = ModelsDevCatalogParser.parse(payload)
            if records:
                return records
        return self._fallback.records()


__all__ = [
    "ModelsDevCatalogCache",
    "ModelsDevCatalogParser",
    "ModelsDevCatalogPolicy",
    "ModelsDevFetcher",
    "ModelsDevModelSource",
]
