import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import type { ApprovalDecision } from "@enterprise-search/api-types";
import { Badge, Button } from "@enterprise-search/design-system";
import type { ReactElement } from "react";
import { stringValue } from "../../utils/jsonUtils";
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
  const submit = (decision: ApprovalDecision): void => {
    resume({ decision, approval_id: approvalId });
  };
  const approvalStatus = resolved ? "Done" : "Waiting for permission";
  const actionName = toolActionName(toolName);
  const approvalTitle = resolved
    ? isMcpApproval
      ? "Permission approved"
      : "Approval resolved"
    : isMcpApproval
      ? `Allow ${displayName ?? "connector"} ${actionName}?`
      : "Approval requested";
  const approvalDescription = isMcpApproval
    ? mcpApprovalDescription(displayName, actionName, readOnly, args.message)
    : String(args.message ?? args.reason ?? approvalId);
  const cardTitle = presentation?.title ?? approvalTitle;
  const cardDescription = presentation?.summary ?? approvalDescription;
  const cardStatus = presentation?.status_label ?? approvalStatus;
  const cardResult =
    presentation?.result_preview && presentation.result_preview.length > 0 ? (
      <PresentationResultRows rows={presentation.result_preview} />
    ) : undefined;
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
        </div>
      ) : null}
    </ActivityCard>
  );
}
