import type { ToolCallMessagePartProps } from "../../runtime/types";
import { Button } from "@enterprise-search/design-system";
import { useState, type ReactElement } from "react";
import { asRecord, formatDateTime, stringValue } from "../../utils/jsonUtils";
import { safeConnectorDisplayName } from "../../utils/toolLabels";
import { ActivityCard } from "../activity/ActivityCard";
import { presentationFromArgs } from "../activity/presentationHelpers";
import { mcpAuthDetails } from "../details/mcpAuthDetails";

// PR 3.3 — sentinel resolution-reason recorded on the audit row when the
// user clicks Skip on a non-blocking discovery card. Distinguishes
// "user actively skipped the suggestion" from
// "we ran out of OAuth attempts" so SIEM exports can split them.
const MCP_DISCOVERY_SKIPPED_REASON = "mcp_discovery_skipped";

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
  // PR 3.3 — non-blocking discovery card variant. Presence of
  // ``discovery_reason`` flips the card from "blocking auth gate" to
  // "Connect / Skip" suggestion. We never branch on event_type or any
  // string other than this attribute, per apps/frontend/CLAUDE.md.
  const discoveryReason = stringValue(args.discovery_reason);
  const expectedValue = stringValue(args.expected_value);
  const isDiscovery = discoveryReason !== null;
  const message = isDiscovery
    ? (expectedValue ?? `${displayName} could improve this answer.`)
    : (stringValue(args.message) ??
      `Enterprise Search needs permission to use ${displayName}.`);
  const expiresAt = stringValue(args.expires_at);
  const resolved = result !== undefined;
  // Run terminated without a user decision: reducer settles the part
  // with `decision: "cancelled"` so the card stops looking actionable.
  const isCancelled = stringValue(asRecord(result).decision) === "cancelled";

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
        // PR 3.3 — discovery skips carry a sentinel reason so the audit
        // row records "user dismissed the suggestion" rather than the
        // generic ``rejected`` label the blocking flow uses. The wire
        // status remains ``rejected`` because ``approval_resolved``
        // already accepts only approved/rejected/forwarded.
        const result = {
          approval_id: approvalId,
          approval_kind: "mcp_auth",
          decision: "rejected",
          server_id: serverId,
          ...(isDiscovery ? { reason: MCP_DISCOVERY_SKIPPED_REASON } : {}),
        };
        resume(result);
      }
    } finally {
      setPendingAction(null);
    }
  }

  // PR 3.3 — title / status copy diverge between blocking and discovery
  // variants. Discovery is non-blocking, so the status pill reads
  // "Suggested" rather than "Waiting for permission" — the latter
  // implies the run is paused, which would mislead users.
  const fallbackTitle = isDiscovery
    ? resolved
      ? isCancelled
        ? `${displayName} cancelled`
        : `${displayName} connected`
      : `Connect ${displayName}?`
    : resolved
      ? isCancelled
        ? `${displayName} cancelled`
        : `${displayName} connected`
      : `Connect ${displayName}`;
  const fallbackStatus = isDiscovery
    ? resolved
      ? isCancelled
        ? "Cancelled"
        : "Resolved"
      : "Suggested"
    : resolved
      ? isCancelled
        ? "Cancelled"
        : "Done"
      : "Waiting for permission";
  const skipLabel = isDiscovery ? "Skip" : "Not now";
  const skipPendingLabel = isDiscovery ? "Skipping..." : "Skipping...";
  return (
    <ActivityCard
      title={presentation?.title ?? fallbackTitle}
      status={presentation?.status_label ?? fallbackStatus}
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
        <div
          className="aui-tool-card__actions"
          role={isDiscovery ? "status" : undefined}
          data-discovery={isDiscovery ? "true" : undefined}
        >
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
            title={
              isDiscovery
                ? `Skip the ${displayName} suggestion`
                : `Skip ${displayName} authentication`
            }
            onClick={() => void submit("skip")}
          >
            {pendingAction === "skip" ? skipPendingLabel : skipLabel}
          </Button>
        </div>
      ) : null}
    </ActivityCard>
  );
}
