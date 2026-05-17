import type { AssistantPerformanceMetrics } from "@enterprise-search/api-types";
import { CopyIcon, RetryIcon } from "@enterprise-search/chat-surface";
import type { ReactElement } from "react";

import {
  ActionBar,
  ActionBarCopy,
  ActionBarReload,
} from "../../runtime/components";
import { AssistantMessageMetrics } from "./AssistantMessageMetrics";

export function AssistantMessageFooter({
  metrics,
  getText,
  onReload,
  onForkFromHere,
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
  /**
   * PR A3 — "Retry from here" affordance. Forks the conversation from
   * this message into a new owned conversation, then navigates to it.
   * Optional — read-only mounts (recipient view) leave it omitted so
   * the button doesn't render.
   */
  onForkFromHere?: () => void;
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
        {onForkFromHere ? (
          <button
            type="button"
            className="aui-footer-icon-button"
            aria-label="Retry from here in a new chat"
            data-tooltip="Retry from here"
            onClick={onForkFromHere}
          >
            ↗
          </button>
        ) : null}
      </ActionBar>
      {metrics ? <AssistantMessageMetrics metrics={metrics} /> : null}
    </div>
  );
}
