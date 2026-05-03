import { ActionBarPrimitive } from "@assistant-ui/react";
import type { AssistantPerformanceMetrics } from "@enterprise-search/api-types";
import type { ReactElement } from "react";
import { CopyIcon } from "../icons/CopyIcon";
import { RetryIcon } from "../icons/RetryIcon";
import { AssistantMessageMetrics } from "./AssistantMessageMetrics";

export function AssistantMessageFooter({
  metrics,
}: {
  metrics: AssistantPerformanceMetrics | null;
}): ReactElement {
  return (
    <div className="aui-assistant-message-footer">
      <ActionBarPrimitive.Root className="aui-assistant-action-bar">
        <ActionBarPrimitive.Copy
          className="aui-footer-icon-button"
          aria-label="Copy response"
          data-tooltip="Copy response"
        >
          <CopyIcon />
        </ActionBarPrimitive.Copy>
        <ActionBarPrimitive.Reload
          className="aui-footer-icon-button"
          aria-label="Retry response"
          data-tooltip="Retry response"
        >
          <RetryIcon />
        </ActionBarPrimitive.Reload>
      </ActionBarPrimitive.Root>
      {metrics ? <AssistantMessageMetrics metrics={metrics} /> : null}
    </div>
  );
}
