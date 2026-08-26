"""``stage_rowset_write``'s gate, asserted where the tokens are actually spent.

**Why this tool and not its two siblings.** Across every run store on the
measuring machine the artifact family did not behave like a family:

===================  ===========  ==========  =====================
tool                 invocations  run events  resident on
===================  ===========  ==========  =====================
``publish_artifact``         142      18,866  883/883 model calls
``revise_artifact``           36       3,930  883/883 model calls
``stage_rowset_write``         0           0  883/883 model calls
===================  ===========  ==========  =====================

(1,441 ``tool_invocations`` rows over 323 run ids; 43,551 run-event lines over
272 sessions; 883 ``context_occupancy`` model-call rows over 475 runs. The
non-zero columns are the positive controls: without them a zero is
indistinguishable from a query that matched nothing, which is exactly the
mistake that produced a first, false zero while investigating this.)

So this is one dead tool inside a live lane, and ``ArtifactToolFamilyExposure``
— an all-or-nothing switch over all three — is the wrong granularity to act on
it with: flipping it would withhold the third most-used tool in the product.
``tool_surface.rowset_staging_tool`` is the right one, and these tests assert
that it withholds exactly one tool and takes its bytes off the wire.

**What "never invoked" does and does not mean.** ``tool_invocations`` rows are
written at ``TOOL_CALL_STARTED`` (``runtime_worker/stream_tools.py``), i.e. when
the model emits the call and *before* any policy, gate or refusal runs — so a
call that was attempted and then rejected would still have left a row. Zero rows
therefore means the model never chose the tool. It does **not** mean no user
anywhere would: this is one machine's corpus, dominated by journey-harness boots
rather than human sessions. Re-opening the knob is a one-line document diff.

The chain driven here is the production one, not a stand-in:

    RuntimeRunHandler._stage_rowset_write_tool   (reads rowset_staging_tool)
        -> execution.factory._model_visible_tools  (appends, or does not)
            -> wrap_tools_with_display             (factory.py:405 applies this)
                -> convert_to_openai_tool          (what LangChain binds)

Every hop matters. The gate lives at the *first* one because a schema is billed
at registration: a tool registered later and refusing at call time would still
cost all 900 tokens. And the display wrap is not optional decoration — measured
on this surface the raw tool is 3,963 B / 978 tokens and the display-wrapped one
is 3,704 B / 900, and it is the wrapped figure that reproduces the live
``context_occupancy.jsonl`` record byte-for-byte. A test that skipped the wrap
would be measuring an object that never reaches a provider.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool

from agent_runtime.capabilities.middleware import wrap_tools_with_display
from agent_runtime.capabilities.tools.catalog import ToolGuidanceCatalog
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from agent_runtime.execution.factory import _model_visible_tools
from agent_runtime.hyperparameters import (
    Hyperparameters,
    RowsetStagingToolExposure,
    ToolSurfaceHyperparameters,
)
from agent_runtime.observability.context_occupancy_recorder import (
    _ToolSchemaCounterBridge,
)
from agent_runtime.observability.context_token_counter import ContextTokenCounter
from agent_runtime.observability.context_tool_ledger import ToolSchemaLedger
from agent_runtime.prompts.tools import STAGE_ROWSET_WRITE_RESIDENT_SUMMARY
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import AgentRunStatus, RunRecord, RuntimeRunCommand
from runtime_worker.handlers.run import RuntimeRunHandler


TOOL_NAME = "stage_rowset_write"

#: A distinctive fragment of the tool's own model-facing text. Asserting on the
#: rendered description rather than only on the name is what makes this a claim
#: about *tokens*: a tool absent from the name list whose description still rode
#: along inside some wrapper would fail here.
DESCRIPTION_FRAGMENT = "Stage a BULK write"

#: A field name unique to ``StageRowsetWriteInput``'s JSON schema.
SCHEMA_FRAGMENT = "agent_holds"

#: The model the live record was written under, so the tokenizer tier matches.
MEASURED_MODEL = "claude-sonnet-5"

#: The live ``context_occupancy.jsonl`` footprint this surface reproduces.
LIVE_BYTES = 3704
LIVE_TOKENS = 900

#: Band around :data:`LIVE_TOKENS`. Wide enough that a copy-edit to the resident
#: summary does not red the suite, narrow enough that a collapse to a stub or a
#: silent revert to the full 1,223-token description would.
TOKEN_FLOOR = 850
TOKEN_CEILING = 950


class _Registry:
    """The registry seam the factory reads; empty is the honest default here."""

    def list_tools(self) -> tuple[object, ...]:
        return ()


class RowsetSurfaceMixin:
    """Build a real handler, compose the real surface, measure it."""

    LANE_ON_ENV = {"ARTIFACT_EFFECTS_V2": "true", "SURFACES_V2": "true"}

    ORG_ID = "org_rs"
    USER_ID = "user_rs"
    RUN_ID = "run_rs"

    def settings(self, exposure: str | None) -> RuntimeSettings:
        """Load settings with the lane on and the row-set knob at ``exposure``.

        ``None`` means "say nothing at all", which must land on the shipped
        document's ``off``.

        ``env_file=os.devnull`` is not decoration. ``RuntimeSettings.load``
        merges ``services/ai-backend/.env`` into the same mapping the
        ``COPILOT_HP__`` overrides resolve against, so without the pin the
        result depends on whether the developer running the suite happens to
        have a ``.env`` — green in a worktree with none, answering to somebody's
        local file in a checkout that has one.
        """

        environ = dict(self.LANE_ON_ENV)
        if exposure is not None:
            environ["COPILOT_HP__TOOL_SURFACE__ROWSET_STAGING_TOOL"] = exposure
        return RuntimeSettings.load(environ=environ, env_file=os.devnull)

    def runtime_context(self) -> AgentRuntimeContext:
        return AgentRuntimeContext(
            user_id=self.USER_ID,
            org_id=self.ORG_ID,
            roles={"employee"},
            model_profile=ModelConfig(
                provider="anthropic-chat",
                model_name=MEASURED_MODEL,
                max_input_tokens=200_000,
                timeout_seconds=30,
                temperature=0,
            ),
            run_id=self.RUN_ID,
            trace_id="trace_rs",
        )

    def run(self) -> RunRecord:
        return RunRecord(
            run_id=self.RUN_ID,
            conversation_id="conv_rs",
            org_id=self.ORG_ID,
            user_id=self.USER_ID,
            user_message_id="msg_rs",
            trace_id="trace_rs",
            model_provider="anthropic-chat",
            model_name=MEASURED_MODEL,
            status=AgentRunStatus.RUNNING,
            runtime_context=self.runtime_context(),
        )

    def command(self) -> RuntimeRunCommand:
        return RuntimeRunCommand(
            run_id=self.RUN_ID,
            conversation_id="conv_rs",
            org_id=self.ORG_ID,
            user_id=self.USER_ID,
            trace_id="trace_rs",
            runtime_context=self.runtime_context(),
        )

    def handler(self, settings: RuntimeSettings) -> RuntimeRunHandler:
        store = InMemoryRuntimeApiStore()
        return RuntimeRunHandler(
            persistence=store,
            event_store=store,
            settings=settings,
            queue=store,
            artifact_service=object(),
        )

    def rowset_tool(self, exposure: str | None) -> object | None:
        """The worker's own builder, under ``exposure``. The gate itself."""

        handler = self.handler(self.settings(exposure))
        return handler._stage_rowset_write_tool(self.command(), self.run())

    def compose(self, exposure: str | None) -> tuple[object, ...]:
        """Assemble the display-wrapped surface exactly as the factory does.

        The guidance catalog is built here rather than passed as ``None``
        because production builds it (``factory.py``'s
        ``ToolGuidanceCatalog.of_tools``) and it is what turns the tool's long
        description into its resident summary. Composing without it measures a
        1,223-token object that no current deployment sends.
        """

        settings = self.settings(exposure)
        handler = self.handler(settings)
        run = self.run()
        publish = handler._publish_artifact_tool(run)
        revise = handler._revise_artifact_tool(run)
        rowset = handler._stage_rowset_write_tool(self.command(), run)
        guidance = ToolGuidanceCatalog.of_tools((rowset, publish, revise))
        composed = _model_visible_tools(
            tools=(),
            mcp_registry=_Registry(),
            skill_registry=_Registry(),
            prior_tool_result_loader=None,
            mcp_discovery_cache=None,
            runtime_context=self.runtime_context(),
            publish_artifact_tool=publish,
            revise_artifact_tool=revise,
            stage_rowset_write_tool=rowset,
            tool_guidance=guidance,
        )
        return tuple(wrap_tools_with_display(composed))

    @staticmethod
    def provider_payload(composed: Sequence[object]) -> str:
        """The tool block as LangChain serializes it when binding to a model."""

        return json.dumps([convert_to_openai_tool(tool) for tool in composed])

    @staticmethod
    def footprints(composed: Sequence[object]) -> dict[str, tuple[int, int]]:
        """Per-tool (bytes, estimated tokens) in the occupancy ledger's units.

        The counter is the recorder's own bridge over ``ContextTokenCounter``,
        so a number asserted here is the number a reader sees in
        ``context_occupancy.jsonl`` — not a char/4 approximation of it.
        """

        bridge = _ToolSchemaCounterBridge(
            counter=ContextTokenCounter(), model=MEASURED_MODEL
        )
        return {
            footprint.tool_name: (footprint.byte_count, footprint.estimated_tokens)
            for footprint in ToolSchemaLedger.measure(composed, counter=bridge)
        }


class TestTheGateWithholdsTheSchema(RowsetSurfaceMixin):
    """The saving, asserted against the bytes rather than against a flag."""

    def test_the_shipped_default_withholds_the_tool_itself(self) -> None:
        """The gate returns ``None``; nothing downstream has to be trusted."""

        assert self.rowset_tool(None) is None

    def test_always_still_builds_the_tool(self) -> None:
        """Re-opening the knob is a document row, not a code change."""

        assert self.rowset_tool("always") is not None

    def test_name_description_and_schema_all_leave_the_provider_payload(
        self,
    ) -> None:
        """Three fragments, because a tool can vanish from a name list and stay.

        The name could be dropped by a wrapper while the description rode along
        inside another tool's text, or the argument schema could survive in a
        union. Asserting all three is asserting the tokens.
        """

        payload = self.provider_payload(self.compose(None))

        assert TOOL_NAME not in payload
        assert DESCRIPTION_FRAGMENT not in payload
        assert SCHEMA_FRAGMENT not in payload

    def test_all_three_are_present_when_the_knob_says_always(self) -> None:
        payload = self.provider_payload(self.compose("always"))

        assert TOOL_NAME in payload
        assert DESCRIPTION_FRAGMENT in payload
        assert SCHEMA_FRAGMENT in payload

    def test_the_description_fragment_is_really_the_text_that_ships(self) -> None:
        """Guard the guard.

        ``DESCRIPTION_FRAGMENT`` is only evidence about tokens while it is
        genuinely in the shipped resident summary. Reword the summary without
        this assertion and the absence test above passes for the wrong reason —
        the fragment is gone because nobody writes it any more, not because the
        gate withheld anything.
        """

        assert DESCRIPTION_FRAGMENT in STAGE_ROWSET_WRITE_RESIDENT_SUMMARY


class TestTheMeasuredSaving(RowsetSurfaceMixin):
    """How many tokens, in the units the occupancy ledger reports them in."""

    def test_the_withheld_footprint_reproduces_the_live_record(self) -> None:
        """A live record reads ``byte_count 3704 / estimated_tokens 900``.

        Reproduced here through the production chain, which is what makes this
        a measurement rather than an estimate. The band is deliberately loose
        around 900 (see :data:`TOKEN_FLOOR` / :data:`TOKEN_CEILING`) so a
        copy-edit does not red the suite, while a collapse to a stub or a silent
        revert to the un-deferred 1,223-token description would fail.
        """

        measured = self.footprints(self.compose("always"))[TOOL_NAME]
        byte_count, tokens = measured

        assert TOKEN_FLOOR <= tokens <= TOKEN_CEILING, (
            f"{TOOL_NAME} measured {tokens} tokens / {byte_count} bytes; the "
            f"live ledger record is {LIVE_TOKENS} / {LIVE_BYTES}. Either the "
            "description changed materially or the surface being measured is "
            "no longer the one production sends."
        )

    def test_the_default_surface_is_smaller_by_that_footprint(self) -> None:
        """The delta on the whole tool block, not just on one row."""

        on = self.footprints(self.compose("always"))
        off = self.footprints(self.compose(None))

        assert TOOL_NAME not in off
        saved = sum(t for _, t in on.values()) - sum(t for _, t in off.values())
        assert TOKEN_FLOOR <= saved <= TOKEN_CEILING, (
            f"withholding {TOOL_NAME} changed the tool block by {saved} tokens, "
            f"not the ~{LIVE_TOKENS} its own footprint accounts for; something "
            "else on the surface moved with it."
        )

    def test_the_two_siblings_are_bit_identical_across_both_surfaces(self) -> None:
        """The whole reason this knob is not ``artifact_family``.

        ``publish_artifact`` is the third most-used tool in the product (142
        invocations over the corpus) and ``revise_artifact`` ran 36 times. Their
        footprints must be *equal*, not merely present: a description that grew
        or shrank as a side effect of removing a neighbour would be a silent
        change to the two tools this work is supposed to leave alone.
        """

        on = self.footprints(self.compose("always"))
        off = self.footprints(self.compose(None))

        for name in ("publish_artifact", "revise_artifact"):
            assert on[name] == off[name], (
                f"{name} measured {off[name]} with the row-set tool withheld and "
                f"{on[name]} with it present; withholding one tool must not "
                "re-render another."
            )


class TestTheGuidanceCatalogDoesNotBackfire(RowsetSurfaceMixin):
    """The failure mode that would turn a -900 saving into a +426 loss.

    ``factory.py`` builds ``ToolGuidanceCatalog.of_tools((rowset, publish,
    revise))``. If a ``None`` first element made the catalog decline to mount,
    ``publish_artifact`` and ``revise_artifact`` would silently revert from
    their resident summaries to their FULL descriptions — and every name-based
    test in this repository would still pass while the tool block grew.
    """

    def test_the_catalog_still_mounts_with_the_rowset_tool_absent(self) -> None:
        handler = self.handler(self.settings(None))
        run = self.run()
        publish = handler._publish_artifact_tool(run)
        revise = handler._revise_artifact_tool(run)

        catalog = ToolGuidanceCatalog.of_tools((None, publish, revise))

        assert catalog is not None
        assert [document.tool_name for document in catalog.documents] == [
            "publish_artifact",
            "revise_artifact",
        ]

    def test_the_siblings_keep_their_resident_summaries_not_full_descriptions(
        self,
    ) -> None:
        """Asserted on the rendered payload, which is where the loss would show.

        ``revise_artifact``'s compare-and-append conflict rule lives only in its
        deferred file; seeing that text on the surface means the description was
        never swapped for its stub.
        """

        payload = self.provider_payload(self.compose(None))

        assert "publish_artifact" in payload
        assert "revise_artifact" in payload
        assert "/tools/publish_artifact.md" in payload
        assert "/tools/revise_artifact.md" in payload


class TestTheKnobItself(RowsetSurfaceMixin):
    """The document contract, including the ways of being wrong."""

    def test_the_bare_document_withholds_the_tool(self) -> None:
        """A document that never mentions the row defaults to withheld."""

        assert (
            Hyperparameters().tool_surface.rowset_staging_tool
            is RowsetStagingToolExposure.OFF
        )

    def test_off_is_the_default_of_the_section(self) -> None:
        assert not ToolSurfaceHyperparameters().admits_rowset_staging_tool(
            lane_enabled=True
        )

    def test_always_admits_the_tool(self) -> None:
        section = ToolSurfaceHyperparameters(
            rowset_staging_tool=RowsetStagingToolExposure.ALWAYS
        )

        assert section.admits_rowset_staging_tool(lane_enabled=True)

    def test_always_is_permission_not_force(self) -> None:
        """The lane gate still wins: a run without the lane gets nothing."""

        section = ToolSurfaceHyperparameters(
            rowset_staging_tool=RowsetStagingToolExposure.ALWAYS
        )

        assert not section.admits_rowset_staging_tool(lane_enabled=False)

    def test_a_misspelled_value_is_rejected_rather_than_read_as_off(self) -> None:
        """A typo must stop the boot, not quietly agree with the default.

        The default already withholds, so a silently-accepted typo would be
        invisible in *both* directions — an operator writing ``alwyas`` to
        re-open the lane would get no tool and no error.
        """

        with pytest.raises(Exception) as excinfo:
            self.settings("alwyas")

        message = str(excinfo.value)
        assert "alwyas" in message or "rowset_staging_tool" in message


class TestTheStagingLaneSurvives(RowsetSurfaceMixin):
    """Withholding the schema must remove no behaviour, only an advertisement.

    Each assertion constructs the object rather than importing the module: an
    import proves the code exists, not that anything reaches it.
    """

    def test_the_builtin_rowset_executor_is_still_registered_for_a_run(self) -> None:
        """Already-approved stages execute by effect KIND, never by tool.

        This is the one that matters most. If a staged row set could not be
        applied once the tool stopped being offered, the saving would have cost
        a live capability rather than an unused advertisement.
        """

        from agent_runtime.effects.contracts import EffectExecutorKind
        from agent_runtime.effects.executor import EffectExecutionScope
        from runtime_worker.builtin_effect_executor import BuiltinRowSetEffectExecutor
        from runtime_worker.mcp_operation_storage import (
            RuntimeMcpEffectCoordinatorFactory,
        )

        run = self.run()
        coordinator = RuntimeMcpEffectCoordinatorFactory(
            event_producer=object(),  # type: ignore[arg-type]
            claims=object(),
            blobs=object(),  # type: ignore[arg-type]
            references=object(),  # type: ignore[arg-type]
            dependencies_factory=object(),
            timeout_seconds=30.0,
        ).for_run(run=run)
        scope = EffectExecutionScope(
            org_id=run.org_id,
            user_id=run.user_id,
            conversation_id=run.conversation_id,
            run_id=run.run_id,
            owner_ref=f"principal://users/{run.user_id}",
        )

        # Resolved, not merely registered: the registry builds the executor, so
        # this fails if the BUILTIN factory were dropped OR left unconstructable.
        executor = coordinator._executors.resolve(
            kind=EffectExecutorKind.BUILTIN, scope=scope
        )

        assert isinstance(executor, BuiltinRowSetEffectExecutor)

    def test_the_user_driven_write_back_still_reaches_the_stager(self) -> None:
        """``SurfaceWriteBackCoordinator`` is the second, non-model producer.

        It stages row sets from the user's own edits with no model involvement,
        so the staged-table review surface keeps a live producer after the tool
        is withheld. What the product loses is the model's ability to *propose*
        a bulk write unasked — a decision, not an accident.
        """

        from agent_runtime.capabilities.surfaces.write_back import (
            SurfaceWriteBackCoordinator,
        )

        stage = SurfaceWriteBackCoordinator._stage

        assert callable(stage)
        assert "stage_rowset" in stage.__code__.co_names

    def test_the_tool_module_is_still_importable_and_its_targets_intact(self) -> None:
        """Withheld, not deleted.

        ``runtime_worker.builtin_effect_executor`` imports ``RowSetEffectProposal``
        and ``reviewed_rowset_target`` from this module to execute an approved
        stage, so removing it would break exactly the path the previous test
        protects.
        """

        from agent_runtime.capabilities.tools.builtin.stage_rowset_write import (
            REVIEWED_ROWSET_TARGETS,
            reviewed_rowset_target,
        )

        assert ("linear", "update_issue") in REVIEWED_ROWSET_TARGETS
        assert (
            reviewed_rowset_target(target_connector="linear", target_op="update_issue")
            is not None
        )
