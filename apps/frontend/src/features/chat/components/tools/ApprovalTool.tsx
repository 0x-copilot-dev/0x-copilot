import type { ToolCallMessagePartProps } from "../../runtime/types";
import type {
  ApprovalDecision,
  ApprovalForwardTarget,
  McpApprovalCategory,
  McpApprovalParam,
  McpApprovalReasonCode,
} from "@enterprise-search/api-types";
import { Button } from "@enterprise-search/design-system";
import {
  Fragment,
  useEffect,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import { useApprovalFocus } from "../../approval/ApprovalFocusContext";
import { asRecord, stringValue } from "../../utils/jsonUtils";
import {
  humanizeIdentifier,
  mcpApprovalActionTitle,
  mcpApprovalCategory,
  mcpApprovalDescription,
  mcpApprovalReason,
  mcpApprovalReassurance,
  safeConnectorDisplayName,
  toolActionName,
} from "../../utils/toolLabels";
import { ActivityCard } from "../activity/ActivityCard";
import { ApprovalCard } from "../activity/ApprovalCard";
import {
  ApprovalReceipt,
  type ApprovalReceiptKind,
} from "../activity/ApprovalReceipt";
import { PresentationResultRows } from "../activity/PresentationResultRows";
import { presentationFromArgs } from "../activity/presentationHelpers";
import { approvalDetailsContent } from "../details/approvalDetailsContent";
import { AskAQuestionTool } from "./AskAQuestionTool";
import { MentionLabel } from "../../../workspace/MentionLabel";
import {
  WorkspaceMemberPicker,
  type WorkspaceMember,
  type WorkspaceMemberLoader,
} from "./WorkspaceMemberPicker";

export interface ApprovalToolExtraProps {
  /** Optional: load workspace members for the forward picker. Default
   * is a passthrough loader that mirrors the typed user_id; the
   * production loader hits ``GET /v1/workspace/members?q=`` and lands
   * in a follow-up alongside the @-mention picker (W3.1). */
  loadWorkspaceMembers?: WorkspaceMemberLoader;
  /** Optional: the caller's own user_id, excluded from the forward
   * picker so they can't pick themselves (server also rejects with 422). */
  selfUserId?: string;
  /** PR 4.4.6.4 — invoked when the user clicks "Undo" inside the 60s
   * reversibility window. Caller posts to
   * ``/v1/agent/approvals/{approval_id}/undo``. Returns the audited
   * timestamps so the caller can update the receipt's persisted state.
   * Side-channel to the approve/reject ``resume`` flow. */
  onRequestUndo?: (
    approvalId: string,
  ) => Promise<{ undo_requested_at: string }>;
}

export function ApprovalTool({
  args,
  result,
  resume,
  loadWorkspaceMembers,
  selfUserId,
  onRequestUndo,
}: ToolCallMessagePartProps<Record<string, unknown>> &
  ApprovalToolExtraProps): ReactElement {
  const presentation = presentationFromArgs(args);
  const approvalId = String(args.approval_id ?? "");
  const toolName = stringValue(args.tool_name);
  const serverName = stringValue(args.server_name);
  const displayName =
    safeConnectorDisplayName(stringValue(args.display_name) ?? serverName) ??
    (serverName
      ? safeConnectorDisplayName(humanizeIdentifier(serverName))
      : null);
  const riskLevel = stringValue(args.risk_level);
  const readOnly = typeof args.read_only === "boolean" ? args.read_only : null;
  const approvalKind =
    stringValue(args.approval_kind) ?? stringValue(args.kind) ?? null;
  const isMcpApproval = approvalKind === "mcp_tool";
  const isAskAQuestion = approvalKind === "ask_a_question";
  if (isAskAQuestion) {
    return (
      <AskAQuestionTool
        args={args}
        approvalId={approvalId}
        resolved={result !== undefined}
        result={result}
        presentation={presentation}
        resume={resume}
      />
    );
  }
  const resolved = result !== undefined;
  // PR 1.4 — read forwarded annotations off the result (set by the
  // approval_forwarded reducer branch). When present, we render a
  // "Waiting on @marcus" pill instead of the resolved record.
  const resultRecord = asRecord(result);
  // Run-cancelled / failed: the reducer's
  // `markPendingInteractionsCancelled` settles unresolved approval parts
  // with `decision: "cancelled"`. Render as a quiet "Cancelled" pill so
  // the card stops looking actionable.
  const isCancelled = stringValue(resultRecord.decision) === "cancelled";
  const isForwarded =
    stringValue(resultRecord.status) === "forwarded" ||
    stringValue(resultRecord.forwarded_to_user_id) !== null;
  const forwardedToUserId = stringValue(resultRecord.forwarded_to_user_id);
  const forwardedByUserId = stringValue(resultRecord.forwarded_by_user_id);
  const forwardedAt = stringValue(resultRecord.forwarded_at);
  // PR 3.3 — chain-final fields, populated by ``annotateChainParent``
  // in chatModel/approval.ts when the leaf approval resolves. The
  // wire-level parent status remains ``forwarded``; these slots are
  // additive so the parent card transforms in place into the
  // chain-final inline record.
  const chainLeafDecision = stringValue(resultRecord.chain_leaf_decision);
  const chainLeafDecidedByUserId = stringValue(
    resultRecord.chain_leaf_decided_by_user_id,
  );
  const chainLeafDecidedAt = stringValue(resultRecord.chain_leaf_decided_at);
  const isChainFinal = isForwarded && chainLeafDecision !== null;

  const [forwarding, setForwarding] = useState(false);

  const submit = (decision: ApprovalDecision): void => {
    resume({ decision, approval_id: approvalId });
  };

  // PR 2.2 — register with the ApprovalFocusContext so a global ⌘↩
  // keymap binding can approve the topmost unresolved card without
  // mouse focus / scroll-into-view ceremony. Only register while the
  // approval is unresolved AND the user can act on it (we exclude
  // ask_a_question, which has its own answer flow, plus already-
  // forwarded cards waiting on someone else).
  const approvalFocus = useApprovalFocus();
  useEffect(() => {
    const canConsent =
      !resolved && approvalId.length > 0 && !isAskAQuestion && !isForwarded;
    if (!canConsent) {
      return;
    }
    approvalFocus.register({
      approvalId,
      approve: () => submit("approved"),
    });
    return () => approvalFocus.unregister(approvalId);
    // `submit` closes over `resume` + `approvalId`; we intentionally
    // do not list `submit` in the dep array (it's recreated each
    // render) — re-register cycles each render would churn the order.
  }, [approvalId, isAskAQuestion, isForwarded, resolved, approvalFocus]);

  const submitForward = (member: WorkspaceMember): void => {
    const target: ApprovalForwardTarget = {
      kind: "workspace_user",
      user_id: member.user_id,
    };
    resume({
      decision: "forwarded",
      approval_id: approvalId,
      // approval_kind is checked in ChatScreen.tsx::isApprovalResumePayload
      // to route MCP-auth flows separately. PR 1.4.1 Phase C narrows the
      // server contract: mcp_auth and ask_a_question approvals are not
      // forwardable; the picker is hidden for those kinds below.
      approval_kind: approvalKind ?? undefined,
      forward_to: target,
    });
  };

  // PR 3.3 — When the chain-final fields land we re-render the parent's
  // status pill into "Approved by @marcus" / "Rejected by @marcus" so
  // scrollback reads as a single coherent record without a modal.
  const chainLeafLabel =
    chainLeafDecision === "approved"
      ? "Approved by"
      : chainLeafDecision === "rejected"
        ? "Rejected by"
        : null;
  const approvalStatus = resolved
    ? isCancelled
      ? "Cancelled"
      : isChainFinal && chainLeafLabel !== null
        ? chainLeafDecidedByUserId
          ? `${chainLeafLabel} @${chainLeafDecidedByUserId}`
          : chainLeafLabel
        : isForwarded
          ? forwardedToUserId
            ? `Waiting on @${forwardedToUserId}`
            : "Forwarded"
          : "Done"
    : "Waiting for permission";
  const actionName = toolActionName(toolName);
  const approvalTitle = resolved
    ? isCancelled
      ? "Approval cancelled"
      : isChainFinal && chainLeafDecision === "approved"
        ? "Approved"
        : isChainFinal && chainLeafDecision === "rejected"
          ? "Rejected"
          : isForwarded
            ? "Forwarded for sign-off"
            : isMcpApproval
              ? "Permission approved"
              : "Approval resolved"
    : isMcpApproval
      ? `Allow ${displayName ?? "connector"} ${actionName}?`
      : "Approval requested";
  const baseDescription = isMcpApproval
    ? mcpApprovalDescription(displayName, actionName, readOnly, args.message)
    : String(args.message ?? args.reason ?? approvalId);
  // PR 3.3 — Chain-final renders a structured `<dl>` so scrollback
  // reads "Approved by @marcus at 10:45 / Forwarded by @sarah at 10:41".
  // We render React (not a plain string) when chain-final is active so
  // ``<MentionLabel>`` resolves user_ids to display names. ActivityCard
  // accepts ReactNode for description.
  const chainFinalDescription = isChainFinal ? (
    <dl className="atlas-approval-chain">
      <div className="atlas-approval-chain__row">
        <dt>{chainLeafLabel}</dt>
        <dd>
          <MentionLabel userId={chainLeafDecidedByUserId} />
          {chainLeafDecidedAt ? (
            <Fragment>
              {" at "}
              {formatTimeShort(chainLeafDecidedAt)}
            </Fragment>
          ) : null}
        </dd>
      </div>
      {forwardedByUserId ? (
        <div className="atlas-approval-chain__row">
          <dt>Forwarded by</dt>
          <dd>
            <MentionLabel userId={forwardedByUserId} />
            {forwardedAt ? (
              <Fragment>
                {" at "}
                {formatTimeShort(forwardedAt)}
              </Fragment>
            ) : null}
          </dd>
        </div>
      ) : null}
    </dl>
  ) : null;
  const forwardedDescription =
    resolved && isForwarded && forwardedToUserId ? (
      <span>
        Forwarded to <MentionLabel userId={forwardedToUserId} />
        {forwardedAt ? <> at {formatTimeShort(forwardedAt)}</> : null}. Waiting
        on their decision.
      </span>
    ) : null;
  const approvalDescription = isChainFinal
    ? chainFinalDescription
    : forwardedDescription !== null
      ? forwardedDescription
      : baseDescription;
  const cardTitle = presentation?.title ?? approvalTitle;
  const cardDescription = presentation?.summary ?? approvalDescription;
  const cardStatus = presentation?.status_label ?? approvalStatus;
  const cardResult =
    presentation?.result_preview && presentation.result_preview.length > 0 ? (
      <PresentationResultRows rows={presentation.result_preview} />
    ) : undefined;
  const detailsLabel = presentation?.debug_label ?? "Tool details";
  const detailsBody = approvalDetailsContent(args, result);
  // PR 1.4 — only `action` and `mcp_tool` kinds are forwardable in v1.
  // ask_a_question is handled in its own component above; mcp_auth is a
  // user-specific OAuth flow we deliberately don't bounce between users.
  const canForward = !resolved && !isMcpApproval && approvalKind !== "mcp_auth";

  // PR 4.4.6.1 — settled approvals collapse to a one-line receipt so
  // scrollback stays readable. The full args + result remain in the
  // <details> dropdown for auditing.
  if (resolved) {
    if (isMcpApproval && !isCancelled && !isForwarded) {
      const receiptKind: ApprovalReceiptKind = isChainFinal
        ? chainLeafDecision === "rejected"
          ? "chain-rejected"
          : "chain-approved"
        : stringValue(resultRecord.decision) === "rejected"
          ? "rejected"
          : "approved";
      const receiptTitle =
        presentation?.title ??
        (displayName
          ? `${capitalize(actionName)} ${displayName}`
          : "Connector action");
      // PR 4.4.6.4 — only approved + reversible decisions carry an
      // undo window. The decision response stashes ``undo_expires_at``
      // on the result record; local state tracks the optimistic flip
      // after a successful POST so scrollback shows "Undo requested".
      const undoUntilIso = stringValue(resultRecord.undo_expires_at);
      const undoRequestedFromServer = stringValue(
        resultRecord.undo_requested_at,
      );
      return (
        <UndoableReceipt
          kind={receiptKind}
          title={receiptTitle}
          details={detailsBody}
          detailsLabel={detailsLabel}
          undoUntilIso={receiptKind === "approved" ? undoUntilIso : null}
          undoRequestedFromServer={undoRequestedFromServer}
          approvalId={approvalId}
          onRequestUndo={onRequestUndo}
        />
      );
    }
    // Forwarded / cancelled / non-MCP approvals keep the existing
    // ActivityCard rendering — those flows have richer chain UX
    // (MentionLabel, chain-final dl) that the receipt doesn't model yet.
    return (
      <ActivityCard
        title={cardTitle}
        status={cardStatus}
        variant="approval"
        description={cardDescription}
        result={cardResult}
        details={detailsBody}
        detailsLabel={detailsLabel}
      />
    );
  }

  // Unresolved + MCP — the redesigned consent surface.
  if (isMcpApproval) {
    // PR 4.4.6.2 — server-supplied structured payload wins; falls
    // through to the Phase-1 synthesisers when the wire is silent.
    const serverVendor = stringValue(args.vendor);
    const serverCategory = stringValue(
      args.category,
    ) as McpApprovalCategory | null;
    const serverReasonCode = stringValue(
      args.reason_code,
    ) as McpApprovalReasonCode | null;
    const serverParams = readApprovalParams(args.params);
    const category = mcpApprovalCategory(displayName, readOnly, {
      vendor: serverVendor,
      category: serverCategory,
    });
    const titleCopy = mcpApprovalActionTitle(toolName, displayName, readOnly);
    const reasonCopy = mcpApprovalReason(
      readOnly,
      riskLevel,
      args.message,
      serverReasonCode,
    );
    const reassuranceCopy = mcpApprovalReassurance(readOnly);
    const params =
      serverParams.length > 0
        ? serverParams.map((row) => ({ label: row.label, value: row.value }))
        : [
            ...(riskLevel
              ? [{ label: "Risk", value: capitalize(riskLevel) }]
              : []),
            ...(readOnly !== null
              ? [
                  {
                    label: "Access",
                    value: readOnly ? "Read-only" : "May change data",
                  },
                ]
              : []),
          ];
    return (
      <ApprovalCard
        title={presentation?.title ?? titleCopy}
        reason={presentation?.summary ?? reasonCopy}
        category={category}
        params={params}
        result={cardResult}
        reassurance={reassuranceCopy}
        details={detailsBody}
        detailsLabel={detailsLabel}
        actions={
          <>
            <Button
              type="button"
              size="sm"
              title="Allow this connector action once"
              onClick={() => submit("approved")}
            >
              ✓ Approve & continue
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              title="Deny this connector action"
              onClick={() => submit("rejected")}
            >
              Skip this step
            </Button>
          </>
        }
      />
    );
  }

  // Unresolved non-MCP approvals (forwardable actions). These keep the
  // ActivityCard surface for now because they need the WorkspaceMemberPicker
  // inline; redesign tracks separately.
  return (
    <ActivityCard
      title={cardTitle}
      status={cardStatus}
      variant="approval"
      description={cardDescription}
      result={cardResult}
      details={detailsBody}
      detailsLabel={detailsLabel}
    >
      <div className="aui-tool-card__actions">
        <Button
          type="button"
          size="sm"
          title="Approve"
          onClick={() => submit("approved")}
        >
          Approve
        </Button>
        <Button
          type="button"
          size="sm"
          variant="secondary"
          title="Reject"
          onClick={() => submit("rejected")}
        >
          Reject
        </Button>
        {canForward ? (
          forwarding ? (
            <WorkspaceMemberPicker
              loadMembers={loadWorkspaceMembers}
              excludeUserIds={
                selfUserId !== undefined ? [selfUserId] : undefined
              }
              onPick={(member) => {
                submitForward(member);
                setForwarding(false);
              }}
              onCancel={() => setForwarding(false)}
            />
          ) : (
            <Button
              type="button"
              size="sm"
              variant="secondary"
              title="Forward this decision to a teammate"
              onClick={() => setForwarding(true)}
            >
              Approve & forward to…
            </Button>
          )
        ) : null}
      </div>
    </ActivityCard>
  );
}

function capitalize(value: string): string {
  return value.length === 0
    ? value
    : value.charAt(0).toUpperCase() + value.slice(1);
}

// PR 4.4.6.4 — local-state wrapper around ApprovalReceipt that owns
// the optimistic undo flip. We keep this in ApprovalTool's module
// (not in ApprovalReceipt) because the receipt is also used in
// audit / inbox surfaces that don't want the side-channel POST wired.
function UndoableReceipt({
  kind,
  title,
  details,
  detailsLabel,
  undoUntilIso,
  undoRequestedFromServer,
  approvalId,
  onRequestUndo,
}: {
  kind: ApprovalReceiptKind;
  title: string;
  details: ReactNode;
  detailsLabel: string;
  undoUntilIso: string | null;
  undoRequestedFromServer: string | null;
  approvalId: string;
  onRequestUndo?: (
    approvalId: string,
  ) => Promise<{ undo_requested_at: string }>;
}): ReactElement {
  const [pending, setPending] = useState(false);
  const [requestedAtIso, setRequestedAtIso] = useState<string | null>(
    undoRequestedFromServer,
  );

  async function handleUndo(): Promise<void> {
    if (pending || onRequestUndo === undefined) {
      return;
    }
    try {
      setPending(true);
      const out = await onRequestUndo(approvalId);
      setRequestedAtIso(out.undo_requested_at);
    } catch {
      // FE recovery: leave the button enabled. The server is
      // authoritative on window expiry; double-click is harmless
      // (audit captures every legitimate request).
    } finally {
      setPending(false);
    }
  }

  return (
    <ApprovalReceipt
      kind={kind}
      title={title}
      details={details}
      detailsLabel={detailsLabel}
      undoUntil={undoUntilIso !== null ? new Date(undoUntilIso) : null}
      undoRequestedAt={
        requestedAtIso !== null ? new Date(requestedAtIso) : null
      }
      onUndo={onRequestUndo !== undefined ? () => void handleUndo() : undefined}
      undoPending={pending}
    />
  );
}

// PR 4.4.6.2 — defensively read server-supplied ``params`` from the
// approval event's args bag. The wire is JsonObject; we trust only
// shape, not values, and cap at 6 rows to mirror the server's
// ``APPROVAL_MAX_PARAMS``.
function readApprovalParams(value: unknown): McpApprovalParam[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const out: McpApprovalParam[] = [];
  for (const item of value) {
    if (out.length >= 6) {
      break;
    }
    if (item === null || typeof item !== "object") {
      continue;
    }
    const record = item as Record<string, unknown>;
    const label = stringValue(record.label);
    const display = stringValue(record.value);
    if (!label || !display) {
      continue;
    }
    const hint = stringValue(record.hint);
    out.push({ label, value: display, hint });
  }
  return out;
}

function formatTimeShort(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], {
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}
