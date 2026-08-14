"""Progressive disclosure of first-party tool schemas at ``/tools/``.

Every assertion here is made against the **assembled model tool surface** and
measured with :class:`ToolSchemaLedger` — the same instrument the Context
Occupancy Ledger reports with — rather than against a flag. A flag says the
mechanism is switched on; only the ledger says the tool block actually got
smaller, which is the entire claim.

Four things are pinned, because each is a way this could ship looking correct
and be worthless:

1. **The saving is real and measured.** The three deferred tools shed 1,327
   estimated tokens of resident text on the composed surface — 1,712 tokens of
   prose replaced by 385 of summary. Neutering the swap makes this test report
   ``saved 0``, which is what the number is worth checking against.
2. **A blind call still works.** Argument schemas are byte-identical to the
   undeferred surface, so a model that never reads ``/tools/`` still composes a
   well-formed call. This is the design's answer to "what if it never expands?"
   and it is structural, not a retry protocol.
3. **The pointer is unmissable.** Every deferred tool's resident text names the
   exact ``read_file`` call, and ``/tools/TOOLS.md`` exists so a probe lands
   somewhere that explains itself. An index the model cannot find is worse than
   the schemas it replaced — it fails silently.
4. **No mount, no stub.** A run whose ``/tools/`` route declined keeps every
   full description. A pointer to a file that does not exist is the one outcome
   strictly worse than doing nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import uuid4

import pytest

from agent_runtime.capabilities.tools.catalog import (
    DEFERRED_TOOL_SUMMARIES,
    Keys,
    Messages,
    ToolGuidanceCatalog,
)
from agent_runtime.capabilities.tools.catalog_backend import ToolCatalogBackend
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.execution.factory import (
    _model_visible_tools,
    _with_tool_guidance_route,
)
from agent_runtime.observability.context_tool_ledger import ToolSchemaLedger
from agent_runtime.prompts.tools import (
    PUBLISH_ARTIFACT_TOOL_DESCRIPTION,
    REVISE_ARTIFACT_TOOL_DESCRIPTION,
    STAGE_ROWSET_WRITE_TOOL_DESCRIPTION,
)
from agent_runtime.surfaces_v2.ledger_ids import ArtifactIdCodec


#: Measured on the real descriptions before this change, via
#: ``ToolSchemaLedger`` + the heuristic counter: publish_artifact 1362
#: (750 prose / 607 schema), stage_rowset_write 1311 (440 / 864),
#: revise_artifact 687 (522 / 160). Prose is what defers.
DEFERRED_PROSE_TOKENS = 750 + 440 + 522

#: ``ReviseArtifactInput`` requires a canonically-encoded artifact id — the
#: argument schema is enforced exactly as before, which is the point of leaving
#: it resident.
_ARTIFACT_ID = ArtifactIdCodec.format(uuid4())


class _RealDescriptionTool:
    """A domain adapter carrying the tool's REAL authored description.

    The existing declaration harness composes tools with placeholder
    descriptions, which is right for what it measures (owner labels) and useless
    for what this file measures (bytes). A token assertion against
    ``"publish_artifact test tool"`` would pass whatever the deferral did.
    """

    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description
        self.calls: list[dict[str, object]] = []
        self.result: object = {"status": "created"}

    async def ainvoke(self, value: object) -> object:
        self.calls.append(dict(value) if isinstance(value, dict) else {"raw": value})
        return self.result


class _AuthProvider:
    async def create_auth_session(self, **_kwargs: object) -> object:
        raise AssertionError("composing the tool surface must not start OAuth")


class _McpRegistry:
    providers = (_AuthProvider(),)

    async def list_available_servers(
        self, _context: AgentRuntimeContext
    ) -> tuple[object, ...]:
        return ()

    async def resolve_server(self, _name: str) -> object:
        raise AssertionError("composing the tool surface must not resolve a server")


class GuidedSurfaceMixin:
    """Compose the model surface with and without the ``/tools/`` catalog."""

    DEFERRED = ("publish_artifact", "revise_artifact", "stage_rowset_write")

    def adapters(self) -> dict[str, _RealDescriptionTool]:
        return {
            "publish_artifact": _RealDescriptionTool(
                "publish_artifact", PUBLISH_ARTIFACT_TOOL_DESCRIPTION
            ),
            "revise_artifact": _RealDescriptionTool(
                "revise_artifact", REVISE_ARTIFACT_TOOL_DESCRIPTION
            ),
            "stage_rowset_write": _RealDescriptionTool(
                "stage_rowset_write", STAGE_ROWSET_WRITE_TOOL_DESCRIPTION
            ),
        }

    def catalog(self, adapters: dict[str, _RealDescriptionTool]) -> ToolGuidanceCatalog:
        catalog = ToolGuidanceCatalog.of_tools(adapters.values())
        assert catalog is not None
        return catalog

    def compose(
        self,
        runtime_context: AgentRuntimeContext,
        *,
        adapters: dict[str, _RealDescriptionTool],
        guidance: ToolGuidanceCatalog | None,
    ) -> tuple[object, ...]:
        return _model_visible_tools(
            tools=(),
            mcp_registry=_McpRegistry(),
            skill_registry=None,
            prior_tool_result_loader=None,
            mcp_discovery_cache=None,
            stage_rowset_write_tool=adapters["stage_rowset_write"],
            publish_artifact_tool=adapters["publish_artifact"],
            revise_artifact_tool=adapters["revise_artifact"],
            runtime_context=runtime_context,
            tool_guidance=guidance,
        )

    def tokens(self, composed: Sequence[object]) -> dict[str, int]:
        return {
            footprint.tool_name: footprint.estimated_tokens
            for footprint in ToolSchemaLedger.measure(composed)
        }

    def by_name(self, composed: Sequence[object]) -> dict[str, object]:
        return {str(getattr(tool, "name", "")): tool for tool in composed}


class TestTheDeferralIsMeasurableOnTheAssembledSurface(GuidedSurfaceMixin):
    def test_the_three_deferred_tools_shed_the_prose_they_were_measured_at(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        adapters = self.adapters()
        before = self.tokens(
            self.compose(runtime_context_admin, adapters=adapters, guidance=None)
        )
        after = self.tokens(
            self.compose(
                runtime_context_admin,
                adapters=adapters,
                guidance=self.catalog(adapters),
            )
        )

        saved = sum(before[name] - after[name] for name in self.DEFERRED)

        # Measured at 1,327: 1,712 tokens of prose replaced by 385 tokens of
        # resident summary. A stub is not free — it carries the purpose, the one
        # disambiguation that decides WHICH tool, and the pointer — so the
        # saving is bounded above by the prose it replaced. The floor is a
        # regression guard set just under the measured value, not a target: it
        # trips if someone grows a summary back toward the text it stands in for.
        assert 1300 <= saved <= DEFERRED_PROSE_TOKENS, (
            f"deferral saved {saved} tokens; measured prose was "
            f"{DEFERRED_PROSE_TOKENS} across {self.DEFERRED}"
        )

    def test_no_other_tool_on_the_surface_changes_size(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        adapters = self.adapters()
        before = self.tokens(
            self.compose(runtime_context_admin, adapters=adapters, guidance=None)
        )
        after = self.tokens(
            self.compose(
                runtime_context_admin,
                adapters=adapters,
                guidance=self.catalog(adapters),
            )
        )

        untouched = {
            name: (before[name], after[name])
            for name in before
            if name not in self.DEFERRED and before[name] != after[name]
        }

        assert untouched == {}

    def test_ask_a_question_stays_resident(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        # The resident set is a decision, not an oversight: `ask_a_question` is
        # reached while a human is waiting, so a round trip to learn how to ask
        # is the worst place in the system to spend one.
        adapters = self.adapters()
        composed = self.by_name(
            self.compose(
                runtime_context_admin,
                adapters=adapters,
                guidance=self.catalog(adapters),
            )
        )

        assert "ask_a_question" not in DEFERRED_TOOL_SUMMARIES
        assert "Fields:" in str(getattr(composed["ask_a_question"], "description", ""))


class TestABlindCallIsStillWellFormed(GuidedSurfaceMixin):
    def test_argument_schemas_are_byte_identical(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        adapters = self.adapters()
        before = self.by_name(
            self.compose(runtime_context_admin, adapters=adapters, guidance=None)
        )
        after = self.by_name(
            self.compose(
                runtime_context_admin,
                adapters=adapters,
                guidance=self.catalog(adapters),
            )
        )

        for name in self.DEFERRED:
            assert (
                before[name].args_schema.model_json_schema()  # type: ignore[attr-defined]
                == after[name].args_schema.model_json_schema()  # type: ignore[attr-defined]
            ), f"{name} lost argument-schema fidelity when its prose deferred"

    async def test_a_tool_called_without_reading_its_file_still_dispatches(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        adapters = self.adapters()
        composed = self.by_name(
            self.compose(
                runtime_context_admin,
                adapters=adapters,
                guidance=self.catalog(adapters),
            )
        )

        result = await composed["revise_artifact"].ainvoke(  # type: ignore[attr-defined]
            {
                "artifact_id": _ARTIFACT_ID,
                "parent_revision": 1,
                "content": "hello",
            }
        )

        assert adapters["revise_artifact"].calls == [
            {
                "artifact_id": _ARTIFACT_ID,
                "parent_revision": 1,
                "content": "hello",
                "content_ref": None,
            }
        ]
        assert result == {"status": "created"}

    async def test_a_refused_call_is_told_which_file_to_read(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        # The rules a JSON schema cannot express — exactly-one-of-content,
        # compare-and-append, the row/diff accuracy check — are exactly the ones
        # that refuse. Without this the model retries the identical call.
        adapters = self.adapters()
        adapters["revise_artifact"].result = {
            "status": "failed",
            "message": "The artifact has moved on.",
        }
        composed = self.by_name(
            self.compose(
                runtime_context_admin,
                adapters=adapters,
                guidance=self.catalog(adapters),
            )
        )

        result = await composed["revise_artifact"].ainvoke(  # type: ignore[attr-defined]
            {"artifact_id": _ARTIFACT_ID, "parent_revision": 1, "content": "hi"}
        )

        assert result["message"] == "The artifact has moved on."
        assert "/tools/revise_artifact.md" in result[Keys.Field.GUIDANCE]

    async def test_a_successful_call_gains_no_guidance_noise(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        adapters = self.adapters()
        composed = self.by_name(
            self.compose(
                runtime_context_admin,
                adapters=adapters,
                guidance=self.catalog(adapters),
            )
        )

        result = await composed["publish_artifact"].ainvoke(  # type: ignore[attr-defined]
            {
                "kind": "document",
                "title": "t",
                "media_type": "text/markdown",
                "content": "x",
            }
        )

        assert Keys.Field.GUIDANCE not in result


class TestThePointerIsUnmissable(GuidedSurfaceMixin):
    def test_every_deferred_description_names_its_own_read_file_call(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        adapters = self.adapters()
        composed = self.by_name(
            self.compose(
                runtime_context_admin,
                adapters=adapters,
                guidance=self.catalog(adapters),
            )
        )

        for name in self.DEFERRED:
            description = str(getattr(composed[name], "description", ""))
            assert f'read_file("/tools/{name}.md")' in description, (
                f"{name} defers its prose without saying how to get it back"
            )

    def test_the_index_lists_every_published_tool(self) -> None:
        adapters = self.adapters()
        index = self.catalog(adapters).snapshot()["/tools/TOOLS.md"]

        for name in self.DEFERRED:
            assert f"/tools/{name}.md" in index

    def test_a_published_file_carries_the_full_text_verbatim(self) -> None:
        adapters = self.adapters()
        published = self.catalog(adapters).snapshot()["/tools/publish_artifact.md"]

        # Invariant 1: one text, two renderings. The file is built from the
        # tool's own description, so it cannot drift from what the tool does.
        assert PUBLISH_ARTIFACT_TOOL_DESCRIPTION in published

    def test_a_custom_description_is_published_not_the_module_constant(self) -> None:
        custom = _RealDescriptionTool("publish_artifact", "A deployment override.")

        catalog = ToolGuidanceCatalog.of_tools((custom,))

        assert catalog is not None
        assert (
            "A deployment override." in catalog.snapshot()["/tools/publish_artifact.md"]
        )


class TestNoMountMeansNoStub(GuidedSurfaceMixin):
    def test_a_declined_route_keeps_every_full_description(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        adapters = self.adapters()

        # A non-composite backend that is not None: the passthrough case the
        # MCP route declines for the same reason.
        backend, mounted = _with_tool_guidance_route(
            object(),
            catalog=self.catalog(adapters),
            memory_backend=object(),
        )
        composed = self.by_name(
            self.compose(runtime_context_admin, adapters=adapters, guidance=mounted)
        )

        assert mounted is None
        assert backend is not None
        for name in self.DEFERRED:
            assert (
                str(getattr(composed[name], "description", ""))
                == adapters[name].description
            )

    def test_a_run_with_no_deferred_tool_publishes_nothing(self) -> None:
        assert ToolGuidanceCatalog.of_tools(()) is None
        assert ToolGuidanceCatalog.of_tools((None, None)) is None

    def test_a_mounted_route_adds_only_the_tools_prefix(self) -> None:
        from deepagents.backends.composite import CompositeBackend
        from deepagents.backends.state import StateBackend

        existing = CompositeBackend(default=StateBackend(), routes={})
        adapters = self.adapters()

        backend, mounted = _with_tool_guidance_route(
            existing,
            catalog=self.catalog(adapters),
            memory_backend=object(),
        )

        assert mounted is not None
        assert isinstance(backend, CompositeBackend)
        assert set(backend.routes) == {ToolCatalogBackend.PATH_PREFIX}

    def test_an_existing_mcp_route_survives_the_tools_mount(self) -> None:
        from deepagents.backends.composite import CompositeBackend
        from deepagents.backends.state import StateBackend

        sentinel = StateBackend()
        existing = CompositeBackend(default=StateBackend(), routes={"/mcp/": sentinel})

        backend, _ = _with_tool_guidance_route(
            existing,
            catalog=self.catalog(self.adapters()),
            memory_backend=object(),
        )

        assert isinstance(backend, CompositeBackend)
        assert backend.routes["/mcp/"] is sentinel
        assert set(backend.routes) == {"/mcp/", ToolCatalogBackend.PATH_PREFIX}


class TestTheModelReachesItThroughTheCompositeMount(GuidedSurfaceMixin):
    """The hop that matters: a ``read_file`` the model makes, not a direct call.

    ``CompositeBackend`` strips ``/tools`` on the way in and re-prepends it on
    the way out, so a backend that only understood its own public paths would
    look correct in every direct-call test and answer the model NOT FOUND.
    """

    def composite(self) -> object:
        from deepagents.backends.composite import CompositeBackend
        from deepagents.backends.state import StateBackend

        backend, _ = _with_tool_guidance_route(
            CompositeBackend(default=StateBackend(), routes={}),
            catalog=self.catalog(self.adapters()),
            memory_backend=object(),
        )
        assert isinstance(backend, CompositeBackend)
        return backend

    def test_read_file_on_the_public_path_returns_the_guidance(self) -> None:
        result = self.composite().read("/tools/publish_artifact.md")

        content = (result.file_data or {})["content"]
        assert "# publish_artifact" in content
        assert "Choosing an accent" in content

    def test_ls_of_the_public_directory_is_never_an_empty_success(self) -> None:
        result = self.composite().ls("/tools")

        paths = {entry["path"] for entry in (result.entries or [])}
        assert result.error is None
        assert "/tools/TOOLS.md" in paths


class TestTheCatalogIsReadableAndReadOnly(GuidedSurfaceMixin):
    def backend(self) -> ToolCatalogBackend:
        return ToolCatalogBackend(self.catalog(self.adapters()))

    def test_listing_the_mount_root_names_every_file(self) -> None:
        result = self.backend().ls("/")

        assert sorted(entry["path"] for entry in (result.entries or [])) == [
            "/TOOLS.md",
            "/publish_artifact.md",
            "/revise_artifact.md",
            "/stage_rowset_write.md",
        ]

    @pytest.mark.parametrize(
        "path", ["/publish_artifact.md", "/tools/publish_artifact.md"]
    )
    def test_both_path_spellings_read(self, path: str) -> None:
        result = self.backend().read(path)

        assert "# publish_artifact" in (result.file_data or {})["content"]

    def test_grep_finds_a_rule_that_left_the_resident_surface(self) -> None:
        # `accent` moved off every model call and into the file; grep is how the
        # model gets back to it without knowing the filename.
        result = self.backend().grep("accent", None, None)

        assert result.matches

    def test_glob_reaches_the_files_by_pattern(self) -> None:
        result = self.backend().glob("*.md", None)

        assert {match["path"] for match in (result.matches or [])} == {
            "/TOOLS.md",
            "/publish_artifact.md",
            "/revise_artifact.md",
            "/stage_rowset_write.md",
        }

    def test_an_unknown_file_answers_with_a_directive_not_an_empty_success(
        self,
    ) -> None:
        result = self.backend().read("/nope.md")

        assert result.error == Messages.NOT_FOUND
        assert result.file_data is None

    def test_the_model_cannot_rewrite_the_rules_it_is_judged_against(self) -> None:
        backend = self.backend()

        assert backend.write("/publish_artifact.md", "anything").error == (
            Messages.READ_ONLY
        )
        assert backend.edit("/publish_artifact.md", "a", "b").error == (
            Messages.READ_ONLY
        )
