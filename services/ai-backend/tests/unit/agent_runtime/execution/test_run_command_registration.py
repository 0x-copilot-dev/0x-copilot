"""Registration of ``run_command`` — the change that makes Phase 1 visible.

PRD: ``docs/plan/agent-execution/PRD-shell-execution.md`` §7.1, §16.6, §17,
§18 Phase 1.

Phase 0 shipped dark: the ``EXECUTE`` axis, its ladder rung and the approval lane
all landed with no tool behind them. This file is about the seam that ends that —
``execution.factory._model_visible_tools``, the ONE place a model tool is
composed — and it pins the claims a registration has to earn:

**1. Absent unless composed.** ``run_command_tool=None`` is the posture of every
deployment the moment this lands, because all four §7.1 prerequisites must hold
before ``runtime_worker.shell_composition`` builds anything: the deployment flag,
the desktop profile, a workspace that is writable AND shell-enabled by the user,
and a loadable never-list. The append site is therefore tested from the OFF side
first. A registration that read as "on for everyone" would pass a presence test
and fail this one.

**2. The prompt and the tool list cannot disagree.** ``NO_SHELL_EXECUTE_GUIDANCE``
says, verbatim, that the run "has no shell/terminal command tool". Shipping that
sentence beside a live ``run_command`` is not cosmetic: it is the prompt telling
the model the opposite of its own tool list, and a model reading it talks the
user out of a capability it actually has. The two facts derive from one field,
``RuntimeDependencies.run_command_tool``, but they are read at two different
sites (``_model_visible_tools`` and ``_instructions_with_capability_tools``, the
latter reached through ``_prompt_assembly_plan(shell_execute_active=...)``, which
carries a default). Only an assertion downstream of BOTH reads catches a caller
that composes the tool and keeps the denial, so those cases build the harness.

**3. Declared in all three occupancy declarations**, or the worker fails at boot
(§16.6). The conformance gate is what turns a missing row into a startup failure
rather than a first-call surprise.

The ownership half — that the composed tool's resident schema cost is charged to
``agent_runtime.capabilities.shell`` rather than to a package that did not author
its description — is asserted here too, because the surface that pins every other
tool's owner label (``test_model_tool_declarations.py``) composes without this
seam supplied and therefore cannot see it.

What is deliberately NOT here: whether a command is permitted. That is the PEP's
question and it is answered in
``tests/unit/agent_runtime/capabilities/shell/test_policy_gate.py``. Registration
only decides whether the model may ever ASK.
"""

from __future__ import annotations

import inspect
from typing import Any

from langchain_core.tools import StructuredTool

from agent_runtime.capabilities.operations.builtin_catalog import (
    DEFAULT_BUILTIN_OPERATION_CATALOG,
)
from agent_runtime.capabilities.operations.catalog import DEFAULT_OPERATION_DESCRIPTORS
from agent_runtime.capabilities.operations.conformance import (
    OperationConformanceGate,
    current_capability_registrations,
)
from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeDependencies
from agent_runtime.execution.deep_agent_builder import (
    FILESYSTEM_IS_NOT_SHELL_GUIDANCE,
    NO_SHELL_EXECUTE_GUIDANCE,
    SHELL_EXECUTE_GUIDANCE,
)
from agent_runtime.execution.factory import (
    RuntimeHarness,
    _model_visible_tools,
    acreate_agent_runtime,
)
from agent_runtime.execution.tool_surface import ModelToolOwner
from agent_runtime.observability.context_tool_ledger import ToolSchemaLedger
from tests.unit.agent_runtime.agent.helpers import CapturingAgentBuilder


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


class _SkillRegistry:
    async def load_skill_by_name(self, _name: str) -> object:
        raise AssertionError("composing the tool surface must not load a skill")


class ShellRegistrationMixin:
    """Compose the model surface with only the seams a case names supplied."""

    class Values:
        #: The model-facing tool name, the trailing URN segment, and the catalog
        #: row's ``op`` — one string in production, spelled once here so a case
        #: cannot pass by asserting a different one.
        TOOL = "run_command"
        CAPABILITY = "builtin"
        SANDBOX_TOOL = "run_in_sandbox"
        REGISTRY_TOOL = "web_search"
        #: deepagents' placeholder, which must stay theirs (§4.1).
        TAKEN_NAME = "execute"
        TAKEN_OWNER = "deepagents.middleware.filesystem.FilesystemMiddleware"

    def tool(self, name: str) -> StructuredTool:
        async def invoke(value: str = "") -> str:
            return value

        return StructuredTool.from_function(
            coroutine=invoke,
            name=name,
            description=f"{name} test tool",
        )

    def compose(
        self, runtime_context: AgentRuntimeContext, **gated: object
    ) -> tuple[object, ...]:
        return _model_visible_tools(
            tools=(self.tool(self.Values.REGISTRY_TOOL),),
            mcp_registry=_McpRegistry(),
            skill_registry=_SkillRegistry(),
            prior_tool_result_loader=object(),
            mcp_discovery_cache=None,
            runtime_context=runtime_context,
            **gated,  # type: ignore[arg-type]
        )

    def names(self, tools: tuple[object, ...]) -> tuple[str, ...]:
        return tuple(str(getattr(tool, "name", "")) for tool in tools)

    async def build_harness(
        self,
        runtime_context: AgentRuntimeContext,
        dependencies: RuntimeDependencies,
        **overrides: Any,
    ) -> CapturingAgentBuilder:
        """Assemble the real harness and return the captured build request."""

        builder = CapturingAgentBuilder()
        harness = await acreate_agent_runtime(
            context=runtime_context,
            dependencies=dependencies.model_copy(update=overrides),
            agent_builder=builder,
        )
        assert isinstance(harness, RuntimeHarness)
        return builder


class TestTheAppendSite(ShellRegistrationMixin):
    """``execution.factory._model_visible_tools`` — the one composition site."""

    def test_the_tool_is_absent_unless_the_worker_composed_one(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        """OFF is the default, and it is the default for everyone.

        This is the assertion that fails if the append site is ever made
        unconditional, or if the seam is defaulted to a built tool rather than
        to ``None``. Every deployment that is not a desktop with an attached,
        writable, shell-enabled folder composes exactly this surface.
        """

        composed = self.names(self.compose(runtime_context_admin))

        assert self.Values.TOOL not in composed

    def test_a_composed_tool_reaches_the_model_under_its_own_name(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        """Present when supplied — and still called ``run_command``.

        The name is asserted rather than assumed because the append site wraps
        the tool (``wrap_model_tool_for_shadow``) before it lands, and a wrapper
        that renamed it would leave ``builtin:shell:run_command`` — the policy
        identity, the catalog row and the PDP descriptor — pointing at a tool
        the model calls by another name.
        """

        composed = self.names(
            self.compose(
                runtime_context_admin,
                run_command_tool=self.tool(self.Values.TOOL),
            )
        )

        assert self.Values.TOOL in composed

    def test_it_is_composed_behind_the_other_model_tools(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        """Position matters: appended last is inside every middleware lane.

        Display, tool-policy, approval and budget middleware all key off
        composition order. The one tool in this list that runs a process on the
        user's own machine is the last one that should be privileged out of the
        ordinary lane, so it is appended beside the other gated capability tools
        rather than ahead of them.
        """

        composed = self.names(
            self.compose(
                runtime_context_admin,
                sandbox_execute_tool=self.tool(self.Values.SANDBOX_TOOL),
                run_command_tool=self.tool(self.Values.TOOL),
            )
        )

        assert composed.index(self.Values.TOOL) > composed.index(
            self.Values.SANDBOX_TOOL
        )
        assert composed.index(self.Values.TOOL) > composed.index(
            self.Values.REGISTRY_TOOL
        )

    def test_its_schema_cost_is_charged_to_the_shell_package(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        """The occupancy ledger names an owner a reader can open.

        Behavioural, not a source sweep: a declaration made under the wrong
        branch satisfies the AST conformance gate and still measures as
        ``UNDECLARED`` here. The label is what an occupancy report prints beside
        this tool's resident token cost on every model call of every run that
        has it.

        The label's ``name`` half is the built object's own name. The AST
        inventory in ``tests/unit/test_context_origin_gate.py`` reads source and
        spells the same declaration ``:run_command_tool``, the append site's
        symbol. Both conventions predate this tool — ``sandbox`` carries the
        identical pair — and both are pinned, so unifying them has to move two
        files on purpose.
        """

        composed = self.compose(
            runtime_context_admin, run_command_tool=self.tool(self.Values.TOOL)
        )
        footprints = {
            footprint.tool_name: footprint
            for footprint in ToolSchemaLedger.measure(composed)
        }

        assert footprints[self.Values.TOOL].declared
        assert footprints[self.Values.TOOL].label == (
            f"{ModelToolOwner.SHELL}:{self.Values.TOOL}"
        )
        assert not footprints[self.Values.TOOL].third_party


class TestThePromptAndTheToolListCannotDisagree(ShellRegistrationMixin):
    """§17 / AC10.1 — asserted over the assembled harness, not either site."""

    async def test_without_the_tool_the_prompt_says_there_is_no_shell(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """AC10.1, the OFF half: the denial ships and the tool is absent."""

        builder = await self.build_harness(runtime_context_admin, fake_dependencies)
        call = builder.calls[0]

        assert NO_SHELL_EXECUTE_GUIDANCE in call.system_prompt
        assert SHELL_EXECUTE_GUIDANCE not in call.system_prompt
        assert self.Values.TOOL not in self.names(tuple(call.tools))

    async def test_with_the_tool_the_denial_is_withdrawn(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """The ON half. A live tool and "you have no shell" cannot coexist."""

        builder = await self.build_harness(
            runtime_context_admin,
            fake_dependencies,
            run_command_tool=self.tool(self.Values.TOOL),
        )
        call = builder.calls[0]

        assert self.Values.TOOL in self.names(tuple(call.tools))
        assert SHELL_EXECUTE_GUIDANCE in call.system_prompt
        assert NO_SHELL_EXECUTE_GUIDANCE not in call.system_prompt

    async def test_the_filesystem_distinction_still_ships_with_the_tool(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """§17: ``FILESYSTEM_IS_NOT_SHELL_GUIDANCE`` is unchanged and stays.

        Its point — that ``read_file`` and ``edit_file`` are bounded file APIs
        rather than a shell — remains true when ``run_command`` is present, and
        it is what stops a model conflating the two lanes. Dropping it as "now
        redundant" is the tempting edit this pins against.
        """

        builder = await self.build_harness(
            runtime_context_admin,
            fake_dependencies,
            run_command_tool=self.tool(self.Values.TOOL),
        )

        assert FILESYSTEM_IS_NOT_SHELL_GUIDANCE in builder.calls[0].system_prompt

    async def test_the_guidance_states_the_four_things_a_model_gets_wrong(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """§17 requires the block to be honest about all four, by meaning.

        Asserted on substrings that would change the model's BELIEF about what
        it may do — approval every time, no state between calls, closed stdin,
        and no undo — rather than on the sentences carrying them. The last has a
        product consequence: §10.2 says commands are outside undo and says so in
        three places, and this is one of the three.
        """

        builder = await self.build_harness(
            runtime_context_admin,
            fake_dependencies,
            run_command_tool=self.tool(self.Values.TOOL),
        )
        prompt = builder.calls[0].system_prompt

        assert "approves every command before it runs" in prompt
        assert "Nothing carries between calls" in prompt
        assert "stdin is closed" in prompt
        assert "CANNOT UNDO" in prompt


class TestTheOccupancyDeclarations(ShellRegistrationMixin):
    """§16.6 — declared in all three, or the worker fails at boot.

    ``OperationConformanceGate.validate_current()`` is called from
    ``runtime_worker.loop``; a registration missing from any one of the three
    declarations takes the worker down at start rather than at first call. The
    three are asserted individually as well, because the gate's failure names a
    coverage gap and not which file is short.
    """

    def test_the_conformance_gate_accepts_the_current_registrations(self) -> None:
        OperationConformanceGate.validate_current()  # raises on any gap

    def test_it_is_declared_as_a_capability_registration(self) -> None:
        assert any(
            registration.capability == self.Values.CAPABILITY
            and registration.op == self.Values.TOOL
            for registration in current_capability_registrations()
        )

    def test_it_carries_a_builtin_catalog_row_that_is_model_visible(self) -> None:
        entry = DEFAULT_BUILTIN_OPERATION_CATALOG.resolve_tool_name(self.Values.TOOL)

        assert entry is not None
        assert entry.model_visible

    def test_it_carries_an_operation_descriptor(self) -> None:
        descriptor = DEFAULT_OPERATION_DESCRIPTORS.resolve(
            capability=self.Values.CAPABILITY, op=self.Values.TOOL
        )

        assert descriptor is not None

    def test_the_taken_execute_name_is_still_somebody_elses(self) -> None:
        """§4.1: ``execute`` is deepagents' placeholder and must stay theirs.

        Naming ours ``execute`` would be a silent identity merge in the policy
        layer — two capabilities resolving to one URN and inheriting each
        other's rules. The registration keeps them separate; this notices if a
        later "tidy the names" edit does not.
        """

        owners = {
            registration.source
            for registration in current_capability_registrations()
            if registration.op == self.Values.TAKEN_NAME
        }

        assert owners == {self.Values.TAKEN_OWNER}


class TestTheDependencySeam:
    """``RuntimeDependencies`` carries the seam, empty by default and threaded.

    The chain from a composed tool to a model call has three hops, and each is
    proved somewhere different:

    1. ``ShellWorkerBundle.compose`` builds it, or returns ``None`` —
       ``tests/unit/runtime_worker/test_shell_composition.py``;
    2. ``RuntimeRunHandler`` carries it into ``RuntimeDependencies`` — here,
       because it is the hop with no other proof; and
    3. ``_model_visible_tools`` appends it — ``TestTheAppendSite`` above.

    Hop 2 is asserted from source, following the pattern
    ``test_sandbox_composition.py`` already uses for its own wiring claim. The
    behavioural alternative is constructing a whole ``RuntimeRunHandler``, and
    the failure being guarded against is not subtle logic — it is the seam
    quietly not being passed at all, which is how a capability lands complete
    and stays dark.
    """

    def test_the_seam_defaults_to_none(self) -> None:
        """The default is this phase's whole blast radius.

        Every caller that does not know about ``run_command`` — every test,
        every non-desktop worker image, every web run — composes a
        byte-identical model surface.
        """

        assert RuntimeDependencies.model_fields["run_command_tool"].default is None

    def test_the_run_handler_threads_a_composed_tool_into_the_dependencies(
        self,
    ) -> None:
        """Hop 2: ``handle`` resolves it, ``_dependencies_for_run`` carries it.

        Both halves, because either alone is dead: a handler that composes the
        tool and drops it on the floor, or a dependencies builder that accepts a
        parameter nobody passes, would each leave ``run_command`` built,
        screened, policed — and never reaching the model.
        """

        from runtime_worker.handlers.run import RuntimeRunHandler

        handle = inspect.getsource(RuntimeRunHandler.handle)
        build = inspect.getsource(RuntimeRunHandler._dependencies_for_run)

        assert "run_command_tool=await self._run_command_tool(command)" in handle
        assert 'update["run_command_tool"] = run_command_tool' in build
