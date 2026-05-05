import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import type {
  ApprovalDecision,
  ApprovalForwardTarget,
} from "@enterprise-search/api-types";
import { Badge, Button } from "@enterprise-search/design-system";
import { useState, type ReactElement } from "react";
import { asRecord, stringValue } from "../../utils/jsonUtils";
import {
  humanizeIdentifier,
  mcpApprovalDescription,
  safeConnectorDisplayName,
  toolActionName,
} from "../../utils/toolLabels";
import { ActivityCard } from "../activity/ActivityCard";
import { PresentationResultRows } from "../activity/PresentationResultRows";
import { presentationFromArgs } from "../activity/presentationHelpers";
import { approvalDetailsContent } from "../details/approvalDetailsContent";
import { AskAQuestionTool } from "./AskAQuestionTool";

export function ApprovalTool({
  args,
  result,
  resume,
}: ToolCallMessagePartProps<Record<string, unknown>>): ReactElement {
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
  const isForwarded =
    stringValue(resultRecord.status) === "forwarded" ||
    stringValue(resultRecord.forwarded_to_user_id) !== null;
  const forwardedToUserId = stringValue(resultRecord.forwarded_to_user_id);
  const forwardedAt = stringValue(resultRecord.forwarded_at);

  const [forwarding, setForwarding] = useState(false);
  const [forwardTargetUserId, setForwardTargetUserId] = useState("");

  const submit = (decision: ApprovalDecision): void => {
    resume({ decision, approval_id: approvalId });
  };
  const submitForward = (userId: string): void => {
    const target: ApprovalForwardTarget = {
      kind: "workspace_user",
      user_id: userId,
    };
    resume({
      decision: "forwarded",
      approval_id: approvalId,
      // approval_kind is checked in ChatScreen.tsx::isApprovalResumePayload
      // to route MCP-auth flows separately. Forwarding `mcp_auth` is not
      // supported in v1; the picker is hidden for that kind below.
      approval_kind: approvalKind ?? undefined,
      forward_to: target,
    });
  };

  const approvalStatus = resolved
    ? isForwarded
      ? forwardedToUserId
        ? `Waiting on @${forwardedToUserId}`
        : "Forwarded"
      : "Done"
    : "Waiting for permission";
  const actionName = toolActionName(toolName);
  const approvalTitle = resolved
    ? isForwarded
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
  const approvalDescription =
    resolved && isForwarded && forwardedToUserId
      ? `Forwarded to @${forwardedToUserId}${
          forwardedAt ? ` at ${formatTimeShort(forwardedAt)}` : ""
        }. Waiting on their decision.`
      : baseDescription;
  const cardTitle = presentation?.title ?? approvalTitle;
  const cardDescription = presentation?.summary ?? approvalDescription;
  const cardStatus = presentation?.status_label ?? approvalStatus;
  const cardResult =
    presentation?.result_preview && presentation.result_preview.length > 0 ? (
      <PresentationResultRows rows={presentation.result_preview} />
    ) : undefined;
  // PR 1.4 — only `action` and `mcp_tool` kinds are forwardable in v1.
  // ask_a_question is handled in its own component above; mcp_auth is a
  // user-specific OAuth flow we deliberately don't bounce between users.
  const canForward = !resolved && !isMcpApproval && approvalKind !== "mcp_auth";
  return (
    <ActivityCard
      title={cardTitle}
      status={cardStatus}
      variant="approval"
      description={cardDescription}
      params={
        isMcpApproval
          ? [
              ...(riskLevel
                ? [{ label: "Risk", value: <Badge>{riskLevel}</Badge> }]
                : []),
              ...(readOnly !== null
                ? [
                    {
                      label: "Access",
                      value: readOnly ? "Read-only" : "May change data",
                    },
                  ]
                : []),
            ]
          : []
      }
      result={cardResult}
      details={approvalDetailsContent(args, result)}
      detailsLabel={presentation?.debug_label ?? "Tool details"}
    >
      {!resolved ? (
        <div className="aui-tool-card__actions">
          <Button
            type="button"
            size="sm"
            title={isMcpApproval ? "Allow this connector action" : "Approve"}
            onClick={() => submit("approved")}
          >
            {isMcpApproval ? "Allow once" : "Approve"}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            title={isMcpApproval ? "Deny this connector action" : "Reject"}
            onClick={() => submit("rejected")}
          >
            {isMcpApproval ? "Deny" : "Reject"}
          </Button>
          {canForward ? (
            forwarding ? (
              <ForwardPicker
                value={forwardTargetUserId}
                onChange={setForwardTargetUserId}
                onCancel={() => {
                  setForwarding(false);
                  setForwardTargetUserId("");
                }}
                onConfirm={() => {
                  const trimmed = forwardTargetUserId.trim();
                  if (trimmed.length === 0) {
                    return;
                  }
                  submitForward(trimmed);
                }}
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
      ) : null}
    </ActivityCard>
  );
}

// PR 1.4 — minimal inline picker. v1 takes a free-text user_id; the
// workspace-member directory picker comes with W3.1's @-mention component.
// Server-side validators reject self-forward and cross-org targets so a
// hand-typed id can't escape the workspace boundary.
function ForwardPicker({
  value,
  onChange,
  onCancel,
  onConfirm,
}: {
  value: string;
  onChange: (next: string) => void;
  onCancel: () => void;
  onConfirm: () => void;
}): ReactElement {
  return (
    <div className="aui-tool-card__forward-picker">
      <input
        type="text"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="user_id (e.g. marcus)"
        aria-label="Forward to user id"
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            onConfirm();
          } else if (event.key === "Escape") {
            event.preventDefault();
            onCancel();
          }
        }}
      />
      <Button
        type="button"
        size="sm"
        title="Forward this decision"
        onClick={onConfirm}
        disabled={value.trim().length === 0}
      >
        Forward
      </Button>
      <Button
        type="button"
        size="sm"
        variant="secondary"
        title="Cancel forwarding"
        onClick={onCancel}
      >
        Cancel
      </Button>
    </div>
  );
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
