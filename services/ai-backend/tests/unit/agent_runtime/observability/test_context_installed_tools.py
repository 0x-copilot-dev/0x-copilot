"""Declarations for tools installed by middleware rather than by the factory.

The population this guards is defined by what the PRD-02 AST gate *cannot* see.
That gate sweeps ``factory._model_visible_tools``; a middleware carrying its own
``tools`` list never appears there, so those tools had no composition site to
declare at, measured as ``UNDECLARED`` on every model call, and did so with the
conformance gate green. On a real run one of them — ``write_todos`` — was 997
estimated tokens of anonymous occupancy.

Three contracts, in the order they matter:

1. **The inventory is resolved, not restated.** Each row names a module and a
   symbol; the *library* answers with the names. A test that hard-coded the
   expected names would pass just as happily against an inventory that had
   quietly stopped resolving, so the assertions here check both the resolution
   and the pinned result.
2. **A stamp always wins.** The fallback is consulted only for a tool carrying
   no declaration, because the code that composed a tool knows more about it
   than a name-keyed inventory does.
3. **It cannot make attribution worse.** Every failure — an unimportable
   module, a symbol that moved, a callable that raises, a value that is not an
   origin — degrades to the ``UNDECLARED`` behaviour that was there before.
"""

from __future__ import annotations

from typing import Final

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from agent_runtime.observability.context_installed_tools import (
    InstalledToolOrigins,
    InstalledToolSource,
)
from agent_runtime.observability.context_origin import (
    UNDECLARED_CONTEXT_LABEL,
    ContextLifecycle,
    ContextOrigin,
    ContextSegmentClass,
    declare_context_origin,
)
from agent_runtime.observability.context_tool_ledger import ToolSchemaLedger


class TodoArgs(BaseModel):
    """Stand-in schema for a middleware-installed tool."""

    todos: str = Field(description="The updated todo list.")


class InstalledToolMixin:
    """Fixtures shared by every group below."""

    # The names the pinned sources are expected to resolve to on the installed
    # dependency set. Pinned as a *golden fixture*, not as the implementation:
    # a library bump that adds or removes a built-in tool is meant to fail here
    # with the tool named, so a reviewer decides whether the new resident cost
    # is acceptable rather than discovering it in a bill.
    EXPECTED_TOOL_NAMES: Final[frozenset[str]] = frozenset(
        {
            "delete",
            "edit_file",
            "execute",
            "glob",
            "grep",
            "ls",
            "read_file",
            "task",
            "write_file",
            "write_todos",
        }
    )

    STAMPED_ORIGIN: Final[ContextOrigin] = ContextOrigin(
        owner="agent_runtime.capabilities.tools",
        name="write_todos",
        segment_class=ContextSegmentClass.TOOLS,
        lifecycle=ContextLifecycle.RESIDENT,
    )

    def tool(self, *, name: str) -> StructuredTool:
        def implementation(todos: str) -> str:
            return todos

        return StructuredTool.from_function(
            implementation,
            name=name,
            description="Maintain the working todo list.",
            args_schema=TodoArgs,
        )


class TestTheInventoryResolves(InstalledToolMixin):
    def test_every_pinned_source_resolves_against_the_installed_libraries(
        self,
    ) -> None:
        for source in InstalledToolOrigins.SOURCES:
            assert source.resolve(), f"{source.qualified_name} resolved to nothing"

    def test_the_resolved_names_are_the_pinned_inventory(self) -> None:
        assert set(InstalledToolOrigins().inventory()) == self.EXPECTED_TOOL_NAMES

    def test_library_tools_are_attributed_to_the_library_that_owns_them(self) -> None:
        inventory = InstalledToolOrigins().inventory()

        assert (
            inventory["write_todos"] == "langchain.agents.middleware.todo:write_todos"
        )
        assert inventory["ls"] == "deepagents.middleware.filesystem:ls"

    def test_a_tool_we_author_is_not_marked_third_party(self) -> None:
        # ``task`` is installed by ``deepagents``' subagent middleware but built
        # by this repository, so it is fixed by editing our source rather than
        # by a profile exclusion — which is exactly what ``third_party`` tells a
        # reader.
        origins = InstalledToolOrigins()

        ours = origins.origin_for("task")
        theirs = origins.origin_for("write_todos")

        assert ours is not None and ours.third_party is False
        assert ours.owner == "agent_runtime.delegation.subagents"
        assert theirs is not None and theirs.third_party is True

    def test_installed_tools_are_declared_as_resident_tool_block_text(self) -> None:
        origin = InstalledToolOrigins().origin_for("write_todos")

        assert origin is not None
        assert origin.segment_class is ContextSegmentClass.TOOLS
        assert origin.lifecycle is ContextLifecycle.RESIDENT

    def test_an_unknown_tool_name_is_still_undeclared(self) -> None:
        assert InstalledToolOrigins().origin_for("publish_artifact") is None
        assert InstalledToolOrigins().origin_for("") is None


class TestResolutionIsTotal(InstalledToolMixin):
    def test_a_module_that_does_not_import_declares_nothing(self) -> None:
        source = InstalledToolSource(
            module="deepagents.middleware.a_module_that_moved",
            symbol="_ALL_FS_TOOL_NAMES",
            owner="deepagents.middleware.filesystem",
        )

        assert source.resolve() == ()

    def test_a_symbol_that_moved_declares_nothing(self) -> None:
        source = InstalledToolSource(
            module="deepagents.middleware.filesystem",
            symbol="_A_SYMBOL_THAT_MOVED",
            owner="deepagents.middleware.filesystem",
        )

        assert source.resolve() == ()

    def test_an_owner_that_is_not_a_dotted_identifier_drops_that_row(self) -> None:
        origins = InstalledToolOrigins(
            sources=(
                InstalledToolSource(
                    module="deepagents.middleware.filesystem",
                    symbol="_ALL_FS_TOOL_NAMES",
                    owner="not a module path",
                ),
            )
        )

        assert dict(origins.inventory()) == {}

    def test_the_disabled_inventory_declares_nothing(self) -> None:
        assert InstalledToolOrigins.disabled().origin_for("write_todos") is None


class TestTheLedgerConsultsTheInventory(InstalledToolMixin):
    def test_an_installed_tool_reports_a_real_label_instead_of_undeclared(
        self,
    ) -> None:
        origins = InstalledToolOrigins()

        (footprint,) = ToolSchemaLedger.measure(
            [self.tool(name="write_todos")],
            origin_fallback=origins.origin_for,
        )

        assert footprint.label == "langchain.agents.middleware.todo:write_todos"
        assert footprint.declared is True
        assert footprint.third_party is True
        assert footprint.estimated_tokens > 0

    def test_without_the_fallback_the_same_tool_is_undeclared(self) -> None:
        # The control for the assertion above: nothing about the tool changed,
        # only whether a declaration was made on its behalf.
        (footprint,) = ToolSchemaLedger.measure([self.tool(name="write_todos")])

        assert footprint.label == UNDECLARED_CONTEXT_LABEL
        assert footprint.declared is False

    def test_a_stamped_tool_keeps_its_own_declaration(self) -> None:
        stamped = self.tool(name="write_todos")
        declare_context_origin(stamped, self.STAMPED_ORIGIN)

        (footprint,) = ToolSchemaLedger.measure(
            [stamped],
            origin_fallback=InstalledToolOrigins().origin_for,
        )

        assert footprint.label == self.STAMPED_ORIGIN.label

    def test_a_fallback_that_raises_costs_only_the_label(self) -> None:
        def explode(tool_name: str) -> ContextOrigin | None:
            raise RuntimeError(tool_name)

        (footprint,) = ToolSchemaLedger.measure(
            [self.tool(name="write_todos")],
            origin_fallback=explode,
        )

        assert footprint.label == UNDECLARED_CONTEXT_LABEL
        assert footprint.estimated_tokens > 0

    def test_a_fallback_returning_a_non_origin_is_ignored(self) -> None:
        (footprint,) = ToolSchemaLedger.measure(
            [self.tool(name="write_todos")],
            origin_fallback=lambda name: name,  # type: ignore[arg-type,return-value]
        )

        assert footprint.label == UNDECLARED_CONTEXT_LABEL
