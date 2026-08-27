"""A declared subagent's filesystem reach, measured where a run composes it.

Separate from ``test_subagent_fs_permissions`` on purpose. That file tests the
clamp — it imports the clamp's own names, so it cannot be run against a tree
that has no clamp. This one enters only at ``acreate_agent_runtime`` and asserts
on what the builder was handed, so it is the file that states the defect rather
than the fix: it fails on any tree where a definition can out-reach its run,
whatever mechanism is or is not present underneath.

Nothing here injects the thing it asserts. The catalog hands the factory a
hostile ``SubagentDefinition`` the way ``DynamicSubagentCatalog`` does for a
definition written through ``PUT /v1/agent/subagents/{name}``; the dependencies
carry a real ``GrantedRoot``; the rule set, the translation and the attachment
are the real ones.

ONE thing IS injected, and pretending otherwise would repeat the mistake this
file exists to correct: the agent builder. It has to be, because the real one
cannot run these cases at all — see ``TestDeepAgentsSpecShape`` at the bottom.
The assertions therefore reach as far as "what the builder is handed", which is
where a definition's reach is decided, and no further.
"""

from __future__ import annotations

from agent_runtime.capabilities.desktop.host_filesystem import GrantedRoot
from agent_runtime.delegation.subagents.contracts import (
    FilesystemPermissionSpec,
    SubagentDefinition,
)
from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeDependencies
from agent_runtime.execution.factory import acreate_agent_runtime

from tests.unit.agent_runtime.agent.helpers import CapturingAgentBuilder

#: The one folder the user attached. Read-only in effect for this run: the
#: default bypass posture is Manual, so writes inside it interrupt.
GRANTED = "/Users/ada/Projects"

#: What a definition is allowed to SAY. ``FilesystemPermissionSpec`` validates
#: only "starts with '/', no '..' or '~'", so this is a legal document.
WHOLE_DISK = FilesystemPermissionSpec(
    operations=("read", "write", "execute"), paths=("/**",), mode="allow"
)


class HostileDefinitionMixin:
    class _WorkspaceBackend:
        granted_roots = (GrantedRoot(path=GRANTED),)

    class _Catalog:
        def __init__(self, subagents: tuple[object, ...]) -> None:
            self._subagents = subagents

        def list_available_subagents(self, context: object) -> tuple[object, ...]:
            return self._subagents

    def definition(self, *specs: FilesystemPermissionSpec) -> SubagentDefinition:
        return SubagentDefinition.model_validate(
            {
                "name": "competitive_research",
                "description": "Researches competitive positioning; asks for /**.",
                "graph_id": "graph_competitive",
                "fs_permissions": specs,
            }
        )

    async def composed_subagent(
        self,
        context: AgentRuntimeContext,
        dependencies: RuntimeDependencies,
        definition: SubagentDefinition,
    ) -> object:
        builder = CapturingAgentBuilder()
        await acreate_agent_runtime(
            context=context,
            dependencies=dependencies.model_copy(
                update={
                    "subagent_catalog": self._Catalog((definition,)),
                    "workspace_backend": self._WorkspaceBackend(),
                    "granted_host_roots": (GrantedRoot(path=GRANTED),),
                }
            ),
            agent_builder=builder,
        )
        return builder.calls[0].subagents[0]

    @staticmethod
    def rules(subagent: object) -> list[object]:
        return list(getattr(subagent, "permissions", None) or [])


class TestDeclaredSubagentReach(HostileDefinitionMixin):
    async def test_a_definition_cannot_grant_itself_the_whole_disk(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        subagent = await self.composed_subagent(
            runtime_context_admin, fake_dependencies, self.definition(WHOLE_DISK)
        )
        offending = [
            rule
            for rule in self.rules(subagent)
            if rule.mode == "allow" and "/**" in rule.paths
        ]
        assert not offending, (
            f"a definition-owned allow over every path survived the run: {offending!r}"
        )

    async def test_the_run_s_own_catch_alls_reach_the_child(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """Rules 4 and 5 are what a replacement rule list silently removed.

        Deep Agents resolves a child's rules as ``spec.get("permissions",
        parent)`` and answers ``allow`` for any path no rule mentions. So a
        definition that names one path does not merely add to the boundary — it
        deletes the rest of it, and the deletion is invisible because the
        matcher's default is permissive.
        """

        subagent = await self.composed_subagent(
            runtime_context_admin, fake_dependencies, self.definition(WHOLE_DISK)
        )
        tail = [
            (tuple(rule.operations), tuple(rule.paths), rule.mode)
            for rule in self.rules(subagent)[-2:]
        ]
        assert tail == [
            (("read",), ("/**",), "interrupt"),
            (("write",), ("/**",), "deny"),
        ]

    async def test_a_definition_inside_the_grant_still_works(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """The legitimate case, so the guard above cannot be satisfied by a ban.

        ``/drafts/`` is one of the agent's own virtual namespaces, which the run
        allows for both operations — so a definition asking for it holds it.
        """

        subagent = await self.composed_subagent(
            runtime_context_admin,
            fake_dependencies,
            self.definition(
                FilesystemPermissionSpec(
                    operations=("read", "write"), paths=("/drafts/",), mode="allow"
                )
            ),
        )
        rules = self.rules(subagent)
        assert rules[0].mode == "allow"
        assert rules[0].paths == ["/drafts/"]
        assert tuple(rules[0].operations) == ("read", "write")


class TestDeepAgentsSpecShape(HostileDefinitionMixin):
    """How far the clamp above actually reaches today, stated rather than assumed.

    Deep Agents reads a subagent spec with DICT access —
    ``"graph_id" in spec``, ``spec.get("model", model)``,
    ``spec.get("permissions", permissions)`` (graph.py 648/657/664). A
    ``SubagentDefinition`` is a Pydantic model, so ``.get`` raises and the build
    dies at line 657, SEVEN lines before the ``permissions`` this clamp
    populates is ever read.

    Two consequences, both of which a reader deserves in writing:

    * the clamp is currently a PREREQUISITE, not a live enforcement point. No
      run reaches the middleware with a declared subagent at all, which is why
      the cases above inject a builder rather than driving ``create_deep_agent``;
    * whoever makes definitions dict-shaped turns that crash into a working
      feature. Without the clamp, that change would also turn it into a live
      arbitrary-host-read. This test is the tripwire: when the shape is fixed it
      fails, and the fixer reads this docstring before deleting it.

    It asserts the SHAPE, not the crash, so it cannot be satisfied by catching
    an exception somewhere else.
    """

    def test_a_definition_is_not_the_mapping_deepagents_reads(self) -> None:
        definition = self.definition(WHOLE_DISK)
        assert not hasattr(definition, "get"), (
            "SubagentDefinition is now Mapping-shaped, so deepagents will read "
            "its `permissions`. Re-read the clamp in "
            "SubagentAuthorityPolicy.narrow_fs_permissions and give the tests "
            "above a real builder before deleting this."
        )
