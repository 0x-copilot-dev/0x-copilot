"""Reviewed graph-level tool-surface contracts."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
import logging
from typing import TYPE_CHECKING


if TYPE_CHECKING:  # pragma: no cover - typing only
    from agent_runtime.observability.context_origin import (
        ContextLifecycle,
        ContextOrigin,
    )


_LOGGER = logging.getLogger(__name__)


#: Deep Agents tools withheld from the model surface.
#:
#: `write_file` / `edit_file` USED to be here, and their absence is why no run
#: in this program ever recorded a write: the permission rules, the floor, the
#: staged lane and the approval card all governed a tool the model was never
#: given. Evidence was in every journey — `write_attempted: false`, every time,
#: whatever the supposed cause — and the model said so plainly: "I don't have a
#: `write_file` tool available in this session."
#:
#: They were withheld when host writes were meant to route exclusively through
#: the staged C3 → ledger → C2 lane. That lane has never run on a desktop
#: install (it needs an attestation only a signed packaged build can produce),
#: so the practical effect was that the agent could not write anywhere at all —
#: not even into its own scratch.
#:
#: What now bounds a write is the RULE SET, which is where the decision belongs:
#: allow inside a folder the user attached read-write, deny in a read-only one,
#: deny everywhere else, and `HostFilesystemFloor` for the dotted paths globs
#: cannot see.
#:
#: `execute` STAYS. It is shell, a materially larger grant than editing files in
#: a folder the user chose, and it is a separate decision that has not been made.
DEEP_AGENT_PROFILE_EXCLUDED_TOOL_NAMES = frozenset({"execute"})


class ModelToolOwner(StrEnum):
    """Who owns the schema text of a tool composed onto the model surface.

    Owners are dotted module namespaces so a label is self-describing: reading
    ``agent_runtime.capabilities.backends:publish_artifact`` in an occupancy
    report tells you which package to open to trim the 650 tokens it costs on
    every model call. That is the whole point of §4.1's owner-namespaced labels
    over a flat central enum of tool names.

    ``DEEP_AGENTS_MIDDLEWARE`` is the marker for tools this repository does not
    author. It is deliberately coarse for now: PRD-06 lands the pinned
    third-party adapter that resolves library-installed tools through the live
    ``HarnessProfile``, and refines what is currently one bucket.

    ``WORKSPACE`` has no append site in ``_model_visible_tools`` and that is the
    correct state, not an omission. The ``/workspace/`` surface reaches the model
    as *routes* under the ``deepagents`` filesystem middleware's own
    ``ls``/``read``/``glob``/``grep``/``write``/``edit`` tools — this repository
    owns where those tools read and write, not the schema text that occupies the
    window — so their tool-block bytes are third-party and belong to PRD-06's
    adapter. The owner is declared here because it is the namespace PRD-07's
    message-side ``binary_b64`` declaration lands under, and because leaving it
    out would invite the next author to invent a second spelling of it.
    """

    MCP = "agent_runtime.capabilities.mcp"
    SKILLS = "agent_runtime.capabilities.skills"
    TOOLS = "agent_runtime.capabilities.tools"
    DISCOVERY = "agent_runtime.capabilities.discovery"
    INTERPRETER = "agent_runtime.capabilities.interpreter"
    SANDBOX = "agent_runtime.capabilities.sandbox"
    DATAFLOW = "agent_runtime.capabilities.dataflow"
    BACKENDS = "agent_runtime.capabilities.backends"
    WORKSPACE = "agent_runtime.capabilities.workspace"
    DEEP_AGENTS_MIDDLEWARE = "deepagents.middleware"


class ModelToolDeclaration:
    """Declare what a composed tool contributes to the model's context window.

    Used at each append site in ``factory._model_visible_tools``, wrapping the
    tool it declares so the declaration is *lexically adjacent* to the
    composition:

    .. code-block:: python

        model_tools.append(
            ModelToolDeclaration.declared(
                _structured_tool(AskAQuestionTool(...), AskAQuestionInput),
                owner=ModelToolOwner.TOOLS,
            )
        )

    Adjacency is not a style choice. The PRD-02 AST conformance gate sweeps
    ``_model_visible_tools`` for tools appended to ``model_tools`` without a
    covering declaration, so a lookup table keyed by tool name — the obvious
    alternative — would defeat the gate: a new tool would compose fine and
    silently measure as ``UNDECLARED`` forever. Declaring at the site is what
    makes "adding a tool" and "accepting its context cost" the same edit.

    Every method is fail-open. Composing the model surface is on the run path
    and declaring an origin is observability; a malformed owner or a tool that
    refuses attribute assignment costs a label in a report, never a run (§6.4).

    The observability import is deferred into the call rather than taken at
    module scope: ``agent_runtime.execution.__init__`` eagerly imports the
    factory, so a module-scope edge from ``execution`` into the context-origin
    module would make importing either package order-dependent.
    """

    @classmethod
    def declared(
        cls,
        tool: object,
        *,
        owner: ModelToolOwner,
        name: str | None = None,
        third_party: bool = False,
        lifecycle: ContextLifecycle | None = None,
    ) -> object:
        """Attach a tool-block origin to ``tool`` and return it for appending.

        ``name`` defaults to the tool's own model-facing name, which keeps the
        label and the thing the model calls the same string — a report that
        named tools differently from the transcript would be an extra
        translation step for every reader.

        ``lifecycle`` defaults to ``RESIDENT`` because tool-block text is re-sent
        on every model call until the surface itself changes — which is true of
        every tool composed here, gated or not. A future contributor with a
        genuinely different cadence passes it explicitly.

        An existing declaration wins. A tool that arrives already declared was
        declared by the code that *authored* it, which knows more than this
        composition site does; overwriting it here would let the factory silently
        relabel a contributor as its own.
        """

        origin = cls._origin(
            tool,
            owner=owner,
            name=name,
            third_party=third_party,
            lifecycle=lifecycle,
        )
        if origin is None:
            return tool
        from agent_runtime.observability.context_origin import (  # noqa: PLC0415
            declare_context_origin,
        )

        return declare_context_origin(tool, origin)

    @classmethod
    def declared_all(
        cls,
        tools: Iterable[object],
        *,
        owner: ModelToolOwner,
        third_party: bool = False,
    ) -> tuple[object, ...]:
        """Declare a homogeneous group of tools that share one owner.

        Used for the injected registry tools and the F3 bridge registrations,
        where the count is decided elsewhere and only the owner is known here.
        Each tool still gets its *own* label off its own name, so a group
        declaration is a shorthand for the owner, never a roll-up that would
        cost the report its per-tool granularity.
        """

        return tuple(
            cls.declared(tool, owner=owner, third_party=third_party) for tool in tools
        )

    @classmethod
    def _origin(
        cls,
        tool: object,
        *,
        owner: ModelToolOwner,
        name: str | None,
        third_party: bool,
        lifecycle: ContextLifecycle | None,
    ) -> ContextOrigin | None:
        """Build the declaration for ``tool``, or ``None`` to leave it alone.

        A tool whose name is empty or unreadable yields ``None`` rather than a
        half-formed label: ``ContextOrigin`` rejects an empty ``name``, and an
        origin that cannot be spelled correctly is better recorded as
        ``UNDECLARED`` — which is visible in ``undeclared_tokens`` — than as a
        label nobody can trace back to a tool.
        """

        try:
            from agent_runtime.observability.context_origin import (  # noqa: PLC0415
                ContextLifecycle,
                ContextOrigin,
                ContextSegmentClass,
                context_origin_of,
            )

            if context_origin_of(tool) is not None:
                return None
            resolved_name = (name or str(getattr(tool, "name", ""))).strip()
            return ContextOrigin(
                owner=str(owner),
                name=resolved_name,
                segment_class=ContextSegmentClass.TOOLS,
                lifecycle=lifecycle or ContextLifecycle.RESIDENT,
                third_party=third_party,
            )
        except Exception:  # noqa: BLE001 — a label is never worth a failed run
            _LOGGER.debug(
                "Could not build a context origin for a composed tool under "
                "owner %s; it will measure as UNDECLARED.",
                owner,
                exc_info=True,
            )
            return None


__all__ = (
    "DEEP_AGENT_PROFILE_EXCLUDED_TOOL_NAMES",
    "ModelToolDeclaration",
    "ModelToolOwner",
)
