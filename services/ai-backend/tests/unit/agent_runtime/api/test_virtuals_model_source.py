"""Unit tests for the Virtuals gateway catalog source.

Every test drives the REAL parser/policy over a payload shaped like the live
``compute.virtuals.io/v1/models`` response — no network. The two behaviours
worth pinning are the ones the live data actually broke:

* the FAMILY must land on the names :class:`ModelSizeTierResolver` knows, or a
  Virtuals-hosted frontier model never reaches the size ladder at all;
* records must be emitted newest-version-first WITHIN a family, because
  Virtuals publishes no release date and the resolver's recency comparison
  therefore keeps whatever it saw first.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_runtime.api.model_tiers import ModelSizeTierResolver
from agent_runtime.api.virtuals_model_source import (
    VirtualsCatalogParser,
    VirtualsCatalogPolicy,
    VirtualsModelSource,
)


class VirtualsPayloadMixin:
    """Rows shaped exactly like the gateway's published inventory."""

    @staticmethod
    def row(
        model_id: str,
        *,
        name: str | None = None,
        context: int | None = 1_000_000,
        input_cost: float = 1.0,
        output_cost: float = 2.0,
    ) -> dict[str, Any]:
        return {
            "id": model_id,
            "name": name if name is not None else model_id,
            "description": "…",
            "contextLength": context,
            "pricing": {
                "input": input_cost,
                "output": output_cost,
                "cacheInput": input_cost / 10,
            },
        }

    @classmethod
    def payload(cls, *rows: dict[str, Any]) -> dict[str, Any]:
        return {"data": list(rows)}


class FakeCache:
    """Stands in for :class:`VirtualsCatalogCache` — no disk, no network."""

    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def payload(self) -> Any:  # noqa: D102 - protocol shim
        return self._payload


class TestFamilyDerivation(VirtualsPayloadMixin):
    @pytest.mark.parametrize(
        ("model_id", "expected"),
        [
            # The frontier lines MUST resolve to the resolver's known families.
            ("anthropic-claude-opus-5", "claude-opus"),
            ("anthropic-claude-sonnet-4-6", "claude-sonnet"),
            ("openai-gpt-55", "gpt"),
            ("openai-gpt-56-terra", "gpt-terra"),
            ("openai-gpt-54-mini", "gpt-mini"),
            ("google-gemini-3-5-flash-lite", "gemini-flash-lite"),
            # `preview` is a qualifier, not part of the line — without dropping
            # it this would be `gemini-pro-preview` and miss the ladder.
            ("google-gemini-3-1-pro-preview", "gemini-pro"),
            # `fast` likewise: a variant of opus, not its own line.
            ("anthropic-claude-opus-4-6-fast", "claude-opus"),
            # Alphanumeric version markers are versions (k3, v4, m3).
            ("moonshotai-kimi-k3", "kimi"),
            ("deepseek-deepseek-v4-flash", "deepseek-flash"),
            ("minimax-minimax-m3", "minimax"),
            # Hyphenated vendor names must not be half-stripped.
            ("x-ai-grok-4-5", "grok"),
            ("z-ai-glm-4-7-flash", "glm-flash"),
        ],
    )
    def test_derives_the_product_line(self, model_id: str, expected: str) -> None:
        assert VirtualsCatalogPolicy.family(model_id) == expected

    def test_known_families_reach_the_tier_ladder(self) -> None:
        """The point of the family mapping: frontier rows become ladder rungs."""

        records = VirtualsCatalogParser.parse(
            self.payload(
                self.row("anthropic-claude-opus-5", output_cost=30),
                self.row("openai-gpt-56-luna", output_cost=7.5),
                self.row("google-gemini-3-5-flash-lite", output_cost=3.125),
                # Off the known main lines — present in the catalog, but never a
                # ladder rung, so it cannot become an auto-selected default.
                self.row("moonshotai-kimi-k3", output_cost=18.75),
            )
        )
        ladder = ModelSizeTierResolver.ladder(records, provider="virtuals")

        assert [r.model_id for r in ladder] == [
            "google-gemini-3-5-flash-lite",
            "openai-gpt-56-luna",
            "anthropic-claude-opus-5",
        ]


class TestVersionOrdering(VirtualsPayloadMixin):
    """Virtuals publishes no release date, so the id is the recency signal."""

    def test_ladder_picks_the_current_release_not_the_first_seen(self) -> None:
        # Deliberately oldest-first, the order the live payload happens to use.
        records = VirtualsCatalogParser.parse(
            self.payload(
                self.row("anthropic-claude-opus-4-5", output_cost=30),
                self.row("anthropic-claude-opus-4-8", output_cost=30),
                self.row("anthropic-claude-opus-5", output_cost=30),
            )
        )
        ladder = ModelSizeTierResolver.ladder(records, provider="virtuals")

        # Not claude-opus-4-5, which is what first-wins produced before the
        # source started emitting newest-first.
        assert [r.model_id for r in ladder] == ["anthropic-claude-opus-5"]

    def test_ragged_versions_compare_correctly(self) -> None:
        """``glm-5-2`` outranks ``glm-5`` — the padding case.

        Negating a ragged version tuple puts the SHORTER one first, so an
        unpadded comparison made a base release outrank its own point release.
        """

        records = VirtualsCatalogParser.parse(
            self.payload(
                self.row("z-ai-glm-5"),
                self.row("z-ai-glm-5-2"),
                self.row("z-ai-glm-5-1"),
            )
        )

        assert [r.model_id for r in records] == [
            "z-ai-glm-5-2",
            "z-ai-glm-5-1",
            "z-ai-glm-5",
        ]

    def test_same_version_prefers_the_cheaper_variant(self) -> None:
        """A ladder rung is a DEFAULT, so it must not open on the dearer twin."""

        records = VirtualsCatalogParser.parse(
            self.payload(
                self.row("anthropic-claude-opus-5-fast", output_cost=60),
                self.row("anthropic-claude-opus-5", output_cost=30),
            )
        )

        assert records[0].model_id == "anthropic-claude-opus-5"

    @pytest.mark.parametrize(
        ("model_id", "expected"),
        [
            ("z-ai-glm-5", (5,)),
            ("z-ai-glm-5-2", (5, 2)),
            ("anthropic-claude-opus-4-8", (4, 8)),
            ("openai-gpt-55", (55,)),
            ("moonshotai-kimi-k3", ()),
        ],
    )
    def test_version_parse(self, model_id: str, expected: tuple[int, ...]) -> None:
        assert VirtualsCatalogPolicy.version(model_id) == expected


class TestUntrustedPayload(VirtualsPayloadMixin):
    """The gateway document is external input — malformed rows are dropped."""

    def test_no_snapshot_yields_no_records(self) -> None:
        assert VirtualsModelSource(cache=FakeCache(None)).records() == ()

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"data": None},
            {"data": "not-a-list"},
            {"data": [None, 3, "x"]},
            {"data": [{"no_id": 1}, {"id": ""}, {"id": "   "}]},
        ],
    )
    def test_malformed_shapes_are_skipped_not_raised(self, payload: Any) -> None:
        assert VirtualsCatalogParser.parse(payload) == ()

    def test_implausible_numbers_are_discarded_not_trusted(self) -> None:
        (record,) = VirtualsCatalogParser.parse(
            self.payload(
                {
                    "id": "z-ai-glm-5",
                    "name": "Z: GLM 5",
                    "contextLength": -1,
                    "pricing": {"input": "free", "output": -3},
                }
            )
        )

        assert record.context_window is None
        assert record.input_cost_per_mtok is None
        assert record.output_cost_per_mtok is None

    def test_niche_models_are_excluded(self) -> None:
        """Codex rows are not general chat models — the shared NICHE policy."""

        records = VirtualsCatalogParser.parse(
            self.payload(
                self.row("openai-gpt-52-codex"),
                self.row("openai-gpt-55"),
            )
        )

        assert [r.model_id for r in records] == ["openai-gpt-55"]

    def test_display_name_drops_the_vendor_prefix(self) -> None:
        (record,) = VirtualsCatalogParser.parse(
            self.payload(
                self.row("anthropic-claude-opus-5", name="Anthropic: Claude Opus 5")
            )
        )

        # The picker already groups these rows under Virtuals.
        assert record.display_name == "Claude Opus 5"

    def test_display_name_falls_back_to_the_id(self) -> None:
        (record,) = VirtualsCatalogParser.parse(
            self.payload({"id": "z-ai-glm-5", "name": None})
        )

        assert record.display_name == "z-ai-glm-5"


class TestRecordShape(VirtualsPayloadMixin):
    def test_carries_provider_pricing_and_context(self) -> None:
        (record,) = VirtualsCatalogParser.parse(
            self.payload(
                self.row(
                    "moonshotai-kimi-k3",
                    context=1_000_000,
                    input_cost=3.75,
                    output_cost=18.75,
                )
            )
        )

        assert record.provider == "virtuals"
        assert record.context_window == 1_000_000
        # Virtuals already quotes dollars per MILLION tokens — no conversion.
        assert record.input_cost_per_mtok == 3.75
        assert record.output_cost_per_mtok == 18.75
        assert record.supports_tools is True
        # No release date is published; consumers read absent as "unknown".
        assert record.release_date is None

    def test_reasoning_flag_agrees_with_the_run_path(self) -> None:
        """The catalog must not advertise reasoning the builder won't request."""

        records = VirtualsCatalogParser.parse(
            self.payload(
                self.row("openai-gpt-55"),
                self.row("z-ai-glm-4-7-flash"),
            )
        )
        by_id = {r.model_id: r for r in records}

        # Dash-joined vendor prefix still resolves to the gpt-5 family.
        assert by_id["openai-gpt-55"].supports_reasoning is True
        assert by_id["z-ai-glm-4-7-flash"].supports_reasoning is False
