"""Blocking grant request for a host folder the agent has no grant for.

A host-absolute path with no covering grant is neither readable nor a silent
empty listing: it parks the run and ASKS. The mechanism is the same
``langgraph.types.interrupt`` seam
:class:`~agent_runtime.capabilities.mcp.middleware.auth_mcp.AuthMcpTool` uses for
MCP authentication — a typed payload with a deterministic ``approval_id``, a safe
public message, and a resume the gate interprets fail-closed. A host that already
renders the MCP connect card renders this one from the same shape.

Where a host path is allowed to travel
--------------------------------------
This is the ONE direction a host-absolute path moves: agent → user, so a consent
surface can render "grant access to <folder>?". It is never sent to the broker,
which keeps
:mod:`agent_runtime.capabilities.desktop.workspace_backend`'s property intact —
only mount names and root-relative virtual paths cross that boundary.

Fail-closed rules
-----------------
The resume is untrusted, exactly like model output:

* a decision that is not an approval ⇒ denied;
* a ``grant_id`` that is not in the broker's CURRENT active snapshot ⇒ denied
  (the snapshot is the only authority on which grants exist);
* no echoed ``root`` ⇒ denied. The granted root is what binds virtual paths to a
  broker grant; assuming it would let a read of ``<root>/a.csv`` resolve against
  a *different* root and quietly return the wrong file;
* an echoed ``root`` that does not contain the folder the user was asked about ⇒
  denied, so an approval cannot be redirected to an unrelated tree;
* more than one newly-appeared grant with no ``grant_id`` to disambiguate ⇒
  denied, because binding the wrong one mis-roots every later read.

Every denial carries a safe message the model can act on. None of them is
silence.

What the resume cannot carry yet
--------------------------------
``root`` is required and cannot currently reach this gate. ``_resume_payload`` in
:mod:`runtime_worker.handlers.approval` branches on ``approval_kind`` and has no
``workspace_grant`` branch, so a folder decision falls to the MCP-tool default and
arrives as ``{"decisions": [{"type": "approve"}]}`` — an approval with no echoed
root and no grant id. :meth:`WorkspaceGrantResume.parse` reads that shape as the
approval it is, and the missing root then denies with
:attr:`WorkspaceGrantMessages.UNBOUND`.

That is deliberate, and the fail-closed half must NOT be relaxed to close the gap.
The broker's grant projection is path-free, so if the host granted a PARENT of the
folder we asked about, assuming the asked folder as the root would make every
later relative path resolve against the wrong directory and return the WRONG FILE,
silently — a worse defect than the empty listing this program exists to kill.
Completing the lane is one branch in ``_resume_payload`` echoing
``{grant_id, root}`` from the decision body; until it lands, an approved folder
ends in an explicit, actionable refusal rather than a wrong answer.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Final, Protocol, cast

from langgraph.types import interrupt as langgraph_interrupt
from pydantic import BaseModel, ConfigDict, Field

from agent_runtime.capabilities.desktop.broker_client import (
    BrokerError,
    BrokerGrant,
    BrokerGrantSnapshot,
)
from agent_runtime.capabilities.desktop.host_path import (
    ClassifiedPath,
    HostPathClassifier,
    HostPathFlavour,
    HostPathKind,
    HostPathMessages,
)


class WorkspaceGrantValues:
    """Wire constants for the workspace-grant approval.

    ``EVENT_TYPE`` must be an event type the run projection already recognises,
    and that is not a style preference. ``StreamMessageParser.explicit_api_payloads``
    collects an interrupt payload only when ``api_event_type`` parses as a
    ``RuntimeApiEventType``; a bespoke name (this constant was
    ``workspace_grant_required``) makes the payload invisible to the walk, so NO
    event is appended, NO ``ApprovalRequestRecord`` is written and NO batch is
    inserted. The run parks on a LangGraph interrupt the client is never told
    about — a hang instead of an empty listing, but the same lie: a question about
    the filesystem answered with silence. ``approval_requested`` is the generic
    blocking-approval type that creates the approval row and its 1-item batch, so
    the run stays resumable.

    ``APPROVAL_KIND`` therefore carries the discrimination instead, and the client
    does not key on it either: ``packages/chat-surface`` keys the folder card on
    the PRESENCE of the ``workspace_grant`` payload block, precisely so a new kind
    need not be added to a union each host declares its own copy of. Downstream an
    unrecognised kind collapses to the generic "approval" pause reason and stays a
    free-standing card (``_approval_event_morphs_tool_bubble`` only morphs the
    tool bubble for ``mcp_tool``), which is what a folder ask wants.
    """

    EVENT_TYPE: Final = "approval_requested"
    APPROVAL_KIND: Final = "workspace_grant"
    APPROVAL_PREFIX: Final = "workspace_grant"
    SOURCE: Final = "workspace_backend"
    MODE_READ_ONLY: Final = "read_only"
    #: Resume decisions that count as an approval (mirrors the MCP auth gate,
    #: including the approve-with-edits coercion the approval batch applies).
    APPROVED_DECISIONS: Final = frozenset(
        {"approved", "approve", "approve_with_edits", "granted", "grant"}
    )
    #: ``decisions[].type`` values that count as an approval. This is the shape
    #: the CURRENT production resume actually has: ``_resume_payload`` in
    #: ``runtime_worker.handlers.approval`` branches on ``approval_kind`` and has
    #: no ``workspace_grant`` branch, so a folder decision falls to the MCP-tool
    #: default and arrives as ``{"decisions": [{"type": "approve"}]}``. Reading
    #: only ``decision`` would score a real approval as a refusal.
    APPROVED_DECISION_TYPES: Final = frozenset({"approve", "approve_with_edits"})


class _PayloadKey:
    """Keys on the interrupt payload.

    The envelope keys mirror ``AuthMcpTool.ainvoke`` byte-for-byte so the
    StreamOrchestrator's existing approval batching, the approval record and the
    resume plumbing treat this interrupt like any other.
    """

    API_EVENT_TYPE: Final = "api_event_type"
    EVENT_TYPE: Final = "event_type"
    APPROVAL_ID: Final = "approval_id"
    ACTION_ID: Final = "action_id"
    APPROVAL_KIND: Final = "approval_kind"
    MESSAGE: Final = "message"
    SOURCE_TOOL: Final = "source_tool"
    #: The block that turns this interrupt into a folder ask. The name is the
    #: client contract: ``packages/chat-surface`` exports it as
    #: ``WORKSPACE_GRANT_PAYLOAD_KEY`` and its card is keyed on the block's
    #: presence rather than on the approval kind, so stamping this one block is
    #: the whole producer obligation.
    GRANT: Final = "workspace_grant"


class _GrantKey:
    """Keys inside the ``workspace_grant`` block, and on the resume.

    ``PATH`` / ``MODE`` / ``REASON`` are the fields the client's
    ``parseWorkspaceGrantRequest`` reads, and ``PATH`` is REQUIRED there: that
    parser returns null when the block names no ``path``, and a null means "this
    interrupt is not a folder ask", so the card silently does not render. Spelling
    it anything else (this producer briefly emitted ``folder_path``) yields a
    parked run with a generic card and no folder in it. ``FOLDER_NAME`` and
    ``PLATFORM`` are additive for hosts that would rather not re-derive them.
    ``ROOT`` / ``GRANT_ID`` / ``DECISION`` / ``DECISIONS`` are resume-side only.
    """

    PATH: Final = "path"
    FOLDER_NAME: Final = "folder_name"
    PLATFORM: Final = "platform"
    MODE: Final = "mode"
    REASON: Final = "reason"
    ROOT: Final = "root"
    GRANT_ID: Final = "grant_id"
    DECISION: Final = "decision"
    DECISIONS: Final = "decisions"
    TYPE: Final = "type"


class WorkspaceGrantMessages:
    """Safe public copy for the grant request and every denial."""

    @staticmethod
    def ask(folder_name: str) -> str:
        """The fallback line a generic approval card renders."""

        return (
            f"Allow reading the folder {folder_name}? The agent has no access to "
            "it yet and cannot read it until you grant access."
        )

    @staticmethod
    def granted(folder_name: str) -> str:
        """Confirmation the model sees once a grant is bound."""

        return f"Access to {folder_name} was granted."

    DECLINED: Final = (
        "The user did not grant access to that folder, so it cannot be read. Do "
        "not describe its contents."
    )
    UNBOUND: Final = (
        "Access to that folder could not be established. Ask the user to grant "
        "the folder again from the workspace settings."
    )
    UNAVAILABLE: Final = (
        "Host folder access is temporarily unavailable, so that folder could not "
        "be read."
    )
    NOT_GRANTED: Final = (
        "That folder has not been granted to this workspace, so it cannot be "
        "read. Ask the user to grant it."
    )


class WorkspaceGrantSnapshotReader(Protocol):
    """The one broker read this gate performs: the CURRENT active grant set."""

    async def grants_snapshot(self) -> BrokerGrantSnapshot:
        """Return the broker's active, path-free grant snapshot."""


class WorkspaceGrantRequest(BaseModel):
    """The typed consent request for one host folder.

    ``folder_path`` is host-absolute — the whole point of the card — and is
    bounded and control-character-free by construction (the classifier refuses
    anything else before a request is built).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_id: str = Field(min_length=1, max_length=256)
    folder_path: str = Field(min_length=1, max_length=1024)
    folder_name: str = Field(min_length=1, max_length=255)
    platform: HostPathFlavour
    mode: str = WorkspaceGrantValues.MODE_READ_ONLY

    def interrupt_payload(self) -> dict[str, Any]:
        """Render the payload handed to the interrupt seam."""

        return {
            _PayloadKey.API_EVENT_TYPE: WorkspaceGrantValues.EVENT_TYPE,
            _PayloadKey.EVENT_TYPE: WorkspaceGrantValues.EVENT_TYPE,
            _PayloadKey.APPROVAL_ID: self.approval_id,
            _PayloadKey.ACTION_ID: self.approval_id,
            _PayloadKey.APPROVAL_KIND: WorkspaceGrantValues.APPROVAL_KIND,
            _PayloadKey.MESSAGE: WorkspaceGrantMessages.ask(self.folder_name),
            _PayloadKey.SOURCE_TOOL: WorkspaceGrantValues.SOURCE,
            _PayloadKey.GRANT: {
                _GrantKey.PATH: self.folder_path,
                _GrantKey.FOLDER_NAME: self.folder_name,
                _GrantKey.PLATFORM: self.platform.value,
                _GrantKey.MODE: self.mode,
            },
        }


@dataclass(frozen=True)
class WorkspaceGrantResume:
    """The gate's fail-closed reading of an untrusted resume value."""

    approved: bool
    grant_id: str | None = None
    root: str | None = None

    #: Grant ids are opaque broker tokens; anything else is not one.
    _GRANT_ID: ClassVar[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
    #: Defensive cap on any echoed string before it is parsed further.
    _MAX_TEXT: ClassVar[int] = 1024

    @classmethod
    def parse(cls, resume: object) -> WorkspaceGrantResume:
        """Read ``{decision, grant_id, root}`` out of an arbitrary resume value.

        Three approval spellings are honoured, because three are real:

        * ``decisions[{type}]`` — what production sends TODAY for this kind (see
          :attr:`WorkspaceGrantValues.APPROVED_DECISION_TYPES`);
        * a flat ``decision`` string — what the MCP-auth branch sends, and the
          shape a dedicated ``workspace_grant`` resume branch would use;
        * bare ``True`` — accepted like the MCP gate accepts it, but it carries no
          root, so it is denied downstream rather than guessed.

        Being liberal about WHERE the fields sit is safe; being liberal about
        their VALUES is not. Every fail-closed rule below (the grant id must be in
        the broker snapshot, the root must contain the asked folder) is applied to
        the values regardless of which spelling carried them.
        """

        if resume is True:
            return cls(approved=True)
        if not isinstance(resume, Mapping):
            return cls(approved=False)
        source = cls._source(resume)
        return cls(
            approved=cls._approved(source),
            grant_id=cls._opaque(source.get(_GrantKey.GRANT_ID)),
            root=cls._text(source.get(_GrantKey.ROOT)),
        )

    @staticmethod
    def _source(resume: Mapping[str, object]) -> Mapping[str, object]:
        """Flatten an echoed ``workspace_grant`` block over the resume itself.

        A host may echo the block it was handed rather than flattening it. The
        block is overlaid so its fields win, and a resume with no block is read
        exactly as before.
        """

        block = resume.get(_PayloadKey.GRANT)
        if isinstance(block, Mapping):
            return {**resume, **block}
        return resume

    @classmethod
    def _approved(cls, source: Mapping[str, object]) -> bool:
        """True when ``source`` carries an approval in any of the live spellings."""

        decision = source.get(_GrantKey.DECISION)
        if isinstance(decision, str):
            return decision.strip().lower() in WorkspaceGrantValues.APPROVED_DECISIONS
        return cls._approved_batch(source.get(_GrantKey.DECISIONS))

    @staticmethod
    def _approved_batch(decisions: object) -> bool:
        """True when every entry of a ``decisions[]`` batch approves.

        A folder ask is always a 1-item batch, but "every entry approves" is the
        rule that stays correct if it ever shares one: a batch holding a single
        rejection is not an approval of the folder.
        """

        if not isinstance(decisions, Sequence) or isinstance(decisions, (str, bytes)):
            return False
        entries = tuple(decisions)
        if not entries:
            return False
        return all(
            isinstance(entry, Mapping)
            and isinstance(entry.get(_GrantKey.TYPE), str)
            and cast("str", entry.get(_GrantKey.TYPE)).strip().lower()
            in WorkspaceGrantValues.APPROVED_DECISION_TYPES
            for entry in entries
        )

    @classmethod
    def _opaque(cls, value: object) -> str | None:
        """Keep ``value`` only when it is shaped like a broker grant id."""

        if isinstance(value, str) and cls._GRANT_ID.fullmatch(value):
            return value
        return None

    @classmethod
    def _text(cls, value: object) -> str | None:
        """Keep ``value`` only when it is a non-empty, bounded string."""

        if isinstance(value, str) and value and len(value) <= cls._MAX_TEXT:
            return value
        return None


@dataclass(frozen=True)
class WorkspaceGrantOutcome:
    """What the gate concluded: a bound grant, or a safe refusal message."""

    approved: bool
    message: str
    grant: BrokerGrant | None = None
    granted_root: ClassifiedPath | None = None

    @classmethod
    def denied(cls, message: str) -> WorkspaceGrantOutcome:
        """A refusal the model can act on (never an empty success)."""

        return cls(approved=False, message=message)

    @classmethod
    def granted(
        cls, *, grant: BrokerGrant, root: ClassifiedPath
    ) -> WorkspaceGrantOutcome:
        """An approval bound to one broker grant and its granted host root."""

        return cls(
            approved=True,
            message=WorkspaceGrantMessages.granted(root.folder_name),
            grant=grant,
            granted_root=root,
        )


@dataclass(frozen=True)
class WorkspaceGrantGate:
    """Parks the run to ask the user to grant one host folder.

    ``grants`` is the broker client (the authority on which grants exist);
    ``interrupt_handler`` is the ``langgraph.types.interrupt`` seam, injected in
    tests. ``run_id`` only scopes the deterministic approval id.
    """

    grants: WorkspaceGrantSnapshotReader
    interrupt_handler: Callable[[dict[str, Any]], object] = langgraph_interrupt
    run_id: str | None = None

    _ACTIVE: ClassVar[str] = "active"
    _DIGEST_LENGTH: ClassVar[int] = 16
    _UNSCOPED_RUN: ClassVar[str] = "run"
    _MAX_DISPLAY: ClassVar[int] = 1024
    _MAX_NAME: ClassVar[int] = 255

    async def request(
        self,
        folder: ClassifiedPath,
        *,
        bound_grant_ids: frozenset[str] = frozenset(),
    ) -> WorkspaceGrantOutcome:
        """Ask for ``folder``, then bind the approval to a live broker grant."""

        if not folder.is_host or not folder.segments:
            # A volume root is not a grantable folder; never ask for one.
            return WorkspaceGrantOutcome.denied(HostPathMessages.VOLUME_ROOT)
        resume = WorkspaceGrantResume.parse(
            self.interrupt_handler(self._request(folder).interrupt_payload())
        )
        if not resume.approved:
            return WorkspaceGrantOutcome.denied(WorkspaceGrantMessages.DECLINED)
        root = self._granted_root(resume.root, folder)
        if root is None:
            return WorkspaceGrantOutcome.denied(WorkspaceGrantMessages.UNBOUND)
        try:
            snapshot = await self.grants.grants_snapshot()
        except BrokerError:
            return WorkspaceGrantOutcome.denied(WorkspaceGrantMessages.UNAVAILABLE)
        grant = self._resolve_grant(snapshot, resume.grant_id, bound_grant_ids)
        if grant is None:
            return WorkspaceGrantOutcome.denied(WorkspaceGrantMessages.UNBOUND)
        return WorkspaceGrantOutcome.granted(grant=grant, root=root)

    def _request(self, folder: ClassifiedPath) -> WorkspaceGrantRequest:
        """Build the typed consent request for ``folder``."""

        return WorkspaceGrantRequest(
            approval_id=self.approval_id(folder),
            folder_path=folder.display[: self._MAX_DISPLAY],
            folder_name=folder.folder_name[: self._MAX_NAME],
            platform=folder.flavour,
        )

    def approval_id(self, folder: ClassifiedPath) -> str:
        """Deterministic, path-free approval id for ``folder`` in this run.

        Approval ids reach clients, events and audit rows, so the folder is
        represented by a digest of its comparison key rather than the path
        itself: the same folder always parks on the same id, and no host path is
        persisted to reach it.
        """

        material = "\x00".join((folder.root_key, *folder.segment_keys))
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        run = self.run_id or self._UNSCOPED_RUN
        return (
            f"{WorkspaceGrantValues.APPROVAL_PREFIX}:{run}:"
            f"{digest[: self._DIGEST_LENGTH]}"
        )

    @staticmethod
    def _granted_root(
        root: str | None, folder: ClassifiedPath
    ) -> ClassifiedPath | None:
        """Validate the echoed granted root, or ``None`` to fail closed.

        The root must be a host-absolute path in the same grammar that CONTAINS
        the folder the user was asked about. A narrower or unrelated root would
        mis-root every later read under this mount.
        """

        if root is None:
            return None
        classified = HostPathClassifier.classify(root)
        if classified.kind is not HostPathKind.HOST_ABSOLUTE:
            return None
        if not classified.contains(folder):
            return None
        return classified

    @classmethod
    def _resolve_grant(
        cls,
        snapshot: BrokerGrantSnapshot,
        grant_id: str | None,
        bound_grant_ids: frozenset[str],
    ) -> BrokerGrant | None:
        """Pick the broker grant this approval created, or ``None``.

        The snapshot is the only authority: an echoed ``grant_id`` must appear in
        it, and with no echoed id exactly one newly-appeared grant must exist.
        """

        active = [
            grant
            for grant in snapshot.grants
            if grant.status == cls._ACTIVE and grant.grant_id
        ]
        if grant_id is not None:
            return next((grant for grant in active if grant.grant_id == grant_id), None)
        fresh = [grant for grant in active if grant.grant_id not in bound_grant_ids]
        return fresh[0] if len(fresh) == 1 else None


__all__ = (
    "WorkspaceGrantGate",
    "WorkspaceGrantMessages",
    "WorkspaceGrantOutcome",
    "WorkspaceGrantRequest",
    "WorkspaceGrantResume",
    "WorkspaceGrantSnapshotReader",
    "WorkspaceGrantValues",
)
