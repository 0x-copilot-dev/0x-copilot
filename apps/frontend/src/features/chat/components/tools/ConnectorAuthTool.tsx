import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import { Button } from "@enterprise-search/design-system";
import { useState, type ReactElement } from "react";
import { formatDateTime, stringValue } from "../../utils/jsonUtils";
import { safeConnectorDisplayName } from "../../utils/toolLabels";
import { ActivityCard } from "../activity/ActivityCard";
import { presentationFromArgs } from "../activity/presentationHelpers";
import { mcpAuthDetails } from "../details/mcpAuthDetails";

export function ConnectorAuthTool({
  args,
  result,
  onConnect,
  onSkip,
  resume,
}: ToolCallMessagePartProps<Record<string, unknown>> & {
  onConnect: (payload: {
    approvalId: string;
    serverId: string;
  }) => Promise<void>;
  onSkip: (payload: { approvalId: string; serverId: string }) => Promise<void>;
}): ReactElement {
  const presentation = presentationFromArgs(args);
  const [pendingAction, setPendingAction] = useState<"connect" | "skip" | null>(
    null,
  );
  const serverId = stringValue(args.server_id);
  const approvalId =
    stringValue(args.approval_id) ?? stringValue(args.action_id) ?? serverId;
  const displayName =
    safeConnectorDisplayName(
      stringValue(args.display_name) ?? stringValue(args.server_name),
    ) ?? "connector";
  const message =
    stringValue(args.message) ??
    `Enterprise Search needs permission to use ${displayName}.`;
  const expiresAt = stringValue(args.expires_at);
  const resolved = result !== undefined;

  async function submit(action: "connect" | "skip"): Promise<void> {
    if (!serverId || !approvalId || resolved || pendingAction !== null) {
      return;
    }
    setPendingAction(action);
    try {
      if (action === "connect") {
        await onConnect({ approvalId, serverId });
      } else {
        await onSkip({ approvalId, serverId });
        const result = {
          approval_id: approvalId,
          approval_kind: "mcp_auth",
          decision: "rejected",
          server_id: serverId,
        };
        resume(result);
      }
    } finally {
      setPendingAction(null);
    }
  }

  return (
    <ActivityCard
      title={
        presentation?.title ??
        (resolved ? `${displayName} connected` : `Connect ${displayName}`)
      }
      status={
        presentation?.status_label ??
        (resolved ? "Done" : "Waiting for permission")
      }
      variant="connector"
      description={presentation?.summary ?? message}
      params={
        expiresAt
          ? [{ label: "Link expires", value: formatDateTime(expiresAt) }]
          : []
      }
      details={mcpAuthDetails(args, result)}
      detailsLabel={presentation?.debug_label ?? "Tool details"}
    >
      {!resolved ? (
        <div className="aui-tool-card__actions">
          <Button
            type="button"
            size="sm"
            disabled={!serverId || !approvalId || pendingAction !== null}
            title={`Connect ${displayName}`}
            onClick={() => void submit("connect")}
          >
            {pendingAction === "connect" ? "Connecting..." : "Connect"}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            disabled={!serverId || !approvalId || pendingAction !== null}
            title={`Skip ${displayName} authentication`}
            onClick={() => void submit("skip")}
          >
            {pendingAction === "skip" ? "Skipping..." : "Not now"}
          </Button>
        </div>
      ) : null}
    </ActivityCard>
  );
}
