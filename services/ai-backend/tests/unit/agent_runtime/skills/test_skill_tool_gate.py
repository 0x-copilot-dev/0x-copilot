"""A Skill's ``allowed_tools``, asserted as a REFUSAL and not as a return value.

Every enforcement assertion here runs through ``RuntimeControlMiddleware`` — the
object ``execution/factory.py`` composes into ``create_deep_agent(middleware=…)``
— and the headline one drives a whole compiled graph, so "the model cannot call
it" is a fact about a real turn. That matters more than usual for this change:
``allowed_tools`` was already parsed, typed and validated, and a test that
asserted the gate class alone would pass just as green over a gate no tool call
ever reaches, which is precisely the state this change exists to leave.

The arming half is driven through the real ``LoadSkillTool``, not by calling
``record_load`` directly, so the chain the product actually walks — model calls
``load_skill`` → bundle returns → ceiling closes — is the chain under test.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool
from langchain.agents.middleware.types import ToolCallRequest

from agent_runtime.capabilities.middleware.runtime_tool_control import (
    RuntimeControlMiddleware,
)
from agent_runtime.capabilities.skills.middleware import LoadSkillTool
from agent_runtime.capabilities.skills.tool_gate import (
    SkillToolGate,
    SkillToolRule,
)
from agent_runtime.capabilities.skills.virtual import (
    VirtualSkillBundle,
    VirtualSkillRegistry,
)


class Names:
    """Skill and tool names shared across the cases."""

    READ_ONLY_SKILL = "search-subagent-logs"
    SEARCH_SKILL = "web-search-discipline"
    UNRESTRICTED_SKILL = "house-style"
    MCP_SKILL = "linear-triage"

    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    WEB_SEARCH = "web_search"
    TASK = "task"
    LOAD_SKILL = "load_skill"
    WRITE_TODOS = "write_todos"

    MCP_CREATE_ISSUE = "mcp__linear__create_issue"
    MCP_LIST_ISSUES = "mcp__linear__list_issues"
    MCP_NOTION_PAGE = "mcp__notion__append_block"
    BARE_CREATE_ISSUE = "create_issue"


@pytest.fixture(autouse=True)
def _bound_gate():
    """One clean run-scoped gate per test, bound and drained like the worker's."""

    token = SkillToolGate.bind_for_run(SkillToolGate(run_id="run-under-test"))
    try:
        yield
    finally:
        SkillToolGate.unbind(token)


class StubSkillProvider:
    """The two provider methods ``VirtualSkillRegistry`` calls, and nothing else."""

    def __init__(self, *bundles: VirtualSkillBundle) -> None:
        self._bundles = {bundle.name: bundle for bundle in bundles}

    async def list_skill_cards(self):
        return tuple(
            {
                "skill_id": bundle.skill_id,
                "name": bundle.name,
                "display_name": bundle.display_name,
                "description": bundle.description,
                "virtual_path": bundle.virtual_path,
                "scope": "user",
                "source_type": "user",
                "version": bundle.version,
                "allowed_tools": bundle.allowed_tools,
                "enabled": True,
            }
            for bundle in self._bundles.values()
        )

    async def load_skill_by_name(self, name: str) -> VirtualSkillBundle:
        return self._bundles[name]


class SkillGateMixin:
    """Bundle/request builders and the two seams under test."""

    @staticmethod
    def bundle(name: str, *allowed: str) -> VirtualSkillBundle:
        return VirtualSkillBundle(
            skill_id=f"skill-{name}",
            name=name,
            display_name=name,
            description=f"{name} description",
            markdown=f"# {name}",
            virtual_path=f"/skills/{name}/SKILL.md",
            version=1,
            allowed_tools=tuple(allowed),
        )

    @classmethod
    async def load(cls, *bundles: VirtualSkillBundle) -> None:
        """Arm the gate the way the product does: through ``load_skill``."""

        registry = VirtualSkillRegistry(providers=[StubSkillProvider(*bundles)])
        tool = LoadSkillTool(registry=registry)
        for bundle in bundles:
            payload = await tool.ainvoke({"skill_name": bundle.name})
            assert payload["ok"] is True

    @staticmethod
    def request(name: str, *, subagent: bool = False) -> ToolCallRequest:
        config: dict[str, Any] = (
            {"metadata": {"supervisor_task_call_id": "call-parent-1"}}
            if subagent
            else {}
        )
        return ToolCallRequest(
            tool_call={
                "name": name,
                "args": {},
                "id": f"call-{name}",
                "type": "tool_call",
            },
            tool=None,
            state={},
            runtime=cast(Any, SimpleNamespace(config=config)),
        )

    @staticmethod
    def ran(_request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="ran", tool_call_id="call-ok")

    @staticmethod
    async def aran(_request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="ran", tool_call_id="call-ok")

    @classmethod
    def call(cls, name: str, *, subagent: bool = False) -> ToolMessage:
        return cast(
            ToolMessage,
            RuntimeControlMiddleware().wrap_tool_call(
                cls.request(name, subagent=subagent),
                cls.ran,
            ),
        )

    @classmethod
    async def acall(cls, name: str) -> ToolMessage:
        return cast(
            ToolMessage,
            await RuntimeControlMiddleware().awrap_tool_call(
                cls.request(name),
                cls.aran,
            ),
        )


class TestCeilingIsEnforcedAtTheToolSurface(SkillGateMixin):
    """The claim the field's name has always made, now asserted as a refusal."""

    async def test_a_skill_declaring_allowed_tools_refuses_a_tool_outside_it(
        self,
    ) -> None:
        """The headline: declared read-only, so the write never reaches the handler.

        Fails without the gate — before it, ``wrap_tool_call`` ran every named
        tool the model asked for and ``allowed_tools`` reached nothing but the
        prompt.
        """

        await self.load(self.bundle(Names.READ_ONLY_SKILL, "ls", Names.READ_FILE))

        refused = self.call(Names.WRITE_FILE)

        assert refused.status == "error"
        assert Names.WRITE_FILE in refused.content
        assert Names.READ_ONLY_SKILL in refused.content
        # The refusal must name the way out, or it is a wedge, not a ceiling.
        assert Names.LOAD_SKILL in refused.content

    async def test_a_declared_tool_still_runs(self) -> None:
        await self.load(self.bundle(Names.READ_ONLY_SKILL, "ls", Names.READ_FILE))

        assert self.call(Names.READ_FILE).content == "ran"

    async def test_the_async_path_refuses_identically(self) -> None:
        """``awrap_tool_call`` is the path a real run takes; test it separately.

        A ceiling on only the synchronous wrapper would be no ceiling at all —
        the streaming runtime dispatches tools through the async node.
        """

        await self.load(self.bundle(Names.READ_ONLY_SKILL, Names.READ_FILE))

        refused = await self.acall(Names.WRITE_FILE)
        allowed = await self.acall(Names.READ_FILE)

        assert refused.status == "error"
        assert allowed.content == "ran"

    async def test_the_ceiling_reaches_inside_a_subagent(self) -> None:
        """A child's tool calls come back through this same seam, so they are governed.

        Delegation is otherwise the obvious hole: a read-only Skill that could
        spawn a child which writes would be a ceiling with a door in it.
        """

        await self.load(self.bundle(Names.READ_ONLY_SKILL, Names.READ_FILE))

        assert self.call(Names.WRITE_FILE, subagent=True).status == "error"
        assert self.call(Names.READ_FILE, subagent=True).content == "ran"

    async def test_task_is_governed_and_not_floored(self) -> None:
        """Delegation reaches real capability, so it is never in the floor."""

        await self.load(self.bundle(Names.READ_ONLY_SKILL, Names.READ_FILE))

        assert self.call(Names.TASK).status == "error"


class TestArmingRule(SkillGateMixin):
    """When the ceiling exists at all — the half that protects existing runs."""

    def test_a_run_that_loads_no_skill_is_untouched(self) -> None:
        assert self.call(Names.WRITE_FILE).content == "ran"

    async def test_a_skill_declaring_nothing_does_not_arm_the_gate(self) -> None:
        await self.load(self.bundle(Names.UNRESTRICTED_SKILL))

        assert self.call(Names.WRITE_FILE).content == "ran"

    async def test_a_skill_declaring_nothing_does_not_rewiden_a_closed_ceiling(
        self,
    ) -> None:
        """Silence is not a declaration of everything — see the module docstring."""

        await self.load(
            self.bundle(Names.READ_ONLY_SKILL, Names.READ_FILE),
            self.bundle(Names.UNRESTRICTED_SKILL),
        )

        assert self.call(Names.WRITE_FILE).status == "error"

    async def test_loading_a_second_restricting_skill_widens_by_union(self) -> None:
        """Union, never intersection: the first Skill's tools survive the second."""

        await self.load(
            self.bundle(Names.READ_ONLY_SKILL, Names.READ_FILE),
            self.bundle(Names.SEARCH_SKILL, Names.WEB_SEARCH),
        )

        assert self.call(Names.READ_FILE).content == "ran"
        assert self.call(Names.WEB_SEARCH).content == "ran"
        assert self.call(Names.WRITE_FILE).status == "error"

    async def test_the_floor_survives_a_closed_ceiling(self) -> None:
        """The escape hatch, plus the bookkeeping tool that reaches nothing."""

        await self.load(self.bundle(Names.READ_ONLY_SKILL, Names.READ_FILE))

        assert self.call(Names.LOAD_SKILL).content == "ran"
        assert self.call("list_skills").content == "ran"
        assert self.call(Names.WRITE_TODOS).content == "ran"


class TestMcpAddressing(SkillGateMixin):
    """How an author names an MCP tool once every name is ``mcp__<server>__<tool>``."""

    async def test_the_namespaced_name_is_how_an_mcp_tool_resolves(self) -> None:
        await self.load(self.bundle(Names.MCP_SKILL, Names.MCP_CREATE_ISSUE))

        assert self.call(Names.MCP_CREATE_ISSUE).content == "ran"
        assert self.call(Names.MCP_LIST_ISSUES).status == "error"

    async def test_a_bare_connector_register_name_matches_nothing(self) -> None:
        """Deliberate. ``create_issue`` would mean "whichever connector has one"."""

        await self.load(self.bundle(Names.MCP_SKILL, Names.BARE_CREATE_ISSUE))

        assert self.call(Names.MCP_CREATE_ISSUE).status == "error"

    async def test_the_server_form_admits_a_whole_connector(self) -> None:
        await self.load(self.bundle(Names.MCP_SKILL, "mcp__linear"))

        assert self.call(Names.MCP_CREATE_ISSUE).content == "ran"
        assert self.call(Names.MCP_LIST_ISSUES).content == "ran"
        assert self.call(Names.MCP_NOTION_PAGE).status == "error"

    def test_matching_folds_case_because_the_two_registers_disagree(self) -> None:
        """``normalize_slug`` lowercases the manifest; ``McpToolName`` does not."""

        assert SkillToolRule.covers(
            declaration="mcp__linear__createissue",
            tool_name="mcp__linear__createIssue",
        )

    def test_a_native_tool_never_matches_a_connector_declaration(self) -> None:
        assert not SkillToolRule.covers(
            declaration="mcp__linear",
            tool_name=Names.WRITE_FILE,
        )


class TestUnboundGate(SkillGateMixin):
    """Replay, evals and unit tests bind nothing and must not be governed."""

    @pytest.fixture(autouse=True)
    def _bound_gate(self):
        """Override the module fixture: this class runs with NOTHING bound."""

        yield

    def test_an_unbound_gate_admits_everything(self) -> None:
        SkillToolGate.record_skill_load(
            skill_name=Names.READ_ONLY_SKILL,
            allowed_tools=[Names.READ_FILE],
        )

        assert SkillToolGate.active() is None
        assert SkillToolGate.evaluate(Names.WRITE_FILE).allowed
        assert self.call(Names.WRITE_FILE).content == "ran"


class GateModel(FakeListChatModel):
    """Calls the refused tool once, then records what came back."""

    seen: list[Any] = []

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any):
        del args, kwargs
        observed = [
            message.content for message in messages if isinstance(message, ToolMessage)
        ]
        if observed:
            GateModel.seen.extend(observed)
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content="done"))]
            )
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": Names.WRITE_FILE,
                                "args": {"path": "/notes.md"},
                                "id": "call-graph-1",
                            }
                        ],
                    )
                )
            ]
        )

    async def _agenerate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any):
        return self._generate(messages)

    def bind_tools(self, tools: Any, **kwargs: Any):
        del tools, kwargs
        return self


class TestCompiledGraphTurn(SkillGateMixin):
    """The strongest form of the claim: a real turn, a real refusal, no side effect."""

    async def test_the_model_cannot_reach_a_tool_outside_the_loaded_skill(self) -> None:
        """Drives a compiled graph, so nothing here is a middleware unit test.

        ``executed`` is the assertion that matters most — a ceiling that
        refuses the model but still runs the tool would satisfy every
        string assertion above and protect nothing.
        """

        executed: list[str] = []

        def write_file(path: str) -> str:
            executed.append(path)
            return "written"

        await self.load(self.bundle(Names.READ_ONLY_SKILL, Names.READ_FILE))
        GateModel.seen = []
        graph = create_agent(
            model=GateModel(responses=["unused"]),
            tools=[
                StructuredTool.from_function(
                    func=write_file,
                    name=Names.WRITE_FILE,
                    description="write a file",
                )
            ],
            middleware=[RuntimeControlMiddleware()],
        )

        await graph.ainvoke({"messages": [("user", "write the notes")]})

        assert executed == []
        assert GateModel.seen, "the model saw no tool result at all"
        assert Names.WRITE_FILE in GateModel.seen[0]
        assert Names.READ_ONLY_SKILL in GateModel.seen[0]
