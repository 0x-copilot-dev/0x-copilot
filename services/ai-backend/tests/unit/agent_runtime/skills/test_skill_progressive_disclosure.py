"""Progressive disclosure for Skills, asserted against the ASSEMBLED PROMPT.

Every visibility assertion here reads ``builder.calls[0].system_prompt`` — the
exact string handed to the deep-agent builder — rather than the registry's
return value. A registry that returns the right list while the prompt still
carries everything is the failure mode this whole change exists to prevent, and
it is invisible to a registry-level test.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest

from agent_runtime.capabilities.skills.constants import Limits
from agent_runtime.capabilities.skills.middleware import (
    ListSkillsInput,
    ListSkillsTool,
    LoadSkillInput,
    LoadSkillTool,
)
from agent_runtime.capabilities.skills.usage import SkillUsageLedger
from agent_runtime.capabilities.skills.virtual import (
    VirtualSkillBundle,
    VirtualSkillCard,
)
from agent_runtime.capabilities.skills.visibility import (
    SkillIndexPlanner,
    SkillVisibilityConditions,
    SkillVisibilityContext,
)
from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeDependencies
from agent_runtime.execution.factory import acreate_agent_runtime
from tests.unit.agent_runtime.agent.helpers import CapturingAgentBuilder
from tests.unit.fakes import FakeMcpRegistry


class Names:
    LINEAR_TRIAGE = "linear_triage"
    LAUNCH_REVIEW = "launch_risk_review"
    MANUAL_SEARCH = "manual_search"


class Connectors:
    LINEAR = "linear"


@dataclass(frozen=True)
class StubConnectorCard:
    """The two attributes the visibility projection reads off an MCP card."""

    name: str
    short_description: str = "Connected connector."
    connector_slug: str | None = None


def card(
    name: str,
    *,
    description: str = "Use when the run needs this skill.",
    metadata: dict[str, object] | None = None,
) -> VirtualSkillCard:
    """Build a compact Skill card with optional frontmatter metadata."""
    return VirtualSkillCard(
        skill_id=f"skill_{name}",
        name=name,
        display_name=name.replace("_", " ").title(),
        description=description,
        virtual_path=f"/skills/org/org_456/user/user_123/{name}/SKILL.md",
        scope="user",
        source_type="user",
        version=1,
        metadata=metadata or {},
    )


@dataclass
class FakeSkillRegistry:
    """A registry whose cards and bundles are fixed by the test."""

    cards: Sequence[VirtualSkillCard] = ()
    loaded: list[str] = field(default_factory=list)

    async def list_available_skills(
        self, _context: object
    ) -> tuple[VirtualSkillCard, ...]:
        return tuple(self.cards)

    async def load_skill_by_name(self, name: str) -> VirtualSkillBundle:
        self.loaded.append(name)
        match = next(entry for entry in self.cards if entry.name == name)
        return VirtualSkillBundle(
            skill_id=match.skill_id,
            name=match.name,
            display_name=match.display_name,
            description=match.description,
            markdown=f"# {match.display_name}\n\nStep 1. Do the {name} thing.",
            virtual_path=match.virtual_path,
            version=match.version,
        )


async def assemble_prompt(
    *,
    context: AgentRuntimeContext,
    dependencies: RuntimeDependencies,
    cards: Sequence[VirtualSkillCard],
    connectors: Sequence[object] = (),
) -> tuple[str, CapturingAgentBuilder]:
    """Build a real runtime harness and return the prompt the builder received."""
    builder = CapturingAgentBuilder()
    await acreate_agent_runtime(
        context=context,
        dependencies=dependencies.model_copy(
            update={
                "skill_registry": FakeSkillRegistry(cards=cards),
                "mcp_registry": FakeMcpRegistry(servers=tuple(connectors)),
            }
        ),
        agent_builder=builder,
    )
    return builder.calls[0].system_prompt, builder


@pytest.fixture
def bound_ledger() -> SkillUsageLedger:
    """Bind a run-scoped usage ledger exactly as the worker's run handler does."""
    ledger = SkillUsageLedger(run_id="run_123")
    token = SkillUsageLedger.bind_for_run(ledger)
    try:
        yield ledger
    finally:
        SkillUsageLedger.unbind(token)


class TestConditionalVisibilityInTheAssembledPrompt:
    """A Skill's declared conditions decide whether the prompt names it."""

    async def test_unmet_condition_keeps_the_skill_out_of_the_prompt(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        prompt, _ = await assemble_prompt(
            context=runtime_context_admin,
            dependencies=fake_dependencies,
            cards=(
                card(
                    Names.LINEAR_TRIAGE,
                    metadata={"requires_connectors": Connectors.LINEAR},
                ),
                card(Names.LAUNCH_REVIEW),
            ),
            connectors=(),
        )

        assert Names.LINEAR_TRIAGE not in prompt
        assert Names.LAUNCH_REVIEW in prompt

    async def test_met_condition_puts_the_skill_in_the_prompt(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        prompt, _ = await assemble_prompt(
            context=runtime_context_admin,
            dependencies=fake_dependencies,
            cards=(
                card(
                    Names.LINEAR_TRIAGE,
                    metadata={"requires_connectors": Connectors.LINEAR},
                ),
                card(Names.LAUNCH_REVIEW),
            ),
            connectors=(StubConnectorCard(name=Connectors.LINEAR),),
        )

        assert Names.LINEAR_TRIAGE in prompt
        assert Names.LAUNCH_REVIEW in prompt

    async def test_fallback_skill_is_hidden_while_its_primary_tool_exists(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """``fallback_for_tools`` hides the Skill when the better tool is present.

        ``load_skill`` is registered by the factory for any run with a skill
        registry, so it is a tool name this run is guaranteed to have — the test
        needs no assumption about how tool names are spelled.
        """

        prompt, _ = await assemble_prompt(
            context=runtime_context_admin,
            dependencies=fake_dependencies,
            cards=(
                card(
                    Names.MANUAL_SEARCH,
                    metadata={"fallback_for_tools": "load_skill"},
                ),
                card(Names.LAUNCH_REVIEW),
            ),
        )

        assert Names.MANUAL_SEARCH not in prompt
        assert Names.LAUNCH_REVIEW in prompt

    async def test_a_skill_declaring_nothing_is_unconditional(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        prompt, _ = await assemble_prompt(
            context=runtime_context_admin,
            dependencies=fake_dependencies,
            cards=(card(Names.LAUNCH_REVIEW),),
        )

        assert Names.LAUNCH_REVIEW in prompt

    def test_conditions_survive_both_wire_forms(self) -> None:
        """Frontmatter metadata is scalar-only, so a list arrives as a string."""
        scalar = SkillVisibilityConditions.from_metadata(
            {"requires_tools": "alpha, beta"}
        )
        sequence = SkillVisibilityConditions.from_metadata(
            {"requires_tools": ["alpha", "beta"]}
        )

        assert scalar.requires_tools == ("alpha", "beta")
        assert sequence.requires_tools == ("alpha", "beta")

    def test_an_unresolved_context_filters_nothing(self) -> None:
        conditions = SkillVisibilityConditions(requires_connectors=(Connectors.LINEAR,))

        assert conditions.is_satisfied_by(SkillVisibilityContext.unresolved()) is True
        assert conditions.is_satisfied_by(SkillVisibilityContext.of()) is False


class TestTheIndexStaysUnderItsBound:
    """The prompt index is a tier, not the library."""

    async def test_index_is_capped_as_the_library_grows(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        cards = tuple(card(f"skill_{index:03d}") for index in range(200))

        prompt, _ = await assemble_prompt(
            context=runtime_context_admin,
            dependencies=fake_dependencies,
            cards=cards,
        )

        rendered = [entry.name for entry in cards if entry.name in prompt]
        assert len(rendered) == Limits.SKILL_INDEX_MAX_ENTRIES
        assert "skill_199" not in prompt
        assert "further Skills match this run" in prompt

    async def test_a_larger_library_does_not_grow_the_prompt(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """The point of the bound: 200 Skills must not cost more than 40."""
        small, _ = await assemble_prompt(
            context=runtime_context_admin,
            dependencies=fake_dependencies,
            cards=tuple(card(f"skill_{index:03d}") for index in range(40)),
        )
        large, _ = await assemble_prompt(
            context=runtime_context_admin,
            dependencies=fake_dependencies,
            cards=tuple(card(f"skill_{index:03d}") for index in range(200)),
        )

        # A 5x library may only add the extra digits of the deferred count in
        # the footer — the growth is O(log N), not the O(N) tax this replaces.
        assert abs(len(large) - len(small)) <= 4

    def test_rows_respect_the_character_budget_before_the_entry_count(self) -> None:
        plan = SkillIndexPlanner.plan(
            cards=tuple(
                card(f"skill_{index:03d}", description="x" * 200) for index in range(20)
            ),
            max_entries=Limits.SKILL_INDEX_MAX_ENTRIES,
            max_chars=400,
        )

        entry_rows = plan.rows[: len(plan.surfaced)]
        assert sum(len(row) + 1 for row in entry_rows) <= 400
        assert 0 < len(plan.surfaced) < 20
        assert plan.deferred

    def test_a_long_description_is_clipped_not_dropped(self) -> None:
        row = SkillIndexPlanner.row(card(Names.LAUNCH_REVIEW, description="y" * 500))

        assert Names.LAUNCH_REVIEW in row
        assert len(row) < 500


class TestOnDemandFetch:
    """The compact entry is a pointer; the body is fetched when asked for."""

    async def test_load_skill_returns_the_full_body(self) -> None:
        registry = FakeSkillRegistry(cards=(card(Names.LAUNCH_REVIEW),))
        tool = LoadSkillTool(registry=registry)  # type: ignore[arg-type]

        result = await tool.ainvoke(LoadSkillInput(skill_name=Names.LAUNCH_REVIEW))

        assert result["ok"] is True
        assert "Step 1. Do the launch_risk_review thing." in result["markdown"]

    async def test_full_body_is_absent_from_the_prompt(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        prompt, builder = await assemble_prompt(
            context=runtime_context_admin,
            dependencies=fake_dependencies,
            cards=(card(Names.LAUNCH_REVIEW),),
        )

        assert Names.LAUNCH_REVIEW in prompt
        assert "Step 1. Do the" not in prompt
        tool_names = {getattr(tool, "name", "") for tool in builder.calls[0].tools}
        assert {"load_skill", "list_skills"} <= tool_names

    async def test_list_skills_reaches_the_deferred_tail(
        self, bound_ledger: SkillUsageLedger
    ) -> None:
        """A Skill the index bound cut is still reachable, by search."""
        bound_ledger.offer(
            surfaced=(Names.LAUNCH_REVIEW,), deferred=(Names.MANUAL_SEARCH,)
        )
        registry = FakeSkillRegistry(
            cards=(card(Names.LAUNCH_REVIEW), card(Names.MANUAL_SEARCH))
        )
        tool = ListSkillsTool(
            registry=registry,  # type: ignore[arg-type]
            runtime_context=None,  # type: ignore[arg-type]
        )

        result = await tool.ainvoke(ListSkillsInput(query="manual"))

        assert [row["name"] for row in result["skills"]] == [Names.MANUAL_SEARCH]

    async def test_list_skills_never_reveals_a_condition_hidden_skill(
        self, bound_ledger: SkillUsageLedger
    ) -> None:
        bound_ledger.offer(
            surfaced=(Names.LAUNCH_REVIEW,), hidden=(Names.LINEAR_TRIAGE,)
        )
        registry = FakeSkillRegistry(
            cards=(card(Names.LAUNCH_REVIEW), card(Names.LINEAR_TRIAGE))
        )
        tool = ListSkillsTool(
            registry=registry,  # type: ignore[arg-type]
            runtime_context=None,  # type: ignore[arg-type]
        )

        result = await tool.ainvoke(ListSkillsInput())

        assert [row["name"] for row in result["skills"]] == [Names.LAUNCH_REVIEW]


class TestUsageSidecar:
    """Surfaced-and-used has to be distinguishable from surfaced-and-unused."""

    async def test_prompt_assembly_records_the_offer_on_the_bound_ledger(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
        bound_ledger: SkillUsageLedger,
    ) -> None:
        await assemble_prompt(
            context=runtime_context_admin,
            dependencies=fake_dependencies,
            cards=(
                card(Names.LAUNCH_REVIEW),
                card(
                    Names.LINEAR_TRIAGE,
                    metadata={"requires_connectors": Connectors.LINEAR},
                ),
            ),
        )

        snapshot = bound_ledger.snapshot()
        assert snapshot.surfaced == (Names.LAUNCH_REVIEW,)
        assert snapshot.hidden == (Names.LINEAR_TRIAGE,)

    async def test_a_used_skill_is_distinguishable_from_an_unused_one(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
        bound_ledger: SkillUsageLedger,
    ) -> None:
        cards = (card(Names.LAUNCH_REVIEW), card(Names.MANUAL_SEARCH))
        await assemble_prompt(
            context=runtime_context_admin,
            dependencies=fake_dependencies,
            cards=cards,
        )
        await LoadSkillTool(registry=FakeSkillRegistry(cards=cards)).ainvoke(  # type: ignore[arg-type]
            LoadSkillInput(skill_name=Names.LAUNCH_REVIEW)
        )

        snapshot = bound_ledger.snapshot()
        assert snapshot.used == (Names.LAUNCH_REVIEW,)
        assert snapshot.surfaced_unused == (Names.MANUAL_SEARCH,)

    async def test_a_failed_load_is_not_a_use(
        self, bound_ledger: SkillUsageLedger
    ) -> None:
        registry = FakeSkillRegistry(cards=(card(Names.LAUNCH_REVIEW),))
        bound_ledger.offer(surfaced=(Names.LAUNCH_REVIEW,))

        result = await LoadSkillTool(registry=registry).ainvoke(  # type: ignore[arg-type]
            {"skill_name": ""}
        )

        assert result["ok"] is False
        assert bound_ledger.snapshot().used == ()

    async def test_recording_is_a_no_op_without_a_bound_ledger(self) -> None:
        """Replay, evals and unit tests bind nothing and must still work."""
        registry = FakeSkillRegistry(cards=(card(Names.LAUNCH_REVIEW),))

        SkillUsageLedger.record_offer(surfaced=(Names.LAUNCH_REVIEW,))
        result = await LoadSkillTool(registry=registry).ainvoke(  # type: ignore[arg-type]
            LoadSkillInput(skill_name=Names.LAUNCH_REVIEW)
        )

        assert result["ok"] is True
        assert SkillUsageLedger.active() is None
