"""The three commit-lifecycle contracts that survived PRD-09b's executor.

PRD-09b landed a self-contained "gated commit executor" here: a server-side edit
merger, an idempotency ledger, a connector protocol, event/audit sinks, and
:class:`!SurfaceCommitExecutor` sequencing them. It was **never constructed
outside tests** — it was written for a send-connector that did not exist, and by
the time one did, every one of its jobs had a wired owner elsewhere:

* the merge — ``runtime_worker.handlers.approval._apply_edits_to_draft`` merges
  ``approve_with_edits`` deltas into the server-held draft version;
* the idempotency claim + precondition re-read + drift abort + audit —
  ``runtime_worker.handlers.stage_commit``, which re-declares the very same
  audit action strings (``surface.commit.committed`` /
  ``surface.commit.aborted_precondition_drift``) plus a ``.failed`` third;
* the side effect — ``agent_runtime.effects.dispatch.EffectDispatchCoordinator``
  through ``runtime_worker.mcp_effect_executor.McpEffectExecutor``. Even v2's own
  ``surfaces_v2.commit_engine`` shed its executor for that seam and kept only
  request types.

Carrying a second, unreachable copy of an **action-safety** core is worse than
carrying dead code generally: it invites a future caller to route a real send
through the arm nothing exercises. So the executor and its ports are gone, and
what remains is only what live code imports — three contracts, no behaviour:

* :class:`SurfaceEdits` — reviewer edit **deltas** at the API edge
  (``ApprovalDecisionRequest.edits``, ``RuntimeApprovalResolvedCommand.edits``).
  It lives
  in the domain rather than in ``runtime_api.schemas`` because the worker, not
  the API, applies it. ``extra`` is forbidden by :class:`RuntimeContract`, so a
  client cannot smuggle a ``target_connector`` / ``tool_name`` / ``run_id``
  override in through the edit object; the base is always the server-held record.
* :class:`RemoteState` — the opaque precondition token a connector captures at
  propose time and re-reads at commit time.
* :class:`ConnectorCommitResult` — the outcome of a connector's side-effecting
  call.

The last two are the shared vocabulary of the v2 staged-write connector seam
(``surfaces_v2.mcp_connector.McpDraftSendConnector``); they stay here because
that is where its importers point.
"""

from __future__ import annotations

from pydantic import Field

from agent_runtime.execution.contracts import JsonObject, RuntimeContract


class SurfaceEdits(RuntimeContract):
    """Reviewer edit deltas applied server-side onto a pending proposal.

    Byte-mirror of the frozen api-types contract (PRD-09a):
    ``{ fields?: Record<string,string>, body?: string, accepted_hunk_ids?: string[] }``.

    The client sends **deltas only** — never a merged final artifact.
    """

    fields: dict[str, str] | None = None
    body: str | None = None
    accepted_hunk_ids: tuple[str, ...] | None = None


class RemoteState(RuntimeContract):
    """Opaque precondition token captured at propose-time and re-read at commit-time.

    Equality is structural: any difference between the captured token and the
    freshly re-read token is drift. ``version`` fits draft-send (monotonic draft
    version); ``fingerprint`` fits record writes (a hash of the captured fields).
    A connector may populate either or both, or neither when it has no
    precondition source — in which case the drift check is skipped.
    """

    version: int | None = None
    fingerprint: str | None = None


class ConnectorCommitResult(RuntimeContract):
    """Outcome of the connector's side-effecting call.

    ``detail`` is deliberately opt-in: raw connector output is UNTRUSTED and the
    live MCP connector leaves it empty rather than echoing it into an event.
    """

    status: str = "sent"
    external_ref: str | None = None
    detail: JsonObject = Field(default_factory=dict)
