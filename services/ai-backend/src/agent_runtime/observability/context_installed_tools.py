"""Declarations for tools that reach the model without an append site (§4.3).

The PRD-02 AST conformance gate sweeps ``factory._model_visible_tools`` and
fails a tool appended there with no lexically adjacent declaration. That gate is
sound for the surface it sweeps and **structurally blind** to everything else:
a middleware that carries its own ``tools`` list installs a tool onto the model
surface without ever touching the factory's append list, so there is no append
for the gate to look at and no composition site for a contributor to declare at.
Those tools measured as ``UNDECLARED`` forever, and the gate stayed green while
they did — which is the worst combination a conformance check can have.

The population is not hypothetical and not small. On a real run the tool block
was 9,759 estimated tokens with several spans unattributed, the largest of them
997 — ``write_todos``, whose 970-token description this repository does not
author and never appends.

**Enumeration, not inference.** Attributing by "which package defines the
function behind this tool" would be a guess dressed as a measurement: a tool is
a ``StructuredTool`` built from a closure, its module of origin is whatever
happened to build it, and our own ``task`` tool is monkey-patched *into*
``deepagents`` and would infer as third-party. So each source below names the
module and the symbol to read, and the *library* answers with the names. A
dependency that adds a built-in tool changes the resolved set, the golden
fixture in ``tests/unit/agent_runtime/observability`` diffs, and a reviewer
decides — the same contract :mod:`context_third_party` holds for prompt text.

**Two populations, not one, and the difference is actionable.** A library tool
is fixed by a profile exclusion or a dependency change; one of ours that happens
to be installed through library machinery is fixed by editing this repository.
``third_party`` carries that distinction, which is why ``task`` — ours, since
``install_atlas_task_tool`` replaces the library's builder — is declared under
``agent_runtime.delegation.subagents`` rather than under ``deepagents``.

Nothing here raises. Every resolution is reflective and every failure degrades
to a missing row, because this is read on the model-call path and §6.4 forbids
an observability concern failing a run.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import importlib
import logging
from types import MappingProxyType
from typing import Annotated, ClassVar, Final

from pydantic import Field

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.observability.context_origin import (
    ContextLifecycle,
    ContextOrigin,
    ContextSegmentClass,
)


_LOGGER = logging.getLogger(__name__)


class InstalledToolSource(RuntimeContract):
    """One place a middleware publishes the names of the tools it installs.

    ``module`` and ``symbol`` are the pinned pair; ``owner`` is the declaration
    the resolved names are attributed to. Keeping ``owner`` separate from
    ``module`` is what lets ``task`` be read out of ``deepagents.middleware.
    subagents`` — where it is installed — while being attributed to the package
    that actually authors it.

    ``symbol`` is resolved permissively because libraries publish their names in
    whichever shape suited them: a bare ``str``, a collection of ``str`` (the
    filesystem middleware's ``_ALL_FS_TOOL_NAMES``), or a live tool object with
    a ``name`` attribute (langchain's ``write_todos``). Supporting the three
    shapes is what makes this an enumeration of what the library says rather
    than a restatement of what we believe it says.
    """

    module: Annotated[str, Field(min_length=1, max_length=300)]
    symbol: Annotated[str, Field(min_length=1, max_length=200)]
    owner: Annotated[str, Field(min_length=1, max_length=200)]
    third_party: bool = True

    NAME_ATTRIBUTE: ClassVar[str] = "name"

    @property
    def qualified_name(self) -> str:
        """``module:symbol`` — the key the golden fixture is pinned on."""

        return f"{self.module}:{self.symbol}"

    def resolve(self) -> tuple[str, ...]:
        """Return the tool names this source publishes, or ``()`` on any failure.

        Sorted so the result is a stable fixture rather than a reflection of the
        iteration order of whatever collection the library happened to use.
        """

        try:
            module = importlib.import_module(self.module)
            published = getattr(module, self.symbol, None)
        except Exception:  # noqa: BLE001 — an unresolvable source is simply absent
            _LOGGER.debug(
                "Installed-tool source %s did not resolve; its tools will "
                "measure as UNDECLARED.",
                self.qualified_name,
            )
            return ()
        return tuple(sorted(self._names(published)))

    @classmethod
    def _names(cls, published: object) -> set[str]:
        """Read tool names out of the three shapes a library may publish."""

        if isinstance(published, str):
            return {published} if published else set()
        attribute = getattr(published, cls.NAME_ATTRIBUTE, None)
        if isinstance(attribute, str) and attribute:
            return {attribute}
        if isinstance(published, Iterable):
            return {item for item in published if isinstance(item, str) and item}
        return set()


class InstalledToolOrigins:
    """Declare, on their behalf, the tools middleware installs (§4.3).

    Built once and memoized: resolution imports dependency modules, which has no
    business happening inside a per-model-call measurement. The first undeclared
    tool pays for it and every later call reads a mapping.

    :meth:`origin_for` is the seam the tool ledger consults for a tool carrying
    no stamp. It returns ``None`` for anything not in the resolved inventory,
    which lands those bytes in ``undeclared_tokens`` exactly as before — the
    alarm still works, it just no longer fires on a population that had no way
    to declare itself.
    """

    SOURCES: Final[tuple[InstalledToolSource, ...]] = (
        # langchain's ``TodoListMiddleware``. Note the package: the third-party
        # prompt sweep is rooted at ``deepagents``, so nothing about this tool —
        # neither its 970-token description nor its system prompt — was visible
        # to any part of the ledger before this row existed.
        InstalledToolSource(
            module="langchain.agents.middleware.todo",
            symbol="write_todos",
            owner="langchain.agents.middleware.todo",
        ),
        # ``deepagents``' filesystem middleware publishes its own complete set,
        # so a library bump that adds a file tool is picked up by resolution and
        # shows up as a fixture diff rather than as a new anonymous span.
        InstalledToolSource(
            module="deepagents.middleware.filesystem",
            symbol="_ALL_FS_TOOL_NAMES",
            owner="deepagents.middleware.filesystem",
        ),
        # Ours, despite where it is installed. ``install_atlas_task_tool``
        # replaces ``deepagents.middleware.subagents._build_task_tool`` at
        # factory import, so the ``task`` the model is shown is this
        # repository's schema text and is fixed by editing this repository.
        # ``TASK_TOOL_NAME`` is read from our own module for the same reason the
        # library rows are resolved rather than restated: one spelling, one
        # owner, and a rename cannot silently orphan the declaration.
        InstalledToolSource(
            module="agent_runtime.delegation.subagents.atlas_task_tool",
            symbol="TASK_TOOL_NAME",
            owner="agent_runtime.delegation.subagents",
            third_party=False,
        ),
    )

    def __init__(
        self,
        *,
        sources: tuple[InstalledToolSource, ...] | None = None,
    ) -> None:
        self._sources = self.SOURCES if sources is None else sources
        self._origins: Mapping[str, ContextOrigin] | None = None

    @classmethod
    def disabled(cls) -> InstalledToolOrigins:
        """An inventory that declares nothing, for tests and library-free hosts.

        Distinct from an inventory whose resolution found nothing: this one
        never imports a dependency, which is what lets a unit test assert
        ``UNDECLARED`` behaviour without depending on whichever ``deepagents``
        or ``langchain`` version happens to be installed.
        """

        return _EMPTY_INSTALLED_TOOL_ORIGINS

    def origin_for(self, tool_name: str) -> ContextOrigin | None:
        """Return the declaration for ``tool_name``, or ``None`` when unknown."""

        if not tool_name:
            return None
        return self._resolved().get(tool_name)

    def inventory(self) -> Mapping[str, str]:
        """``tool_name -> owner:name`` label, the shape the golden fixture pins.

        A mapping rather than the source tuple because that is what a reviewer
        reads in a diff: a dependency that adds a built-in tool adds one line
        naming both the tool and who is going to be charged for it.
        """

        return MappingProxyType(
            {name: origin.label for name, origin in sorted(self._resolved().items())}
        )

    def _resolved(self) -> Mapping[str, ContextOrigin]:
        """Resolve every source once, memoizing the ``name -> origin`` mapping."""

        if self._origins is None:
            self._origins = MappingProxyType(self._build())
        return self._origins

    def _build(self) -> dict[str, ContextOrigin]:
        """Project every resolvable source name onto a declaration.

        First source wins on a name collision. The order in :attr:`SOURCES` is
        therefore meaningful and is the reason our own ``task`` row sits last:
        if a future library bump started publishing a ``task`` of its own, the
        collision would be visible in the fixture rather than silently
        relabelling a tool this repository authors.
        """

        origins: dict[str, ContextOrigin] = {}
        for source in self._sources:
            for tool_name in source.resolve():
                if tool_name in origins:
                    continue
                origin = self._origin(source, tool_name)
                if origin is not None:
                    origins[tool_name] = origin
        return origins

    @staticmethod
    def _origin(source: InstalledToolSource, tool_name: str) -> ContextOrigin | None:
        """Build one declaration, or ``None`` when it cannot be spelled legally.

        ``RESIDENT`` because tool-block text is re-sent on every model call
        until the surface itself changes — the same lifecycle every appended
        tool is declared with, and the reason these bytes are rent rather than
        turn cost.
        """

        try:
            return ContextOrigin(
                owner=source.owner,
                name=tool_name,
                segment_class=ContextSegmentClass.TOOLS,
                lifecycle=ContextLifecycle.RESIDENT,
                third_party=source.third_party,
            )
        except Exception:  # noqa: BLE001 — a label is never worth a failed run
            _LOGGER.debug(
                "Installed tool %r from %s does not form a legal declaration; "
                "it will measure as UNDECLARED.",
                tool_name,
                source.qualified_name,
            )
            return None


_EMPTY_INSTALLED_TOOL_ORIGINS: Final[InstalledToolOrigins] = InstalledToolOrigins(
    sources=()
)


__all__ = (
    "InstalledToolOrigins",
    "InstalledToolSource",
)
