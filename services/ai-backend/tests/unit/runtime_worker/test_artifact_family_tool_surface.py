"""The artifact / row-set family's exposure knob, measured on the real surface.

A benchmark against the packaged app put three tool schemas at **3,326
estimated tokens** of resident rent on every model call of every run —
``publish_artifact`` 1,381, ``stage_rowset_write`` 1,223, ``revise_artifact``
722 — out of a ~23k input of which only 15 tokens were the user's prompt. That
text is re-sent whether or not the run ever publishes anything, and on a cold
start it is billed at full price rather than at the cache-read rate, which is
where 71% of measured spend sits.

These tests assert the **surface**, not the flag. A test that reads
``settings.hyperparameters.tool_surface.artifact_family`` back proves only that
Pydantic parsed a string; it would stay green if the knob were wired to nothing,
which is exactly the failure this repository has been bitten by before. So each
test below drives the real worker builders (``RuntimeRunHandler``'s
``_publish_artifact_tool`` / ``_revise_artifact_tool`` /
``_stage_rowset_write_tool``), threads their results through the real
composition seam (``agent_runtime.execution.factory._model_visible_tools``, the
single place model tools are composed), and then asks the composed surface what
is on it and what it costs.

The saving is measured with ``ToolSchemaLedger``, the same instrument the
occupancy report uses, so the number asserted here is the number a reader would
see in ``context_occupancy.jsonl``.

Two directions matter and both are covered:

* ``off`` removes exactly the three, and nothing else — a gate that also
  swallowed a neighbouring tool would be a regression, not a saving; and
* the gate **fails toward including**. The artifact lane is live and has
  causal-lane sealing consequences, so every way of not-saying-off (unset
  document, unset section, an absent knob) must still yield the tools.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from agent_runtime.execution.factory import _model_visible_tools
from agent_runtime.hyperparameters import (
    ArtifactToolFamilyExposure,
    Hyperparameters,
    ToolSurfaceHyperparameters,
)
from agent_runtime.observability.context_tool_ledger import ToolSchemaLedger
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import AgentRunStatus, RunRecord, RuntimeRunCommand
from runtime_worker.handlers.run import RuntimeRunHandler


#: The family under test, by the name the model actually sees.
ARTIFACT_FAMILY = frozenset(
    {"publish_artifact", "revise_artifact", "stage_rowset_write"}
)


class _McpRegistry:
    """The registry seam the factory reads; empty is the honest default here."""

    def list_tools(self) -> tuple[object, ...]:
        return ()


class _SkillRegistry:
    def list_tools(self) -> tuple[object, ...]:
        return ()


class ArtifactSurfaceMixin:
    """Build a real handler, compose the real surface, measure it."""

    #: Turning the lane itself on. ``ARTIFACT_EFFECTS_V2`` is the canonical
    #: floor for the ``artifact_repository`` rollout capability (see
    #: ``LegacyRolloutControls.canonical_floor``), so it admits the lane
    #: without an explicit ``ARTIFACT_REPOSITORY_MODE`` — which would trip the
    #: startup validator's legacy-coexistence check.
    LANE_ON_ENV = {
        "ARTIFACT_EFFECTS_V2": "true",
        "SURFACES_V2": "true",
    }

    ORG_ID = "org_ts"
    USER_ID = "user_ts"
    RUN_ID = "run_ts"

    def settings(self, exposure: str | None) -> RuntimeSettings:
        """Load settings with the lane on and the knob at ``exposure``.

        ``None`` means "say nothing at all", which is the case that has to fail
        toward including.
        """

        environ = dict(self.LANE_ON_ENV)
        if exposure is not None:
            environ["COPILOT_HP__TOOL_SURFACE__ARTIFACT_FAMILY"] = exposure
        return RuntimeSettings.load(environ=environ)

    def runtime_context(self) -> AgentRuntimeContext:
        return AgentRuntimeContext(
            user_id=self.USER_ID,
            org_id=self.ORG_ID,
            roles={"employee"},
            model_profile=ModelConfig(
                provider="openai",
                model_name="gpt-5.4-mini",
                max_input_tokens=4096,
                timeout_seconds=30,
                temperature=0,
            ),
            run_id=self.RUN_ID,
            trace_id="trace_ts",
        )

    def run(self) -> RunRecord:
        return RunRecord(
            run_id=self.RUN_ID,
            conversation_id="conv_ts",
            org_id=self.ORG_ID,
            user_id=self.USER_ID,
            user_message_id="msg_ts",
            trace_id="trace_ts",
            model_provider="openai",
            model_name="gpt-5.4-mini",
            status=AgentRunStatus.RUNNING,
            runtime_context=self.runtime_context(),
        )

    def command(self) -> RuntimeRunCommand:
        return RuntimeRunCommand(
            run_id=self.RUN_ID,
            conversation_id="conv_ts",
            org_id=self.ORG_ID,
            user_id=self.USER_ID,
            trace_id="trace_ts",
            runtime_context=self.runtime_context(),
        )

    def handler(self, settings: RuntimeSettings) -> RuntimeRunHandler:
        """A handler with the artifact service present, as a live run has.

        ``artifact_service`` only has to be non-``None`` for
        ``_artifact_publication_enabled``; the builders under test compose it,
        they never call it.
        """

        store = InMemoryRuntimeApiStore()
        return RuntimeRunHandler(
            persistence=store,
            event_store=store,
            settings=settings,
            queue=store,
            artifact_service=object(),
        )

    def compose(self, exposure: str | None) -> tuple[object, ...]:
        """Assemble the model-visible surface the way the worker does.

        This is the load-bearing helper: the three builders are the worker's
        own, and ``_model_visible_tools`` is the factory's own. Nothing about
        the family's presence is asserted from a flag — it is read back off the
        composed tuple.
        """

        settings = self.settings(exposure)
        handler = self.handler(settings)
        run = self.run()
        return _model_visible_tools(
            tools=(),
            mcp_registry=_McpRegistry(),
            skill_registry=_SkillRegistry(),
            prior_tool_result_loader=None,
            mcp_discovery_cache=None,
            runtime_context=self.runtime_context(),
            publish_artifact_tool=handler._publish_artifact_tool(run),
            revise_artifact_tool=handler._revise_artifact_tool(run),
            stage_rowset_write_tool=handler._stage_rowset_write_tool(
                self.command(), run
            ),
        )

    def names(self, composed: Sequence[object]) -> frozenset[str]:
        return frozenset(str(getattr(tool, "name", "")) for tool in composed)

    def family_tokens(self, composed: Sequence[object]) -> int:
        """Estimated schema tokens the family occupies on the composed surface."""

        return sum(
            footprint.estimated_tokens
            for footprint in ToolSchemaLedger.measure(composed)
            if footprint.tool_name in ARTIFACT_FAMILY
        )


class TestArtifactFamilyExposure(ArtifactSurfaceMixin):
    def test_family_is_on_the_surface_when_the_knob_is_unset(self) -> None:
        """The shipped default keeps every artifact run working, untouched."""

        names = self.names(self.compose(None))

        assert ARTIFACT_FAMILY <= names

    def test_family_is_on_the_surface_when_the_knob_says_always(self) -> None:
        assert ARTIFACT_FAMILY <= self.names(self.compose("always"))

    def test_family_is_absent_from_the_surface_when_the_knob_says_off(self) -> None:
        """The assertion the saving rests on, made against the surface."""

        names = self.names(self.compose("off"))

        assert not (ARTIFACT_FAMILY & names), (
            "the exposure knob is off, so no artifact/row-set schema may reach "
            f"the model; found {sorted(ARTIFACT_FAMILY & names)}"
        )

    def test_off_removes_the_family_and_nothing_else(self) -> None:
        """A saving that also dropped a neighbour would be a regression."""

        on = self.names(self.compose("always"))
        off = self.names(self.compose("off"))

        assert on - off == ARTIFACT_FAMILY
        assert not off - on

    def test_off_saves_the_measured_schema_tokens(self) -> None:
        """Prove the saving with the occupancy report's own instrument.

        The absolute figure moves whenever a description is reworded, so the
        assertion is on the *delta between the two surfaces* plus a floor that
        would catch the family silently shrinking to a stub. The benchmark's
        3,326 is the number this reproduces in spirit; the floor is set well
        under it so a copy-edit does not red the suite.
        """

        on_tokens = self.family_tokens(self.compose("always"))
        off_tokens = self.family_tokens(self.compose("off"))

        assert off_tokens == 0
        assert on_tokens >= 1500, (
            "the artifact family measured far cheaper than the benchmark's "
            f"3,326 tokens ({on_tokens}); either the schemas shrank or the "
            "surface is no longer being measured"
        )


class TestArtifactFamilyFailsTowardIncluding(ArtifactSurfaceMixin):
    """Every way of being wrong must leave the tools on the surface.

    Artifacts are a live capability with ledger consequences; a run that
    legitimately needs to publish must never find the tool missing because a
    knob was defaulted, mistyped, or absent.
    """

    def test_default_document_admits_the_family(self) -> None:
        assert ToolSurfaceHyperparameters().admits_artifact_family(lane_enabled=True)

    def test_section_is_present_and_defaults_to_always_on_a_bare_document(
        self,
    ) -> None:
        """A document that never mentions the section still gets ``always``."""

        assert (
            Hyperparameters().tool_surface.artifact_family
            is ArtifactToolFamilyExposure.ALWAYS
        )

    def test_a_misspelled_value_is_rejected_rather_than_silently_withholding(
        self,
    ) -> None:
        """The dangerous failure is a typo that reads as "off". It cannot."""

        with pytest.raises(Exception) as excinfo:
            self.settings("offf")

        assert "offf" in str(excinfo.value) or "artifact_family" in str(excinfo.value)

    def test_the_knob_cannot_resurrect_a_lane_its_caller_disabled(self) -> None:
        """``always`` is permission, not force: the lane gate still wins."""

        section = ToolSurfaceHyperparameters(
            artifact_family=ArtifactToolFamilyExposure.ALWAYS
        )

        assert not section.admits_artifact_family(lane_enabled=False)
