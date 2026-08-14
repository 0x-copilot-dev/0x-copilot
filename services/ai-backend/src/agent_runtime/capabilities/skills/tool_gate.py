"""Run-scoped enforcement of a Skill's declared ``allowed_tools``.

``allowed_tools`` shipped as a *typed, validated, documented* manifest field
that was spent on one f-string in the prompt index and enforced by nothing
(``execution/factory.py``, ``_skill_card_line``). A field with that name and no
gate behind it is worse than no field: it reads as a control to anyone auditing
the manifest, and the only thing standing between a Skill and a tool it
declared it would not touch was the model's willingness to take advice. This
module is the gate that makes the name true.

**The rule, in one sentence.** A run is ungoverned until it loads a Skill that
declares a non-empty ``allowed_tools``; from then on a graph-visible tool call
is admitted only when its name is in the union of every loaded Skill's declared
set, plus the floor below.

Four properties are load-bearing, and each is a decision that could have gone
the other way:

*Armed by loading, not by offering.* The gate is disarmed until
:meth:`SkillToolGate.record_load` sees a restricting Skill, so every run that
loads no Skill — or loads only Skills that declare nothing — has byte-identical
behaviour to before this module existed. A field enforced against runs that
never opted into it would be a silent capability cut across the whole product.

*Union across loaded Skills, never intersection.* Two Skills loaded in one run
were each authored against their own set; intersecting them would make loading
a second Skill revoke the first's tools, which no author of either could have
predicted. Union is also what makes the floor's escape hatch work at all.

*Declaring nothing is not declaring everything.* A Skill with no
``allowed_tools`` contributes nothing to the union, so it can neither narrow an
ungoverned run nor re-widen a governed one. The alternative — treating silence
as "all tools" — would let any unrestricted Skill erase a restriction another
Skill declared, which is a gate an author can dissolve by accident.

*Refuse, do not raise.* A refused call comes back as an error ``ToolMessage``,
the same shape as a budget rejection (``ToolBudgetRejected``) and a hook veto,
so the model reads the refusal, is told what it may call instead and how to
widen the ceiling, and adapts. Narrowing a run is a real behaviour change;
making it terminal would be a much larger one.

**The floor.** A tool belongs in :data:`SkillToolGate.FLOOR` when it cannot
carry out work — when it only organises the run or changes the run's own
ceiling. Three qualify, and the boundary is worth stating precisely because it
is the only part of this module that grants rather than refuses:

``load_skill`` / ``list_skills`` are the progressive-disclosure pair from
:mod:`agent_runtime.capabilities.skills.middleware`, and they are the escape
hatch. The escape hatch is why union-not-intersection matters: a run narrowed
to one Skill's tools can always search for and load a Skill that allows what it
needs next. Take these away and a governed run has no move that changes its own
ceiling — which is the difference between a ceiling and a wedge.

``write_todos`` (LangChain's ``TodoListMiddleware``) mutates nothing outside the
run's own checklist. Deep Agents prompts the model to call it constantly, so
refusing it would fill a governed run's transcript with refusals that protect
nothing. ``task`` deliberately does **not** qualify: delegation reaches real
capability, and a Skill that declared read-only tools must not be able to spawn
a child that writes.

**Addressing an MCP tool.** An author names an MCP tool by its **model-surface**
name — ``mcp__<server>__<tool>``, the exact string
:meth:`~agent_runtime.capabilities.mcp.tool_naming.McpToolName.compose`
registers and the model emits — because that is the only register this seam can
see. A **bare connector-register name does not match a namespaced tool, and that
is deliberate**: ``search`` would otherwise mean "whichever connector happens to
call something search", which is precisely the cross-connector collision
``tool_naming`` exists to remove. A whole connector is addressed with the server
form alone, ``mcp__<server>``, which admits every tool that connector registers.

Matching folds case because the two registers disagree about it and neither is
wrong: ``ValueNormalizer.normalize_slug`` lowercases the manifest entry, while
``McpToolName.sanitize`` preserves the connector's own casing, so a server
advertising ``createIssue`` registers ``mcp__linear__createIssue`` against a
manifest that can only ever hold ``mcp__linear__createissue``.

Binding follows the runtime's per-run ContextVar pattern, and deliberately the
same one as :class:`~agent_runtime.capabilities.skills.usage.SkillUsageLedger`
— the two are bound and unbound together in the worker's run handler, because
they are fed by the same event (a successful ``load_skill``) and a run that has
one without the other could report a Skill as used while enforcing nothing it
declared. Every classmethod is a no-op when nothing is bound, so replay, evals
and unit tests need no gate.
"""

from __future__ import annotations

from collections.abc import Iterable
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
import logging
from typing import ClassVar

from agent_runtime.capabilities.mcp.tool_naming import McpToolName

_LOGGER = logging.getLogger(__name__)


class Log:
    """The lines the gate emits, spelled once.

    Tool and Skill names are user-authored, so they follow the same posture the
    rest of the runtime takes toward user content: the refusal itself is a
    fact worth a WARNING, and it carries the tool name because a refusal
    nobody can attribute is not actionable; the full declared set stays at
    DEBUG.
    """

    REFUSED = "skill_tool_gate.refused tool=%s skills=%s"
    ALLOWED_SET = "skill_tool_gate.allowed run_id=%s allowed=%s"
    NONE = "-"

    @classmethod
    def joined(cls, names: Iterable[str]) -> str:
        """Render a name list for the log, never blank."""
        return ",".join(sorted(names)) or cls.NONE


@dataclass(frozen=True, slots=True)
class SkillToolDecision:
    """Whether one tool call clears the run's Skill ceiling, and why not.

    ``allowed`` is the whole answer for the caller; ``reason`` exists so the
    refusal the model reads is written here, next to the rule that produced it,
    rather than assembled by whichever seam happens to surface it.
    """

    allowed: bool
    reason: str = ""

    @classmethod
    def admit(cls) -> "SkillToolDecision":
        """Return the admitting decision."""
        return cls(allowed=True)


class SkillToolRule:
    """Pure matching between a manifest entry and a registered tool name.

    Separate from the ledger because it is the part with the interesting
    behaviour and no state: every ``allowed_tools`` question is "does this
    declaration cover this registered name", and that question is answerable
    without knowing anything about a run.
    """

    @classmethod
    def normalize(cls, value: object) -> str:
        """Fold one name to the register-agnostic form both sides compare in."""
        return str(value or "").strip().lower()

    @classmethod
    def covers(cls, *, declaration: str, tool_name: str) -> bool:
        """Return ``True`` when ``declaration`` admits ``tool_name``.

        Three shapes, checked in the order an author is most likely to have
        meant them:

        1. An exact name — ``web_search``, or the model-surface
           ``mcp__linear__create_issue``.
        2. A whole connector — ``mcp__linear`` admits every tool that connector
           registers, and nothing else. Written as the server form with no tool
           segment, which :meth:`McpToolName.parse` already rejects as a tool
           name, so the two shapes cannot be confused.
        3. Nothing else. In particular a bare ``search`` never matches
           ``mcp__x__search``: see this module's docstring for why that is a
           refusal and not an oversight.
        """

        declared = cls.normalize(declaration)
        registered = cls.normalize(tool_name)
        if not declared or not registered:
            return False
        if declared == registered:
            return True
        parsed = McpToolName.parse(registered)
        if parsed is None:
            return False
        return declared == cls.normalize(f"{McpToolName.PREFIX}{parsed.server}")


_SKILL_TOOL_GATE: ContextVar["SkillToolGate | None"] = ContextVar(
    "skill_tool_gate", default=None
)


@dataclass
class SkillToolGate:
    """Per-run ceiling assembled from the ``allowed_tools`` of loaded Skills."""

    #: The tools a governed run can always reach. See the module docstring for
    #: the rule that admits a name here. ``ClassVar`` and not ``Final``:
    #: ``dataclasses`` only excludes the former from the generated ``__init__``,
    #: so a ``Final`` here would quietly become a constructor argument callers
    #: could pass a different floor to.
    FLOOR: ClassVar[frozenset[str]] = frozenset(
        {"load_skill", "list_skills", "write_todos"}
    )

    run_id: str = ""
    _declarations: dict[str, frozenset[str]] = field(default_factory=dict, repr=False)

    # ── recording ──────────────────────────────────────────────────────

    def record_load(self, *, skill_name: str, allowed_tools: Iterable[str]) -> None:
        """Fold one loaded Skill's declaration into this run's ceiling.

        A Skill that declares nothing is recorded as declaring nothing rather
        than skipped, so ``restricting_skills`` can tell "loaded and
        unrestricted" from "never loaded" — the difference the refusal message
        needs to name the Skills actually holding the ceiling down.
        """

        name = str(skill_name or "").strip()
        if not name:
            return
        self._declarations[name] = frozenset(
            SkillToolRule.normalize(entry)
            for entry in allowed_tools
            if SkillToolRule.normalize(entry)
        )

    @property
    def armed(self) -> bool:
        """Return ``True`` once a loaded Skill has declared a restriction."""
        return any(self._declarations.values())

    @property
    def restricting_skills(self) -> tuple[str, ...]:
        """Names of the loaded Skills that contribute a restriction."""
        return tuple(
            sorted(name for name, declared in self._declarations.items() if declared)
        )

    @property
    def allowed_tools(self) -> frozenset[str]:
        """The union of every loaded Skill's declaration, floor included.

        Empty when the gate is disarmed — an unarmed gate has no ceiling to
        report, and returning the bare floor there would read as "these two
        tools are all that is allowed", which is the opposite of the truth.
        """

        if not self.armed:
            return frozenset()
        declared: set[str] = set(self.FLOOR)
        for entry in self._declarations.values():
            declared |= entry
        return frozenset(declared)

    # ── the decision ───────────────────────────────────────────────────

    def decide(self, tool_name: str) -> SkillToolDecision:
        """Admit or refuse one graph-visible tool call by registered name."""

        if not self.armed:
            return SkillToolDecision.admit()
        registered = SkillToolRule.normalize(tool_name)
        if not registered:
            # A call with no name cannot be attributed to a declaration either
            # way. The budget seam already fails such a call loudly; this one
            # must not be the reason a nameless call is refused, because the
            # refusal would name no tool and the model could not act on it.
            return SkillToolDecision.admit()
        if registered in self.FLOOR:
            return SkillToolDecision.admit()
        for declared in self._declarations.values():
            for declaration in declared:
                if SkillToolRule.covers(declaration=declaration, tool_name=registered):
                    return SkillToolDecision.admit()
        _LOGGER.warning(
            Log.REFUSED,
            registered,
            Log.joined(self.restricting_skills),
        )
        return SkillToolDecision(allowed=False, reason=self.refusal(tool_name))

    def refusal(self, tool_name: str) -> str:
        """The sentence the model reads when the ceiling refuses a call.

        It names the tool, the Skills holding the ceiling, the tools that are
        allowed, and the one move that widens it. A refusal that omits the last
        of those is a dead end rather than a constraint.
        """

        skills = ", ".join(self.restricting_skills) or "the loaded skills"
        allowed = ", ".join(sorted(self.allowed_tools - self.FLOOR)) or "no tools"
        return (
            f"{str(tool_name).strip()} is not in the allowed_tools declared by "
            f"the skills loaded in this run ({skills}). Allowed here: {allowed}. "
            "Call list_skills and load_skill to load a skill that allows it, or "
            "continue without this tool."
        )

    # ── run binding ────────────────────────────────────────────────────

    @classmethod
    def bind_for_run(cls, gate: "SkillToolGate") -> Token:
        """Bind ``gate`` to the current run; return the restoration token."""
        return _SKILL_TOOL_GATE.set(gate)

    @classmethod
    def unbind(cls, token: object) -> None:
        """Emit the run's ceiling and restore the previous binding."""
        gate = _SKILL_TOOL_GATE.get(None)
        if gate is not None:
            gate.emit()
        _SKILL_TOOL_GATE.reset(token)  # type: ignore[arg-type]

    @classmethod
    def active(cls) -> "SkillToolGate | None":
        """Return the bound gate, or ``None`` outside a bound run."""
        return _SKILL_TOOL_GATE.get(None)

    @classmethod
    def record_skill_load(
        cls,
        *,
        skill_name: str,
        allowed_tools: Iterable[str],
    ) -> None:
        """Record a load on the bound gate; no-op when unbound."""
        gate = _SKILL_TOOL_GATE.get(None)
        if gate is not None:
            gate.record_load(skill_name=skill_name, allowed_tools=allowed_tools)

    @classmethod
    def evaluate(cls, tool_name: str) -> SkillToolDecision:
        """Decide one call against the bound gate; admit when unbound.

        Admitting when unbound is the same posture every other run-scoped
        sidecar takes: replay, evals and unit tests bind nothing, and a gate
        that refused everything outside a bound run would fail them all.
        """

        gate = _SKILL_TOOL_GATE.get(None)
        if gate is None:
            return SkillToolDecision.admit()
        return gate.decide(tool_name)

    def emit(self) -> None:
        """Log the run's final ceiling. Never raises."""

        try:
            if not self.armed:
                return
            _LOGGER.info(
                Log.ALLOWED_SET,
                self.run_id,
                Log.joined(self.allowed_tools),
            )
        except Exception:  # pragma: no cover - observability must never fail a run
            _LOGGER.debug("skill_tool_gate.emit_failed", exc_info=True)


__all__ = [
    "SkillToolDecision",
    "SkillToolGate",
    "SkillToolRule",
]
