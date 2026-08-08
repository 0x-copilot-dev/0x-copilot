"""Size tiers and the default short list the composer's model pill shows.

The picker must open on a handful of good models, not the whole catalog. This
module derives that short list instead of hardcoding model ids, so a new release
appears the day models.dev carries it — with no code change.

Two derivations, in order:

**The size ladder.** ``family`` (``claude-opus``, ``gpt-nano``, ``gemini-flash``)
is the vendor's own product line and therefore the size axis. Take the newest
release per family, rank those representatives by output cost, and the ladder
falls out: haiku < sonnet < opus, nano < mini < flagship.

**The tiers.** ``small`` is the cheapest rung, ``big`` the flagship, ``medium``
the middle. ``big`` deliberately means *flagship general model*, NOT "most
expensive" — the dearest OpenAI row is ``gpt-5.5-pro`` at $180/Mtok, a
max-reasoning SKU nobody wants as a default, and the dearest Anthropic row is
the creative-writing ``claude-fable`` line rather than ``claude-opus``.

That last distinction has no vendor-agnostic signal in models.dev: OpenAI's
"pro" is a reasoning tier while Google's "pro" is its flagship size, and both
sort above their general line on price. So each known provider carries an
explicit :attr:`ModelSizeTierResolver.MAIN_LINE` tuple — *product lines*, not
versions, which is why it stays correct across releases. Providers without a
hint (OpenRouter, anything new) fall back to pure cost ranking.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable, Mapping, Sequence
from typing import Final, Literal

from agent_runtime.api.litellm_model_source import CatalogModelRecord

_LOGGER = logging.getLogger(__name__)

ModelTier = Literal["small", "medium", "big"]


class ModelTierLines:
    """Resolve the per-provider tier lines, allowing an out-of-band override.

    The built-in map ships with the app, so a brand-new product line would
    normally wait for an app release before it could be featured in the default
    set. This indirection removes that: the same value can be supplied as JSON
    via :attr:`ENV`, letting a new line be rolled out as configuration (or a
    pushed settings value) rather than a binary.

    Shape: ``{"anthropic": [["claude-haiku"], ["claude-sonnet"], ["claude-opus"]]}``
    — provider -> tiers smallest-first, each tier a list of acceptable families.
    A malformed override is ignored wholesale (logged, never partially applied),
    because a half-read tier map would silently distort everyone's defaults.
    """

    ENV: Final[str] = "RUNTIME_MODEL_TIER_LINES"

    @classmethod
    def resolve(cls) -> Mapping[str, tuple[tuple[str, ...], ...]]:
        raw = (os.environ.get(cls.ENV) or "").strip()
        if not raw:
            return ModelSizeTierResolver.DEFAULT_MAIN_LINE
        try:
            parsed = json.loads(raw)
            return cls._coerce(parsed)
        except (ValueError, TypeError) as exc:
            _LOGGER.warning(
                "Ignoring malformed %s; using built-in tier lines: %s", cls.ENV, exc
            )
            return ModelSizeTierResolver.DEFAULT_MAIN_LINE

    @staticmethod
    def _coerce(parsed: object) -> Mapping[str, tuple[tuple[str, ...], ...]]:
        if not isinstance(parsed, Mapping):
            raise TypeError("tier lines must be an object keyed by provider")
        lines: dict[str, tuple[tuple[str, ...], ...]] = {}
        for provider, tiers in parsed.items():
            if not isinstance(provider, str) or not isinstance(tiers, Sequence):
                raise TypeError(f"bad tier entry for {provider!r}")
            lines[provider] = tuple(
                tuple(str(family) for family in tier)
                for tier in tiers
                if isinstance(tier, Sequence) and not isinstance(tier, (str, bytes))
            )
        return lines


class ModelSizeTierResolver:
    """Derive each provider's small/medium/big ladder from family + cost."""

    #: Provider -> the general-purpose product lines per tier, smallest first.
    #: Each tier lists the families acceptable for that rung; the newest release
    #: ACROSS them wins, which is what absorbs vendor reshuffles — models.dev
    #: files "GPT-5.6" under ``gpt-sol`` while "GPT-5.5" sits in ``gpt``, and
    #: both are the same flagship rung.
    #:
    #: Restricting to these lines is also what keeps max-reasoning SKUs
    #: (``gpt-pro`` at $180/Mtok, ``o-pro``) and specialty lines
    #: (``claude-fable``, for creative writing) out of the default set — the
    #: distinction they need is not expressible from cost, which sorts all of
    #: them above the general line.
    #: Overridable at runtime — see :class:`ModelTierLines`. Neither models.dev
    #: nor LiteLLM publishes a size/tier field (verified against the full field
    #: set of both), so this is the one thing that cannot be derived. Keeping it
    #: as *families* rather than model ids is what bounds the maintenance: a new
    #: VERSION of an existing line (Opus 6, GPT-5.7) is picked up automatically
    #: on the next catalog refresh, and only a brand-new product LINE needs a
    #: change here.
    #: OpenAI's 5.6 line renamed every rung — ``gpt-luna`` / ``gpt-terra`` /
    #: ``gpt-sol`` supersede ``gpt-nano`` / ``gpt-mini`` / ``gpt``. Both spellings
    #: are listed per rung rather than swapped, which is the case this structure
    #: exists for: the newest release across a rung's families wins, so the ladder
    #: follows the rename automatically and still resolves for a provider whose
    #: catalog only carries the older line.
    DEFAULT_MAIN_LINE: Final[Mapping[str, tuple[tuple[str, ...], ...]]] = {
        "anthropic": (("claude-haiku",), ("claude-sonnet",), ("claude-opus",)),
        "openai": (
            ("gpt-nano", "gpt-luna"),
            ("gpt-mini", "gpt-terra"),
            ("gpt", "gpt-sol"),
        ),
        "gemini": (("gemini-flash-lite",), ("gemini-flash",), ("gemini-pro",)),
    }
    #: Gateways resell other vendors' lines rather than shipping their own, so
    #: their ladder is the union of every known main line, ranked by cost.
    GATEWAY_PROVIDERS: Final[frozenset[str]] = frozenset({"openrouter", "virtuals"})
    TIER_ORDER: Final[tuple[ModelTier, ...]] = ("small", "medium", "big")

    @classmethod
    def ladder(
        cls, records: Iterable[CatalogModelRecord], *, provider: str
    ) -> tuple[CatalogModelRecord, ...]:
        """The provider's rungs, smallest to largest.

        For a provider with a declared main line, one rung per tier — the newest
        release across that tier's families. Otherwise every family's newest,
        cost-ranked.
        """

        candidates = [r for r in records if r.provider == provider]
        all_lines = ModelTierLines.resolve()
        main_line = all_lines.get(provider)
        if main_line is not None:
            rungs = [cls._newest_in(candidates, families) for families in main_line]
            resolved = tuple(rung for rung in rungs if rung is not None)
            # A declared main line matches nothing when the records carry no
            # family at all — which is exactly what the LiteLLM fallback serves.
            # Falling through to cost ranking keeps the short list populated
            # offline instead of collapsing the picker to the default model.
            if resolved:
                return resolved
        if provider in cls.GATEWAY_PROVIDERS:
            known = {f for line in all_lines.values() for fam in line for f in fam}
            candidates = [r for r in candidates if (r.family or "") in known]
        newest: dict[str, CatalogModelRecord] = {}
        for record in candidates:
            family = record.family or record.model_id
            current = newest.get(family)
            if current is None or cls._newer(record, current):
                newest[family] = record
        return tuple(sorted(newest.values(), key=cls._cost))

    @classmethod
    def _newest_in(
        cls, records: Iterable[CatalogModelRecord], families: tuple[str, ...]
    ) -> CatalogModelRecord | None:
        best: CatalogModelRecord | None = None
        for record in records:
            if (record.family or "") not in families:
                continue
            if best is None or cls._newer(record, best):
                best = record
        return best

    @classmethod
    def tiers(
        cls, records: Iterable[CatalogModelRecord], *, provider: str, count: int
    ) -> tuple[tuple[ModelTier, CatalogModelRecord], ...]:
        """Pick ``count`` models spanning the provider's ladder.

        ``count`` 1 -> the flagship; 2 -> smallest + flagship; 3+ -> smallest,
        middle, flagship. Never returns more than the ladder holds.
        """

        rungs = cls.ladder(records, provider=provider)
        if not rungs or count <= 0:
            return ()
        if count == 1:
            return (("big", rungs[-1]),)
        if count == 2 or len(rungs) == 2:
            return (("small", rungs[0]), ("big", rungs[-1]))
        return (
            ("small", rungs[0]),
            ("medium", rungs[len(rungs) // 2]),
            ("big", rungs[-1]),
        )

    @classmethod
    def tier_of(
        cls, record: CatalogModelRecord, *, ladder: Sequence[CatalogModelRecord]
    ) -> ModelTier | None:
        """Label a record against an already-computed ladder (None = off-ladder)."""

        for index, rung in enumerate(ladder):
            if rung.model_id != record.model_id:
                continue
            if index == 0:
                return "small"
            return "big" if index == len(ladder) - 1 else "medium"
        return None

    @staticmethod
    def _cost(record: CatalogModelRecord) -> float:
        return record.output_cost_per_mtok or 0.0

    @staticmethod
    def _newer(candidate: CatalogModelRecord, current: CatalogModelRecord) -> bool:
        """Compare by release date; a dateless record never displaces a dated one."""

        if candidate.release_date is None:
            return False
        if current.release_date is None:
            return True
        return candidate.release_date > current.release_date


class DefaultModelSelectionPolicy:
    """The 6-9 models the pill opens on before the user curates in Settings.

    Shape (product decision, not a derivation):

    * a provider the caller holds a key for contributes **3** — small, medium,
      big — because that's the provider they will actually run;
    * up to :attr:`GUEST_PROVIDERS` other providers contribute **1** each (the
      flagship), so switching providers is one click and one key away;
    * with **no key anywhere**, every provider contributes **2** (small + big),
      which keeps the first-run picker an honest menu of what a key would buy.
    """

    KEYED_PER_PROVIDER: Final[int] = 3
    GUEST_PER_PROVIDER: Final[int] = 2
    KEYLESS_PER_PROVIDER: Final[int] = 2
    #: How many un-keyed providers ride along beside the keyed ones. Two guests
    #: at 2 models each lands every combination in the 6-9 target: one key -> 7,
    #: two keys -> 8, no keys -> 6.
    GUEST_PROVIDERS: Final[int] = 2
    #: Preference order for the guest slots and the keyless spread. Matches the
    #: desktop composer's provider priority so the pill and the auto-selected
    #: model can never disagree about which provider leads.
    PROVIDER_ORDER: Final[tuple[str, ...]] = (
        "openai",
        "anthropic",
        "openrouter",
        "gemini",
    )

    @classmethod
    def select(
        cls,
        records: Iterable[CatalogModelRecord],
        *,
        user_key_providers: frozenset[str] = frozenset(),
    ) -> tuple[str, ...]:
        """Return the default-enabled model ids, in display order."""

        pool = tuple(records)
        providers = cls._providers_present(pool)
        keyed = [p for p in providers if p in user_key_providers]
        # A gateway resells the same models the direct providers offer, so an
        # un-keyed one would just double every row ("GPT-5.6" and "GPT-5.6
        # (OpenRouter)"). It earns a slot only when it is the key the user holds
        # — which is exactly when it is their route to those models.
        guests = [
            p
            for p in providers
            if p not in user_key_providers
            and p not in ModelSizeTierResolver.GATEWAY_PROVIDERS
        ]
        selected: list[str] = []
        if keyed:
            for provider in keyed:
                selected += cls._ids(pool, provider, cls.KEYED_PER_PROVIDER)
            for provider in guests[: cls.GUEST_PROVIDERS]:
                selected += cls._ids(pool, provider, cls.GUEST_PER_PROVIDER)
        else:
            for provider in guests:
                selected += cls._ids(pool, provider, cls.KEYLESS_PER_PROVIDER)
        # Dedupe while preserving order: a model can be reachable from two
        # providers (OpenRouter mirrors the direct lines) and must appear once.
        seen: set[str] = set()
        return tuple(i for i in selected if not (i in seen or seen.add(i)))

    @classmethod
    def _providers_present(cls, records: Sequence[CatalogModelRecord]) -> list[str]:
        available = {record.provider for record in records}
        ordered = [p for p in cls.PROVIDER_ORDER if p in available]
        # Anything outside the known order still gets a slot, deterministically.
        return ordered + sorted(available - set(ordered))

    @classmethod
    def _ids(
        cls, records: Sequence[CatalogModelRecord], provider: str, count: int
    ) -> list[str]:
        return [
            record.model_id
            for _, record in ModelSizeTierResolver.tiers(
                records, provider=provider, count=count
            )
        ]


__all__ = ["DefaultModelSelectionPolicy", "ModelSizeTierResolver", "ModelTier"]
