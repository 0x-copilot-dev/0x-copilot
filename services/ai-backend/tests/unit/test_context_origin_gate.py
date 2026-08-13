"""PRD-02 canaries for the context-origin conformance gate.

This is the keystone test of the Context Occupancy Ledger. Everything else in
the program measures; this is the part that makes measurement *complete*, by
refusing to let a contributor reach the model's context window without saying
what it is. It has the same two halves as ``test_llm_seam_gate``, and for the
same reasons:

1. **The sweep must be empty.** ``undeclared_context_contributors`` failing means
   somebody added text to the provider request and did not declare its origin,
   so the occupancy report would charge it to ``undeclared_tokens`` and name
   nobody. The remedy is always a declaration at the composition site, never an
   entry in an allowlist.
2. **The inventory must be pinned.** Declaring correctly is not the last step —
   the golden tuple below has to move too, and reviewing that diff is the moment
   an author consciously accepts what their contribution costs on *every* model
   call of *every* run.

The planted-tree tests underneath exist because a gate that returns ``()`` is
indistinguishable from a gate that is not looking. Each one plants a minimal,
syntactically real source tree with exactly one defect and asserts the gate
names it.
"""

from __future__ import annotations

from pathlib import Path

from agent_runtime.observability.context_origin_conformance import (
    CONTEXT_COMPOSITION_SITE,
    MODEL_TOOL_OWNER_REGISTRY,
    PROMPT_SOURCE_REGISTRY,
    context_origin_inventory,
    declared_origin_registry_complete,
    undeclared_context_contributors,
)


_SRC_ROOT = Path(__file__).resolve().parents[2] / "src"


def test_every_context_contributor_declares_an_origin() -> None:
    assert undeclared_context_contributors(_SRC_ROOT) == ()
    assert declared_origin_registry_complete(_SRC_ROOT)


def test_declared_context_origin_inventory_is_reviewed() -> None:
    # Adding a line here is the moment the author accepts the context cost.
    #
    # Every entry is an owner-namespaced declaration of something that occupies
    # the model's context window on real runs: a tool's schema block, a system
    # fragment, or a message contributor. A new tool is not merely a new
    # capability — it is resident rent charged on every model call of every run
    # until somebody removes it, which is why the gate refuses to let one appear
    # without this diff. Deleting a line is the same review in reverse: it
    # asserts those bytes are genuinely gone, not merely undeclared.
    #
    # The ``name`` half of a tool entry is the *declaration site's symbol*
    # (``publish_artifact_tool``, ``LoadMcpServerTool``), not the string the
    # model calls, because a tool's declared name is read off the built object
    # at runtime. For a system source it is the ``fragment_id`` its registered
    # provider allocates. Two sites are forbidden from sharing a label, so the
    # substitution cannot cost this pin its resolution.
    assert context_origin_inventory(_SRC_ROOT) == (
        "agent_runtime.capabilities.backends:publish_artifact_tool",
        "agent_runtime.capabilities.backends:revise_artifact_tool",
        "agent_runtime.capabilities.dataflow:stage_rowset_write_tool",
        "agent_runtime.capabilities.desktop:50_workspace_guidance",
        # Accepted rent: one schema block per model call, in exchange for the
        # model being able to establish what is already connected. Its
        # neighbour below can only ever describe connectors that are NOT
        # connected, so without this entry a run whose prompt suppresses the
        # MCP card enumeration has no model-visible route to a connected
        # server — and reaches for a Connect card the user does not need.
        "agent_runtime.capabilities.discovery:ListConnectedServersTool",
        "agent_runtime.capabilities.discovery:SuggestMcpConnectorTool",
        "agent_runtime.capabilities.interpreter:code_mode_tool",
        "agent_runtime.capabilities.mcp.catalog:40_suggested_connectors",
        "agent_runtime.capabilities.mcp:20_mcp_cards",
        "agent_runtime.capabilities.mcp:AuthMcpTool",
        "agent_runtime.capabilities.mcp:LoadMcpServerTool",
        # The per-tool MCP surface — now the ONLY arm. `mcp_dispatcher` was
        # the other, and it is deleted along with the umbrella `call_mcp_tool`.
        # The cost this declares is therefore no longer a choice between two
        # shapes: one umbrella schema whose arguments were a server name and a
        # payload has become N real tool schemas, so the rent is proportional
        # to the connectors a user has installed rather than constant.
        "agent_runtime.capabilities.mcp:per_tool_mcp_tools",
        "agent_runtime.capabilities.sandbox:sandbox_execute_tool",
        "agent_runtime.capabilities.skills:30_skill_cards",
        "agent_runtime.capabilities.skills:LoadSkillTool",
        # Accepted rent: one schema block per model call, in exchange for a
        # three-call read chain costing one model turn and one tool result
        # instead of three of each. The description is deliberately terse — the
        # work this tool saves is the model's own repeated calling, so a long
        # explanation would spend the saving it exists to buy.
        "agent_runtime.capabilities.tool_program:program_tool",
        "agent_runtime.capabilities.tools:AskAQuestionTool",
        "agent_runtime.capabilities.tools:LoadPriorToolResultTool",
        "agent_runtime.capabilities.workspace:binary_b64",
        "agent_runtime.capabilities:60_capability_guidance",
        "agent_runtime.capabilities:citation_pointer_note",
        "agent_runtime.capabilities:tool_budget_note",
        "agent_runtime.context.memory:subagent_trace",
        "agent_runtime.context.memory:summary",
        "agent_runtime.context:offload_stub",
        "agent_runtime.conversation:assistant_text",
        "agent_runtime.conversation:assistant_thinking",
        "agent_runtime.conversation:assistant_tool_calls",
        "agent_runtime.conversation:tool_result",
        "agent_runtime.conversation:user",
        "agent_runtime.execution.factory:10_application_context_boundary",
        "agent_runtime.execution.model_invocation:response_format",
        "agent_runtime.execution:state_file",
        "agent_runtime.prompts.runtime:00_base_runtime",
        "agent_runtime.prompts:assembly_joiner",
        "deepagents.middleware:tools",
    )


def test_inventory_covers_every_model_tool_composed_by_the_factory() -> None:
    """The tool half of the pin must not silently lose contributors.

    The inventory is deduplicated, so a labelling bug that collapsed several
    append sites onto one entry would shrink the tuple rather than fail. The
    gate's duplicate check makes that a violation, and this asserts the
    resulting count directly: one entry per composition site in
    ``_model_visible_tools``, which today is the registry seed plus thirteen
    appends plus the P2-8 per-tool MCP extend. The ``call_mcp_tool`` umbrella
    and the per-tool surface are the arms of one branch — a run composes one or
    the other, never both — so the count is of declaring SITES, as it has
    always been, not of tools any single run pays for.
    """

    tool_owners = (
        "agent_runtime.capabilities.backends",
        "agent_runtime.capabilities.dataflow",
        "agent_runtime.capabilities.discovery",
        "agent_runtime.capabilities.interpreter",
        "agent_runtime.capabilities.mcp",
        "agent_runtime.capabilities.sandbox",
        "agent_runtime.capabilities.skills",
        "agent_runtime.capabilities.tools",
        "deepagents.middleware",
    )
    composed = [
        label
        for label in context_origin_inventory(_SRC_ROOT)
        if label.rsplit(":", 1)[0] in tool_owners
        and not label.rsplit(":", 1)[1][:1].isdigit()
    ]
    # 14 since the umbrella `call_mcp_tool` was retired: it was one composed
    # site, and the per-tool surface that replaced it is a single site too.
    assert len(composed) == 14


class PlantedSourceTreeMixin:
    """Write the smallest source tree the gate can form a judgement about.

    The gate reads source rather than importing it, which is what lets a test
    plant a defect that could not exist in the real tree without breaking the
    build. Each planted file is the *shape* the gate keys on and nothing more:
    the owner enum, the prompt-source registry, and a factory whose tool-surface
    function and assembly call the test supplies.
    """

    OWNER_MEMBER = "TOOLS"
    OWNER_VALUE = "agent_runtime.capabilities.tools"
    SECOND_OWNER_MEMBER = "MCP"
    SECOND_OWNER_VALUE = "agent_runtime.capabilities.mcp"
    SOURCE_MEMBER = "MCP_CARDS"
    SOURCE_VALUE = "mcp_cards"
    FRAGMENT_ID = "20_mcp_cards"
    PROMPT_OWNER = "agent_runtime.capabilities.mcp"

    DECLARED_APPEND = (
        "    model_tools.append(\n"
        "        ModelToolDeclaration.declared(\n"
        "            _structured_tool(AskAQuestionTool(), AskAQuestionInput),\n"
        "            owner=ModelToolOwner.TOOLS,\n"
        "        )\n"
        "    )\n"
    )

    def plant(
        self,
        root: Path,
        *,
        surface_body: str,
        assembly: str | None = None,
        registered_sources: tuple[str, ...] | None = None,
    ) -> None:
        """Plant the owner enum, the source registry, and the factory."""

        self.plant_owner_registry(root)
        self.plant_prompt_registry(root, registered_sources=registered_sources)
        self.plant_factory(root, surface_body=surface_body, assembly=assembly)

    def plant_owner_registry(self, root: Path) -> None:
        path = root / MODEL_TOOL_OWNER_REGISTRY
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "from enum import StrEnum\n"
            "\n"
            "\n"
            "class ModelToolOwner(StrEnum):\n"
            f'    {self.OWNER_MEMBER} = "{self.OWNER_VALUE}"\n'
            f'    {self.SECOND_OWNER_MEMBER} = "{self.SECOND_OWNER_VALUE}"\n',
            encoding="utf-8",
        )

    def plant_prompt_registry(
        self,
        root: Path,
        *,
        registered_sources: tuple[str, ...] | None = None,
    ) -> None:
        registrations = "".join(
            "    RegisteredPromptFragmentProvider(\n"
            f"        source=PromptSource.{member},\n"
            f'        fragment_id="{self.FRAGMENT_ID}",\n'
            "    ),\n"
            for member in (
                registered_sources
                if registered_sources is not None
                else (self.SOURCE_MEMBER,)
            )
        )
        path = root / PROMPT_SOURCE_REGISTRY
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "from enum import StrEnum\n"
            "\n"
            "\n"
            "class PromptSource(StrEnum):\n"
            f'    {self.SOURCE_MEMBER} = "{self.SOURCE_VALUE}"\n'
            "\n"
            "\n"
            "DEFAULT_PROMPT_FRAGMENT_PROVIDERS = (\n"
            f"{registrations}"
            ")\n",
            encoding="utf-8",
        )

    def plant_factory(
        self,
        root: Path,
        *,
        surface_body: str,
        assembly: str | None = None,
    ) -> None:
        path = root / CONTEXT_COMPOSITION_SITE
        path.parent.mkdir(parents=True, exist_ok=True)
        assembly_block = (
            ""
            if assembly is None
            else (
                "\n\n"
                "def _prompt_assembly_plan(runtime_context):\n"
                "    return PromptAssemblyInputs(\n"
                "        context=_prompt_assembly_context(runtime_context),\n"
                f"{assembly}"
                "    )\n"
            )
        )
        path.write_text(
            "def _model_visible_tools(tools):\n"
            f"{surface_body}"
            "    return tuple(model_tools)\n"
            f"{assembly_block}",
            encoding="utf-8",
        )

    @staticmethod
    def declared_material(owner: str) -> str:
        return (
            "        mcp_cards=_prompt_source_material(\n"
            f'            owner="{owner}",\n'
            '            content="cards",\n'
            "        ),\n"
        )


class TestModelToolSurfaceGate(PlantedSourceTreeMixin):
    """The half that makes adding an undeclared tool a build failure."""

    def test_undeclared_append_is_named_by_its_composition_symbol(
        self,
        tmp_path: Path,
    ) -> None:
        self.plant(
            tmp_path,
            surface_body=(
                "    model_tools = list(\n"
                "        ModelToolDeclaration.declared_all(\n"
                "            (wrap_model_tool_for_shadow(tool) for tool in tools),\n"
                "            owner=ModelToolOwner.MCP,\n"
                "        )\n"
                "    )\n"
                "    model_tools.append(\n"
                "        _structured_tool(PublishArtifactTool(), PublishArtifactInput)\n"
                "    )\n"
            ),
        )

        assert undeclared_context_contributors(tmp_path) == (
            "agent_runtime/execution/factory.py:_model_visible_tools -> "
            "PublishArtifactTool is composed into model_tools without a "
            "context-origin declaration",
        )

    def test_undeclared_seed_assignment_is_reported(self, tmp_path: Path) -> None:
        self.plant(
            tmp_path,
            surface_body="    model_tools = list(tools)\n",
        )

        assert undeclared_context_contributors(tmp_path) == (
            "agent_runtime/execution/factory.py:_model_visible_tools -> "
            "tools is composed into model_tools without a "
            "context-origin declaration",
        )

    def test_declaration_may_precede_the_append(self, tmp_path: Path) -> None:
        self.plant(
            tmp_path,
            surface_body=(
                "    model_tools = []\n"
                "    tool = _structured_tool(AskAQuestionTool(), AskAQuestionInput)\n"
                "    declare_context_origin(tool, ASK_A_QUESTION_ORIGIN)\n"
                "    model_tools.append(tool)\n"
            ),
        )
        origins = tmp_path / "agent_runtime" / "capabilities" / "origins.py"
        origins.parent.mkdir(parents=True, exist_ok=True)
        origins.write_text(
            "ASK_A_QUESTION_ORIGIN = ContextOrigin(\n"
            '    owner="agent_runtime.capabilities.tools",\n'
            '    name="ask_a_question",\n'
            ")\n",
            encoding="utf-8",
        )

        assert undeclared_context_contributors(tmp_path) == ()
        assert (
            "agent_runtime.capabilities.tools:ask_a_question"
            in context_origin_inventory(tmp_path)
        )

    def test_inline_origin_literal_declares_and_inventories(
        self,
        tmp_path: Path,
    ) -> None:
        self.plant(
            tmp_path,
            surface_body=(
                "    model_tools = []\n"
                "    model_tools.append(\n"
                "        declare_context_origin(\n"
                "            _structured_tool(AskAQuestionTool(), AskAQuestionInput),\n"
                "            ContextOrigin(\n"
                '                owner="agent_runtime.capabilities.tools",\n'
                '                name="ask_a_question",\n'
                "            ),\n"
                "        )\n"
                "    )\n"
            ),
        )

        assert undeclared_context_contributors(tmp_path) == ()
        assert context_origin_inventory(tmp_path) == (
            "agent_runtime.capabilities.tools:ask_a_question",
        )

    def test_literal_owner_is_accepted_where_the_enum_has_no_member(
        self,
        tmp_path: Path,
    ) -> None:
        """Owners are open strings by design (§4.1); only *computed* ones fail.

        The gate's requirement is that a label can be read without running the
        program, not that every owner passes through ``ModelToolOwner``. A
        literal is as reviewable in the golden inventory as an enum member is.
        """

        self.plant(
            tmp_path,
            surface_body=(
                "    model_tools = []\n"
                "    model_tools.append(\n"
                "        ModelToolDeclaration.declared(\n"
                "            _structured_tool(AskAQuestionTool(), AskAQuestionInput),\n"
                '            owner="agent_runtime.capabilities.experiments",\n'
                "        )\n"
                "    )\n"
            ),
        )

        assert undeclared_context_contributors(tmp_path) == ()
        assert context_origin_inventory(tmp_path) == (
            "agent_runtime.capabilities.experiments:AskAQuestionTool",
        )

    def test_every_element_of_an_extended_batch_is_judged(
        self,
        tmp_path: Path,
    ) -> None:
        """Extending with a literal batch is as many contributions as elements."""

        self.plant(
            tmp_path,
            surface_body=(
                "    model_tools = []\n"
                "    model_tools.extend(\n"
                "        [\n"
                "            ModelToolDeclaration.declared(\n"
                "                _structured_tool(AskAQuestionTool(), AskInput),\n"
                "                owner=ModelToolOwner.TOOLS,\n"
                "            ),\n"
                "            _structured_tool(RogueTool(), RogueInput),\n"
                "        ]\n"
                "    )\n"
            ),
        )

        assert undeclared_context_contributors(tmp_path) == (
            "agent_runtime/execution/factory.py:_model_visible_tools -> "
            "RogueTool is composed into model_tools without a "
            "context-origin declaration",
        )

    def test_augmented_assignment_is_a_contribution(self, tmp_path: Path) -> None:
        self.plant(
            tmp_path,
            surface_body=(
                "    model_tools = []\n"
                "    model_tools += [_structured_tool(RogueTool(), RogueInput)]\n"
            ),
        )

        assert undeclared_context_contributors(tmp_path) == (
            "agent_runtime/execution/factory.py:_model_visible_tools -> "
            "RogueTool is composed into model_tools without a "
            "context-origin declaration",
        )

    def test_declaration_of_an_unnameable_subject_is_refused(
        self,
        tmp_path: Path,
    ) -> None:
        """A declaration the inventory cannot spell is not a declaration.

        The label would be pinned as whatever the expression happened to look
        like, which is the one thing the golden tuple must never be.
        """

        self.plant(
            tmp_path,
            surface_body=(
                "    model_tools = []\n"
                "    model_tools.append(\n"
                "        ModelToolDeclaration.declared(\n"
                "            _built_tools(runtime_context)[0],\n"
                "            owner=ModelToolOwner.TOOLS,\n"
                "        )\n"
                "    )\n"
            ),
        )

        assert undeclared_context_contributors(tmp_path) == (
            "agent_runtime/execution/factory.py:_model_visible_tools -> a tool "
            "composed into model_tools declares a context origin that cannot be "
            "named statically",
        )

    def test_declaration_bound_to_a_local_name_covers_the_append(
        self,
        tmp_path: Path,
    ) -> None:
        self.plant(
            tmp_path,
            surface_body=(
                "    model_tools = []\n"
                "    tool = ModelToolDeclaration.declared(\n"
                "        _structured_tool(AskAQuestionTool(), AskAQuestionInput),\n"
                "        owner=ModelToolOwner.TOOLS,\n"
                "    )\n"
                "    model_tools.append(tool)\n"
            ),
        )

        assert undeclared_context_contributors(tmp_path) == ()
        assert context_origin_inventory(tmp_path) == (
            "agent_runtime.capabilities.tools:AskAQuestionTool",
        )

    def test_unresolvable_origin_reference_is_refused(self, tmp_path: Path) -> None:
        """A declaration nobody can trace back to a label is not a declaration."""

        self.plant(
            tmp_path,
            surface_body=(
                "    model_tools = []\n"
                "    tool = _structured_tool(AskAQuestionTool(), AskAQuestionInput)\n"
                "    declare_context_origin(tool, resolve_origin(tool))\n"
                "    model_tools.append(tool)\n"
            ),
        )

        assert undeclared_context_contributors(tmp_path) == (
            "agent_runtime/execution/factory.py:_model_visible_tools -> "
            "tool is declared with a context origin that is not a literal "
            "declaration this gate can inventory",
        )

    def test_computed_owner_cannot_stand_in_for_a_declaration(
        self,
        tmp_path: Path,
    ) -> None:
        self.plant(
            tmp_path,
            surface_body=(
                "    model_tools = []\n"
                "    model_tools.append(\n"
                "        ModelToolDeclaration.declared(\n"
                "            _structured_tool(AskAQuestionTool(), AskAQuestionInput),\n"
                "            owner=owner_for(runtime_context),\n"
                "        )\n"
                "    )\n"
            ),
        )

        assert undeclared_context_contributors(tmp_path) == (
            "agent_runtime/execution/factory.py:_model_visible_tools -> "
            "AskAQuestionTool declares a context origin whose owner is not a "
            "ModelToolOwner member",
        )

    def test_two_sites_may_not_collapse_onto_one_label(self, tmp_path: Path) -> None:
        self.plant(
            tmp_path,
            surface_body=(
                f"    model_tools = []\n{self.DECLARED_APPEND}{self.DECLARED_APPEND}"
            ),
        )

        assert undeclared_context_contributors(tmp_path) == (
            "agent_runtime.capabilities.tools:AskAQuestionTool is declared at "
            "more than one composition site: "
            "agent_runtime/execution/factory.py:_model_visible_tools",
        )

    def test_transforming_the_declared_surface_is_not_a_contribution(
        self,
        tmp_path: Path,
    ) -> None:
        """Wrapping declared tools adds no bytes and needs no new declaration.

        The exemption is structural rather than a name allowlist: it holds only
        because the transform reads the already-declared list, which is exactly
        what makes it a transform.
        """

        self.plant(
            tmp_path,
            surface_body=(
                "    model_tools = []\n"
                f"{self.DECLARED_APPEND}"
                "    model_tools = tuple(wrap_tools_with_display(model_tools))\n"
            ),
        )

        assert undeclared_context_contributors(tmp_path) == ()

    def test_docstrings_and_comments_do_not_compose_tools(
        self,
        tmp_path: Path,
    ) -> None:
        self.plant(
            tmp_path,
            surface_body=(
                '    """Explains model_tools.append(PublishArtifactTool())."""\n'
                "    # model_tools.append(_structured_tool(RogueTool(), RogueInput))\n"
                "    model_tools = []\n"
                f"{self.DECLARED_APPEND}"
            ),
        )

        assert undeclared_context_contributors(tmp_path) == ()

    def test_missing_composition_site_is_detected(self, tmp_path: Path) -> None:
        assert undeclared_context_contributors(tmp_path) == (
            "agent_runtime/execution/factory.py: the context-origin composition "
            "site is absent; the gate cannot see what occupies the model's "
            "context",
        )
        assert context_origin_inventory(tmp_path) == ()

    def test_renaming_the_tool_surface_does_not_disarm_the_gate(
        self,
        tmp_path: Path,
    ) -> None:
        """A gate that stops finding its subject must fail, not pass."""

        self.plant_owner_registry(tmp_path)
        self.plant_prompt_registry(tmp_path)
        path = tmp_path / CONTEXT_COMPOSITION_SITE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "def _compose_model_tools(tools):\n"
            "    model_tools = list(tools)\n"
            "    return tuple(model_tools)\n",
            encoding="utf-8",
        )

        assert undeclared_context_contributors(tmp_path) == (
            "agent_runtime/execution/factory.py: _model_visible_tools is absent; "
            "the gate cannot see the model tool surface",
        )


class TestPromptSourceGate(PlantedSourceTreeMixin):
    """The half that keeps the system block attributable to its owners."""

    def test_declared_source_joins_owner_and_registered_fragment_id(
        self,
        tmp_path: Path,
    ) -> None:
        self.plant(
            tmp_path,
            surface_body=f"    model_tools = []\n{self.DECLARED_APPEND}",
            assembly=self.declared_material(self.PROMPT_OWNER),
        )

        assert undeclared_context_contributors(tmp_path) == ()
        assert f"{self.PROMPT_OWNER}:{self.FRAGMENT_ID}" in context_origin_inventory(
            tmp_path
        )

    def test_conditional_source_is_read_through_to_its_material(
        self,
        tmp_path: Path,
    ) -> None:
        self.plant(
            tmp_path,
            surface_body=f"    model_tools = []\n{self.DECLARED_APPEND}",
            assembly=(
                "        mcp_cards=(\n"
                "            PromptSourceMaterial(\n"
                f'                source_owner="{self.PROMPT_OWNER}",\n'
                "                content=mcp_block,\n"
                "            )\n"
                "            if mcp_block\n"
                "            else None\n"
                "        ),\n"
            ),
        )

        assert undeclared_context_contributors(tmp_path) == ()
        assert f"{self.PROMPT_OWNER}:{self.FRAGMENT_ID}" in context_origin_inventory(
            tmp_path
        )

    def test_assembly_moved_to_another_module_is_refused(
        self,
        tmp_path: Path,
    ) -> None:
        """The one-file scope must not be escapable by moving the call.

        This is the failure a conformance proof can least afford: the sweep
        would find nothing to complain about and report success, while the
        sources it exists to check went unread.
        """

        self.plant(
            tmp_path,
            surface_body=f"    model_tools = []\n{self.DECLARED_APPEND}",
        )
        elsewhere = tmp_path / "agent_runtime" / "prompts" / "assembly_site.py"
        elsewhere.parent.mkdir(parents=True, exist_ok=True)
        elsewhere.write_text(
            "def build_inputs(runtime_context):\n"
            "    return PromptAssemblyInputs(\n"
            "        context=_context(runtime_context),\n"
            "        mcp_cards=_prompt_source_material(\n"
            f'            owner="{self.PROMPT_OWNER}",\n'
            '            content="cards",\n'
            "        ),\n"
            "    )\n",
            encoding="utf-8",
        )

        assert undeclared_context_contributors(tmp_path) == (
            "agent_runtime/prompts/assembly_site.py:build_inputs -> "
            "PromptAssemblyInputs is composed outside "
            "agent_runtime/execution/factory.py; its sources are not covered by "
            "the context-origin gate",
        )

    def test_computed_source_owner_is_refused(self, tmp_path: Path) -> None:
        self.plant(
            tmp_path,
            surface_body=f"    model_tools = []\n{self.DECLARED_APPEND}",
            assembly=(
                "        mcp_cards=_prompt_source_material(\n"
                "            owner=owner_for(runtime_context),\n"
                '            content="cards",\n'
                "        ),\n"
            ),
        )

        assert undeclared_context_contributors(tmp_path) == (
            "agent_runtime/execution/factory.py:_prompt_assembly_plan -> "
            "PromptAssemblyInputs(mcp_cards=...) has no statically declared "
            "source owner",
        )

    def test_material_built_by_an_unreviewed_helper_is_refused(
        self,
        tmp_path: Path,
    ) -> None:
        """A third constructor hides the owner literal behind a call.

        Adding one is allowed — the gate just insists it be taught here first,
        which is the review that keeps "who owns these bytes" answerable from
        the composition site.
        """

        self.plant(
            tmp_path,
            surface_body=f"    model_tools = []\n{self.DECLARED_APPEND}",
            assembly="        mcp_cards=_mcp_card_material(runtime_context),\n",
        )

        assert undeclared_context_contributors(tmp_path) == (
            "agent_runtime/execution/factory.py:_prompt_assembly_plan -> "
            "PromptAssemblyInputs(mcp_cards=...) has no statically declared "
            "source owner",
        )

    def test_unknown_prompt_source_keyword_is_refused(self, tmp_path: Path) -> None:
        self.plant(
            tmp_path,
            surface_body=f"    model_tools = []\n{self.DECLARED_APPEND}",
            assembly=(
                "        agent_notes=_prompt_source_material(\n"
                f'            owner="{self.PROMPT_OWNER}",\n'
                '            content="notes",\n'
                "        ),\n"
            ),
        )

        assert undeclared_context_contributors(tmp_path) == (
            "agent_runtime/execution/factory.py:_prompt_assembly_plan -> "
            "PromptAssemblyInputs(agent_notes=...) is not a declared "
            "PromptSource member",
        )

    def test_source_without_a_registered_provider_is_refused(
        self,
        tmp_path: Path,
    ) -> None:
        self.plant(
            tmp_path,
            surface_body=f"    model_tools = []\n{self.DECLARED_APPEND}",
            assembly=self.declared_material(self.PROMPT_OWNER),
            registered_sources=(),
        )

        assert undeclared_context_contributors(tmp_path) == (
            "agent_runtime/execution/factory.py:_prompt_assembly_plan -> "
            "PromptAssemblyInputs(mcp_cards=...) has no registered prompt "
            "fragment provider",
        )

    def test_keyword_expansion_defeats_static_declaration(
        self,
        tmp_path: Path,
    ) -> None:
        self.plant(
            tmp_path,
            surface_body=f"    model_tools = []\n{self.DECLARED_APPEND}",
            assembly="        **materials,\n",
        )

        assert undeclared_context_contributors(tmp_path) == (
            "agent_runtime/execution/factory.py:_prompt_assembly_plan -> "
            "PromptAssemblyInputs is composed by keyword expansion; its sources "
            "cannot be declared statically",
        )

    def test_absent_source_is_not_a_contributor(self, tmp_path: Path) -> None:
        """A source explicitly passed as ``None`` occupies nothing."""

        self.plant(
            tmp_path,
            surface_body=f"    model_tools = []\n{self.DECLARED_APPEND}",
            assembly="        mcp_cards=None,\n",
        )

        assert undeclared_context_contributors(tmp_path) == ()

    def test_missing_prompt_registry_is_reported_at_the_assembly_site(
        self,
        tmp_path: Path,
    ) -> None:
        self.plant_owner_registry(tmp_path)
        self.plant_factory(
            tmp_path,
            surface_body=f"    model_tools = []\n{self.DECLARED_APPEND}",
            assembly=self.declared_material(self.PROMPT_OWNER),
        )

        assert undeclared_context_contributors(tmp_path) == (
            "agent_runtime/execution/factory.py:_prompt_assembly_plan -> "
            "agent_runtime/prompts/sources.py is absent; prompt sources cannot "
            "be checked for a declared origin",
        )


class TestDeclaredOriginRegistryCompleteness(PlantedSourceTreeMixin):
    """Every ``PromptSource`` member must have a fragment id to be labelled by."""

    def test_registry_is_complete_when_every_member_is_registered(
        self,
        tmp_path: Path,
    ) -> None:
        self.plant_prompt_registry(tmp_path)

        assert declared_origin_registry_complete(tmp_path) is True

    def test_unregistered_member_leaves_the_registry_incomplete(
        self,
        tmp_path: Path,
    ) -> None:
        """An unregistered source renders nothing — until it silently does.

        The assembler skips a source with no provider, so the failure mode is
        not a crash: it is a source that one day carries bytes and can never
        carry a label.
        """

        self.plant_prompt_registry(tmp_path, registered_sources=())

        assert declared_origin_registry_complete(tmp_path) is False

    def test_missing_registry_is_not_complete(self, tmp_path: Path) -> None:
        assert declared_origin_registry_complete(tmp_path) is False


class TestLiteralOriginInventory(PlantedSourceTreeMixin):
    """Declarations that are neither a tool nor a prompt source still pin."""

    def test_literal_declarations_anywhere_join_the_inventory(
        self,
        tmp_path: Path,
    ) -> None:
        self.plant(
            tmp_path,
            surface_body=f"    model_tools = []\n{self.DECLARED_APPEND}",
        )
        classifier = tmp_path / "agent_runtime" / "observability" / "messages.py"
        classifier.parent.mkdir(parents=True, exist_ok=True)
        classifier.write_text(
            "class MessageContextOrigins:\n"
            "    SUMMARY = ContextOrigin(\n"
            '        owner="agent_runtime.context.memory",\n'
            '        name="summary",\n'
            "        segment_class=ContextSegmentClass.MESSAGES,\n"
            "    )\n",
            encoding="utf-8",
        )

        assert undeclared_context_contributors(tmp_path) == ()
        assert context_origin_inventory(tmp_path) == (
            "agent_runtime.capabilities.tools:AskAQuestionTool",
            "agent_runtime.context.memory:summary",
        )

    def test_projected_origins_are_not_mistaken_for_declarations(
        self,
        tmp_path: Path,
    ) -> None:
        """A projection of somebody else's declaration is not a new one.

        ``_fragment_origin`` and the third-party adapter build origins from
        values they were handed. Demanding literals of them would push authors
        to inline strings the runtime would then have to keep in sync.
        """

        self.plant(
            tmp_path,
            surface_body=f"    model_tools = []\n{self.DECLARED_APPEND}",
        )
        recorder = tmp_path / "agent_runtime" / "observability" / "recorder.py"
        recorder.parent.mkdir(parents=True, exist_ok=True)
        recorder.write_text(
            "def _fragment_origin(fragment):\n"
            "    return ContextOrigin(\n"
            "        owner=fragment.source_owner,\n"
            "        name=fragment.fragment_id,\n"
            "    )\n",
            encoding="utf-8",
        )

        assert undeclared_context_contributors(tmp_path) == ()
        assert context_origin_inventory(tmp_path) == (
            "agent_runtime.capabilities.tools:AskAQuestionTool",
        )
