import type { AssistantPerformanceMetrics } from "@enterprise-search/api-types";
import type { ReactElement } from "react";
import {
  ActionBar,
  ActionBarCopy,
  ActionBarReload,
} from "../../runtime/components";
import { CopyIcon } from "../icons/CopyIcon";
import { RetryIcon } from "../icons/RetryIcon";
import { AssistantMessageMetrics } from "./AssistantMessageMetrics";

export function AssistantMessageFooter({
  metrics,
  getText,
  onReload,
}: {
  metrics: AssistantPerformanceMetrics | null;
  /** Lazily produces the copy-target text. Called only on click. */
  getText: () => string;
  /**
   * Footer Reload (regenerate) handler. Optional — when omitted the
   * Reload button is hidden so read-only renders (preview / shared
   * thread) don't show an action that wouldn't work.
   */
  onReload?: () => void;
}): ReactElement {
  return (
    <div className="aui-assistant-message-footer">
      <ActionBar className="aui-assistant-action-bar">
        <ActionBarCopy
          className="aui-footer-icon-button"
          aria-label="Copy response"
          data-tooltip="Copy response"
          getText={getText}
        >
          <CopyIcon />
        </ActionBarCopy>
        {onReload ? (
          <ActionBarReload
            className="aui-footer-icon-button"
            aria-label="Retry response"
            data-tooltip="Retry response"
            onReload={onReload}
          >
            <RetryIcon />
          </ActionBarReload>
        ) : null}
      </ActionBar>
      {metrics ? <AssistantMessageMetrics metrics={metrics} /> : null}
    </div>
  );
}
