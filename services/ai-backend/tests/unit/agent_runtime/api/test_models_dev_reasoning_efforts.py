"""The per-model reasoning-effort ladder models.dev publishes.

models.dev carries the ladder in ``reasoning_options``, a key that is a *sibling*
of ``reasoning`` rather than nested inside it::

    "reasoning": true,
    "reasoning_options": [
      {"type": "effort", "values": ["none","low","medium","high","xhigh","max"]}
    ]

``reasoning`` really is a boolean, so reading only it looks correct and still
loses the ladder — which is exactly what the parser did, leaving ``xhigh``
unreachable end to end even though the runtime could express it.

These tests pin the two properties that keep the ladder honest as the vendors
move: an unrecognised rung costs that rung and never the model, and an absent
ladder means *unknown* (empty) rather than *no reasoning*.
"""

from __future__ import annotations

from typing import Any

from agent_runtime.api.model_catalog import ModelCatalog
from agent_runtime.api.models_dev_source import ModelsDevCatalogPolicy
from agent_runtime.execution.contracts import ModelReasoningEffort
from agent_runtime.settings import RuntimeSettings

from tests.unit.agent_runtime.api.test_model_catalog import _FakeSource
from agent_runtime.api.litellm_model_source import CatalogModelRecord


class ModelsDevPayloadMixin:
    """Payload fragments in the shape models.dev actually serves."""

    #: Verified against the live models.dev entry for ``openai/gpt-5.6-sol``.
    SOL_LADDER: tuple[str, ...] = (
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    )

    @classmethod
    def model(cls, options: Any) -> dict[str, Any]:
        return {"id": "gpt-5.6-sol", "reasoning": True, "reasoning_options": options}

    @classmethod
    def effort_option(cls, values: Any) -> list[dict[str, Any]]:
        return [{"type": "effort", "values": values}]

    @classmethod
    def efforts(cls, options: Any) -> list[str]:
        return [
            effort.value
            for effort in ModelsDevCatalogPolicy.reasoning_efforts(cls.model(options))
        ]


class CatalogRecordMixin:
    """Records for driving ``ModelCatalog.build`` without a live source."""

    @classmethod
    def record(
        cls,
        *,
        provider: str = "openai",
        efforts: tuple[ModelReasoningEffort, ...] = (),
    ) -> CatalogModelRecord:
        return CatalogModelRecord(
            provider=provider,
            model_id="gpt-5.6-sol",
            display_name="GPT-5.6 Sol",
            supports_reasoning=True,
            reasoning_efforts=efforts,
            supports_tools=True,
        )

    @classmethod
    def item_for(cls, record: CatalogModelRecord) -> Any:
        ModelCatalog.configure_source(_FakeSource((record,)))
        catalog = ModelCatalog.build(RuntimeSettings.load())
        return next(item for item in catalog if item.model_name == record.model_id)


class TestReasoningEffortLadderParsing(ModelsDevPayloadMixin):
    def test_reads_the_ladder_from_the_sibling_key(self) -> None:
        assert self.efforts(self.effort_option(list(self.SOL_LADDER))) == list(
            self.SOL_LADDER
        )

    def test_xhigh_and_max_are_expressible(self) -> None:
        # The two rungs that postdate the runtime's original vocabulary.
        assert ModelReasoningEffort("xhigh") is ModelReasoningEffort.XHIGH
        assert ModelReasoningEffort("max") is ModelReasoningEffort.MAX

    def test_unknown_rung_costs_the_rung_not_the_model(self) -> None:
        # A vendor adding a rung we have never heard of must not empty the ladder.
        assert self.efforts(self.effort_option(["low", "ultra", "xhigh"])) == [
            "low",
            "xhigh",
        ]

    def test_non_effort_option_kinds_are_skipped(self) -> None:
        # ``reasoning_options`` is a typed option list; budget_tokens/toggle
        # entries earn their own fields once their wire shape is verified.
        options = [
            {"type": "budget_tokens", "min": 1024},
            *self.effort_option(["high"]),
        ]
        assert self.efforts(options) == ["high"]

    def test_duplicate_rungs_collapse_in_source_order(self) -> None:
        assert self.efforts(self.effort_option(["high", "low", "high"])) == [
            "high",
            "low",
        ]

    def test_absent_ladder_is_unknown_not_none(self) -> None:
        payload = {"id": "gpt-5.6-sol", "reasoning": True}
        assert ModelsDevCatalogPolicy.reasoning_efforts(payload) == ()

    def test_malformed_payloads_degrade_to_empty(self) -> None:
        # Every one of these is a shape an upstream index could plausibly emit.
        assert self.efforts("not-a-list") == []
        assert self.efforts([None, 7, "nope"]) == []
        assert self.efforts(self.effort_option("high")) == []
        assert self.efforts(self.effort_option([None, 7])) == []
        assert self.efforts([{"values": ["high"]}]) == []


class TestReasoningEffortReachesTheWire(CatalogRecordMixin):
    def test_ladder_is_published_for_a_native_reasoning_provider(self) -> None:
        item = self.item_for(
            self.record(
                efforts=(ModelReasoningEffort.HIGH, ModelReasoningEffort.XHIGH),
            )
        )
        assert item.reasoning_efforts == ("high", "xhigh")

    def test_empty_ladder_is_omitted_rather_than_sent_as_empty(self) -> None:
        # Absent means "unknown" on the wire, so the client falls back to its own
        # rungs instead of hiding the control.
        assert self.item_for(self.record()).reasoning_efforts is None

    def test_ladder_is_withheld_when_reasoning_is_not_exposed(self) -> None:
        # Advertising rungs for a provider whose reasoning the catalog does not
        # expose would offer a control the run path then ignores.
        item = self.item_for(
            self.record(provider="openrouter", efforts=(ModelReasoningEffort.HIGH,))
        )
        assert item.supports_reasoning is False
        assert item.reasoning_efforts is None
