"""The PEP for ``run_command`` — every command reaches a decision (§5, §8, §9).

This is the Policy **Enforcement** Point. The Policy **Decision** Point is
:class:`~agent_runtime.capabilities.policy.service.PdpPolicyService`, and the
policy *data* it decides from is authored in ``services/backend`` and delivered
once, at run-create, on ``AgentRuntimeContext.user_policies_json``. That split is
the root ``CLAUDE.md`` rule this module exists to honour:

    Policy data belongs to ``backend``; policy enforcement stays in the runtime …
    snapshot the policy once at run start, enforce in-process, POST the facts
    afterwards. **Never put a per-call HTTP hop on the tool path.**

Nothing here opens a socket. The ``execute`` mode arrives in the same snapshot
the read / write / destructive modes already ride, and
:class:`ShellCommandPolicyGate` reads it out of the run context it was
constructed with.

THE ORDER IS THE DESIGN
-----------------------
One call flows exactly:

1. :meth:`CommandNeverList.screen` — the **pre-PDP lexical screen** (§9.3). It
   runs on the full, untruncated command **before the PDP is entered**, because
   it is the only reader that can tokenise: a ``PermissionRule``'s subject half
   is one glob ``fullmatch``ed against one opaque string, with no word
   boundaries, so it cannot express "``sudo`` in command position" — the
   property that separates ``sudo rm -rf /`` from
   ``git commit -m "no sudo here"``. A screen hit raises, and **no approval card
   is ever created**: there is nothing to click past.
2. :meth:`PdpPolicyService.decide` — availability, then authorization, then the
   posture ladder, whose 3.5½ rung GATEs ``EXECUTE`` in every posture unless the
   axis itself is authored ``auto``. That rung, not this module, is why
   ``BYPASS`` does not auto-run a command.
3. ``GATE`` ⇒ :meth:`ToolAccessGate.park_command_for_approval` — park the run on
   a LangGraph interrupt, resume on the human's decision, in the same run.

There is **no fourth path**. :meth:`ShellCommandPolicyGate.authorize` either
returns a :class:`ShellAuthorization` — which only the ALLOW arm and the
approved-resume arm construct — or raises :class:`ShellRefusedError`. A caller
cannot obtain permission to run without a decision having produced it, because
there is no other constructor of the permission object.

FAIL-CLOSED POINTS, NAMED
-------------------------
* ``never_list`` is a **required** constructor argument, not an optional one. An
  optional screen has an unwired state, and an unwired state on this seam is a
  command reaching the PDP without §9.3 having run.
* ``gate is None`` (no approval channel wired for this run) ⇒ a typed refusal,
  never a silent dispatch. The invariant is
  ``capabilities/mcp/middleware/policy_tool.py``'s, verbatim.
* The shipped never-list floor is merged **LAST** into the PDP's ``_never``
  ruleset, because ``PermissionRuleset.evaluate`` is last-match-wins and
  ``merge`` concatenates (§9.5).
* An always-grant is offered only when the never-list vouches for the command as
  a single simple command, and the rule it writes is keyed on the tokeniser's
  patterns — an empty pattern tuple writes nothing, so an ``always`` that
  arrives for a command nobody vouched for degrades to ``once``.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Final, Protocol

from agent_runtime.capabilities.actions.policy import ConnectorWritePolicyOverrides
from agent_runtime.capabilities.policy.contracts import (
    CapabilityDescriptor,
    PolicyDecision,
    Posture,
)
from agent_runtime.capabilities.policy.decisions import (
    DecisionScope,
    PendingAsk,
    RunDecisionLedgers,
)
from agent_runtime.capabilities.policy.rules import PermissionRuleset
from agent_runtime.capabilities.policy.service import PdpPolicyService
from agent_runtime.capabilities.shell.contracts import (
    ShellRefusal,
    ShellRefusalReason,
    ShellRefusedError,
)
from agent_runtime.capabilities.shell.descriptor import (
    BuiltinCapabilityAllowlist,
    RunCommandDescriptor,
    ShellCapability,
    ShellPrincipal,
)
from agent_runtime.capabilities.tools.permissions import ToolUsePolicySnapshot
from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeContract
from agent_runtime.execution.filesystem_bypass import FilesystemBypassMode
from agent_runtime.surfaces_v2.gate import ToolAccessGate


class _Note:
    """Model-facing refusal sentences for decisions this module makes.

    Authored constants, never interpolated from a PDP reason code: the codes are
    deliberately coarse so model output cannot distinguish "missing scope" from
    "not allowlisted", and rendering one into a sentence would un-coarsen it.
    Each line says whether the condition is permanent, following the convention
    ``PolicyStageMessages`` sets — a deterministic refusal described as
    temporary sends the model into a retry loop that cannot succeed.
    """

    NOT_PERMITTED: Final = (
        "This command is not permitted in this workspace. Nothing was run, and "
        "there is nothing to approve: retrying will not change it."
    )
    WORKSPACE_GONE: Final = (
        "The workspace this command would run in is no longer available. "
        "Nothing was run."
    )
    DECLINED: Final = (
        "You declined this command, so it was not run. Nothing on disk was "
        "changed by it."
    )
    APPROVAL_UNAVAILABLE: Final = (
        "Running a command needs your approval, which is not available for this "
        "run, so it was not run. Retrying the same way will not change it."
    )


class _PdpReason:
    """The one PDP DENY code this stage distinguishes.

    Mirrors ``PdpPolicyService._Reason.CONNECTOR_UNAVAILABLE`` — the availability
    denial, which for the command lane means the sealed enablement or the bound
    grant went away (§7.2). Every other DENY (never-list floor, workspace
    ``BLOCK``, an authored ``deny`` rule) collapses to "not permitted", exactly
    as in the MCP stage: the PDP coarsens them on purpose and this table must not
    un-coarsen them.
    """

    CONNECTOR_UNAVAILABLE: Final = "connector_unavailable"


class DecisionBasis(StrEnum):
    """What authorised one command. Carried into the §15.3 audit row.

    Three values because there are exactly three ways a command becomes
    runnable, and an audit row that cannot tell them apart cannot answer "who
    approved this".
    """

    #: The PDP returned ALLOW without a card — the ``execute`` axis is authored
    #: ``auto``, or a run-scoped grant from an earlier ``always`` answered it.
    POLICY = "policy"
    #: A human approved this call, and only this call.
    APPROVED_ONCE = "once"
    #: A human approved this call and authorised its ``argv[0]`` for the rest of
    #: this run, in this workspace.
    APPROVED_ALWAYS = "always"


class ShellAuthorization(RuntimeContract):
    """Permission to run **one** command, and the record of how it was obtained.

    The only object that says "dispatch". It is constructed in exactly two
    places in this module — the ALLOW arm and the approved-resume arm — and both
    are downstream of :meth:`PdpPolicyService.decide`. That is the structural
    form of "no path returns ALLOW without a decision": there is no other
    constructor, so a future edit that skipped the PDP would have nothing to
    return.
    """

    basis: DecisionBasis
    #: The PDP's stable machine reason code for the decision that produced this.
    #: Empty string for a plain ALLOW, matching ``_Reason.ALLOW``.
    reason: str = ""
    #: The approval id the card, the ledger and the resume joined on. ``None``
    #: when no card was drawn.
    approval_id: str | None = None
    #: Whether the human was offered a run-scoped grant for this command.
    always_offered: bool = False


class CommandNeverList(Protocol):
    """The tokenising judgements about a command line (§9.2, §9.3, §8.3).

    Implemented by :mod:`agent_runtime.capabilities.shell.never_list`, which owns
    the one data table both readers are derived from. Declared here as a port
    because the PEP needs the *judgements*, not the table: this module must be
    able to state the ordering property (screen strictly before the PDP) and the
    fail-closed property (no patterns ⇒ no grant) without also owning a shell
    tokeniser.

    Every method is total. A tokeniser that cannot parse must return the
    narrowing answer, never raise into the tool path.
    """

    def screen(self, command: str) -> ShellRefusal | None:
        """The §9.3 lexical screen. A refusal means **no card is ever drawn**."""
        ...

    def floor(self) -> PermissionRuleset:
        """The coarse whole-command DENY rows for the PDP's ``_never`` ruleset.

        The same data table as :meth:`screen`, compiled for a reader that cannot
        tokenise. Kept as a floor rather than dropped: it survives a bug in the
        screen and it is the rung the PDP evaluates above every rule and every
        posture, so it holds if a second call path ever skips the screen.
        """
        ...

    def always_grant_patterns(self, command: str) -> tuple[str, ...]:
        """``argv[0]``-keyed rule patterns for a run-scoped grant, or ``()``.

        ``()`` means "this command may not earn a standing yes" — it is
        compound, or its first token is a wrapper binary (``env``, ``sh``,
        ``sudo``, ``xargs``…) that is not the command actually being run. The
        emptiness does double duty: it withholds the ``allow_always`` control on
        the card AND makes an ``always`` reply write no rule, so the two cannot
        disagree.
        """
        ...


class ShellCommandPolicyGate:
    """Decide, park, and refuse — the one enforcement point for ``run_command``.

    Constructed once per run by the tool factory and bound to the tool at
    registration, so nothing model-supplied participates in choosing *what* is
    being authorized: the binding is the tool, not the payload (the first of the
    three properties ``PolicyToolMiddleware`` is built around).
    """

    __slots__ = ("_runtime_context", "_never_list", "_gate")

    #: The approval-id namespace for this lane. Deliberately NOT ``mcp_write:``.
    #:
    #: Minting a lookalike would make two client predicates
    #: (``allowsRunScopedGrant``, ``writeGateCallId``) start matching, which is
    #: tempting and wrong: the prefix is the client's proof that the id's
    #: suffix is an MCP tool-call id from the MCP policy middleware, and
    #: borrowing it to mean something else is how a join silently binds the
    #: wrong call. The command card binds to its tool call by ``tool_name``
    #: instead — the second join ``eventProjector.ts::matchAskToCall`` already
    #: implements for exactly the lanes whose id is not that shape, which is why
    #: ``tool_name`` is on the interrupt payload.
    _APPROVAL_PREFIX: Final = "shell_exec"

    #: The key under which the root-scoped grant subject is offered to the PDP.
    #: See :meth:`_policy_arguments` for why it exists.
    _GRANT_SUBJECT_ARG: Final = "workspace_scoped_command"

    def __init__(
        self,
        *,
        runtime_context: AgentRuntimeContext,
        never_list: CommandNeverList,
        gate: ToolAccessGate | None,
    ) -> None:
        """Bind the run's policy inputs, its screen, and its approval channel.

        ``never_list`` has no default **because there is no safe default**. An
        optional screen is a screen that can be absent, and an absent screen is
        §9.3 not running — which would be invisible, because the PDP floor would
        still catch the coarse cases and the suite would look green.
        """

        self._runtime_context = runtime_context
        self._never_list = never_list
        self._gate = gate

    async def authorize(
        self,
        *,
        command: str,
        workspace_label: str,
        available: bool,
        tool_call_id: str | None = None,
    ) -> ShellAuthorization:
        """Return permission to run ``command``, or raise a typed refusal.

        ``available`` is the caller's §7.2 recheck — the sealed enablement AND
        the bound root still being granted and still writable, re-read at call
        time. It is threaded into the descriptor rather than checked here so the
        recheck flows *through* the PDP (Stage 1) instead of becoming a second
        gate that has to agree with the first.
        """

        # 1 — The pre-PDP lexical screen. Above everything, including the
        #     descriptor: a never-listed command must not even be described,
        #     let alone carded.
        refusal = self._never_list.screen(command)
        if refusal is not None:
            raise ShellRefusedError(refusal)

        descriptor = RunCommandDescriptor.for_availability(available=available)
        arguments = self._policy_arguments(
            command=command, workspace_label=workspace_label
        )
        decision, reason = self._pdp().decide(
            principal=ShellPrincipal.for_run(self._runtime_context),
            descriptor=descriptor,
            args=arguments,
            posture=self._posture(),
        )
        if decision is PolicyDecision.ALLOW:
            return ShellAuthorization(basis=DecisionBasis.POLICY, reason=reason)
        if decision is PolicyDecision.DENY:
            raise ShellRefusedError(self._denial(reason))
        return await self._park(
            command=command,
            workspace_label=workspace_label,
            descriptor=descriptor,
            arguments=arguments,
            reason=reason,
            tool_call_id=tool_call_id,
        )

    # -- decision inputs ---------------------------------------------------

    def _pdp(self) -> PdpPolicyService:
        """Compose the PDP from this run's sealed policy snapshot.

        Rebuilt per call, from frozen run-scoped data plus the run ledger, for
        the same reason ``McpDispatchPolicy.evaluate`` rebuilds it: the ledger's
        rules change when a human answers ``always``, and a PDP cached at
        registration would answer with the ruleset the run started under.

        Two orderings are load-bearing and both are stated in one place here:

        * ``rules`` is ``authored ⧺ this run's grants``. The ruleset is
          last-match-wins, so a grant the user just answered overrides a broader
          authored default and never the reverse.
        * ``never`` is ``authored ⧺ the shipped floor``. The floor is merged
          LAST so a permissive user row cannot sit after it and win (§9.5).
        """

        authored, authored_never = PermissionRuleset.authored(
            self._runtime_context.user_policies_json
        )
        return PdpPolicyService(
            snapshot=self._snapshot(),
            overrides=ConnectorWritePolicyOverrides.from_user_policies(
                self._runtime_context.user_policies_json
            ),
            allowlist=BuiltinCapabilityAllowlist(),
            rules=authored.merge(
                RunDecisionLedgers.for_run(self._runtime_context.run_id).rules
            ),
            never=authored_never.merge(self._never_list.floor()),
        )

    def _snapshot(self) -> ToolUsePolicySnapshot:
        """The run's ``(axis → mode)`` map, folded once at run-create.

        Imported lazily for the reason ``McpDispatchPolicy._snapshot`` is: the
        tool-enforcement stack must not load on every import path that only
        needs the shell contracts.
        """

        from agent_runtime.capabilities.tools.tool_use_enforcement import (  # noqa: PLC0415
            ToolUsePolicyResolver,
        )

        return ToolUsePolicyResolver.resolve(self._runtime_context)

    def _posture(self) -> Posture:
        """Map the run's filesystem-bypass mode onto the approval posture.

        Value-identical enums, mapped explicitly so ``BYPASS`` is only ever
        selected for the literal bypass mode and never by a truthy accident.
        The mapping is what makes §8.2's claim testable: the composer's bypass
        pill reaches the ladder as ``Posture.BYPASS``, and rung 3.5½ GATEs it.
        """

        if self._runtime_context.filesystem_bypass.mode is FilesystemBypassMode.BYPASS:
            return Posture.BYPASS
        return Posture.MANUAL

    @classmethod
    def _policy_arguments(
        cls, *, command: str, workspace_label: str
    ) -> Mapping[str, object]:
        """What the PDP sees as this call's arguments.

        The first two are the call's real arguments, and folding them into
        :class:`PolicySubjects` is what makes the command string itself a policy
        subject — matched by the same rule engine that matches MCP tool
        arguments.

        The third is **not an argument the model supplied**, and it is here for a
        mechanical reason worth stating rather than hiding. §8.3 keys a
        run-scoped grant on ``(run_id, bound_root_label, argv[0])``, but the rule
        engine has no conjunction: ``PermissionRuleset.verdict`` folds
        independently over each subject and returns the strictest single match,
        so no pair of patterns over ``command`` and ``workspace`` can express
        "this ``argv[0]`` **and** this root". One subject carrying both facts
        can. Hence ``run_command@<label>: <command>``, against which an
        ``always`` writes ``run_command@<label>: pytest`` and
        ``run_command@<label>: pytest *`` — patterns that cannot match the same
        ``argv[0]`` reached from a different bound root.

        It is safe to add because a subject can only ever ADD a match, never
        remove one: the floor rows and every authored rule still see the raw
        ``command`` subject unchanged, so nothing that refused before starts
        permitting. The label is runtime-chosen from a closed set of mount
        slugs, never model text, so it cannot smuggle a wildcard into a pattern.
        """

        return {
            "command": command,
            "workspace": workspace_label,
            cls._GRANT_SUBJECT_ARG: cls.grant_subject(
                command=command, workspace_label=workspace_label
            ),
        }

    @staticmethod
    def grant_subject(*, command: str, workspace_label: str) -> str:
        """The root-scoped subject a run grant is written and matched against.

        Public because the never-list builds its ``argv[0]`` patterns in the
        same shape, and two spellings of one join is how a grant silently stops
        matching the thing it was written for.
        """

        return f"{ShellCapability.OP}@{workspace_label}: {command}"

    # -- outcomes ----------------------------------------------------------

    @staticmethod
    def _denial(reason: str) -> ShellRefusal:
        """Map a PDP DENY reason onto a typed, non-leaking refusal.

        ``connector_unavailable`` is the availability denial and becomes
        ``unavailable`` — the capability could not serve this call, and the
        command itself was never judged. Everything else becomes ``refused``
        with the unappealable sentence, because everything else IS a judgement
        about the command.
        """

        if reason == _PdpReason.CONNECTOR_UNAVAILABLE:
            return ShellRefusal.unavailable(
                ShellRefusalReason.WORKSPACE_UNAVAILABLE, _Note.WORKSPACE_GONE
            )
        return ShellRefusal.refused(
            ShellRefusalReason.COMMAND_NOT_PERMITTED, _Note.NOT_PERMITTED
        )

    async def _park(
        self,
        *,
        command: str,
        workspace_label: str,
        descriptor: CapabilityDescriptor,
        arguments: Mapping[str, object],
        reason: str,
        tool_call_id: str | None,
    ) -> ShellAuthorization:
        """Park on the approval interrupt and resume on the human's decision."""

        if self._gate is None:
            # No approval channel is wired for this run. A GATE fails CLOSED:
            # never a silent dispatch, and never a command that runs because
            # nobody could be asked.
            raise ShellRefusedError(
                ShellRefusal.refused(
                    ShellRefusalReason.COMMAND_APPROVAL_UNAVAILABLE,
                    _Note.APPROVAL_UNAVAILABLE,
                )
            )
        patterns = self._never_list.always_grant_patterns(command)
        scoped = tuple(
            self.grant_subject(command=pattern, workspace_label=workspace_label)
            for pattern in patterns
        )
        approval_id = self._approval_id(tool_call_id)
        ledger = RunDecisionLedgers.for_run(self._runtime_context.run_id)
        # Remember the ask BEFORE parking, keyed on the id the card and the
        # resume path already join on. Registering after would be too late in
        # the one case that matters: a sibling call answered ``always`` while
        # this one is parked could not cover an ask the ledger was never told
        # about. ``subjects`` are the GRANT patterns, not this call's subjects,
        # because they are what ``reply`` turns into ALLOW rows — an empty tuple
        # is therefore how "no standing yes for this command" is enforced rather
        # than merely not offered.
        ledger.register(
            PendingAsk(
                request_id=approval_id,
                permission=descriptor.urn,
                subjects=scoped,
            )
        )
        resume = await self._gate.park_command_for_approval(
            command=command,
            workspace_label=workspace_label or None,
            approval_id=approval_id,
            simple_command=bool(scoped),
        )
        if not resume.approved:
            raise ShellRefusedError(
                ShellRefusal.refused(
                    ShellRefusalReason.COMMAND_DECLINED, _Note.DECLINED
                )
            )
        # An APPROVED resume is the one moment the user's chosen scope exists in
        # the runtime. ``once`` writes nothing; ``always`` appends the ALLOW rows
        # the NEXT ``decide`` in this run reads back, so the second ``pytest``
        # dispatches without a second card.
        outcome = ledger.reply(
            request_id=approval_id, scope=DecisionScope.from_wire(resume.decision_scope)
        )
        del arguments  # policed above; not re-read on the resume path.
        return ShellAuthorization(
            basis=(
                DecisionBasis.APPROVED_ALWAYS
                if outcome.scope is DecisionScope.ALWAYS
                else DecisionBasis.APPROVED_ONCE
            ),
            reason=reason,
            approval_id=approval_id,
            always_offered=bool(scoped),
        )

    def _approval_id(self, tool_call_id: str | None) -> str:
        """Deterministic, per-call id for this command's approval.

        Both halves are load-bearing in opposite directions, exactly as in
        ``PolicyGatedMcpTool._approval_id``:

        * **Deterministic** across the park→resume replay — LangGraph
          re-executes the tool node from the top on resume, so the id computed
          on the parked pass and on the resumed pass must be identical or the
          approval record cannot join.
        * **Unique per call** — two commands in one run must not share an id, or
          the second park would find the first approval already resolved, no new
          pending approval would be created, and the run would park forever.

        The tool-name fallback covers direct invocation (unit tests, replay),
        where LangChain injects nothing and only one call is ever in flight.
        """

        suffix = tool_call_id or ShellCapability.OP
        return f"{self._APPROVAL_PREFIX}:{self._runtime_context.run_id}:{suffix}"


__all__ = [
    "CommandNeverList",
    "DecisionBasis",
    "ShellAuthorization",
    "ShellCommandPolicyGate",
]
