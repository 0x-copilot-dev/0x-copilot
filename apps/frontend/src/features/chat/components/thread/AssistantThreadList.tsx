import {
  ThreadListItemPrimitive,
  ThreadListPrimitive,
} from "@assistant-ui/react";
import type { Conversation } from "@enterprise-search/api-types";
import type { ReactElement } from "react";
import { LogoMark } from "./LogoMark";

export function AssistantThreadList({
  collapsed,
  conversations,
  loading,
  activeRunId,
  onOpenSettings,
  onRefresh,
}: {
  collapsed: boolean;
  conversations: Conversation[];
  loading: boolean;
  activeRunId: string | null;
  onOpenSettings: () => void;
  onRefresh: () => void;
}): ReactElement {
  return (
    <aside
      className="aui-sidebar"
      data-collapsed={collapsed ? "true" : undefined}
      aria-label="Conversation history"
      aria-hidden={collapsed}
    >
      <div className="aui-sidebar__header">
        <LogoMark />
        <button
          className="aui-icon-button"
          type="button"
          aria-label="Refresh conversations"
          data-tooltip="Refresh conversations"
          data-tooltip-placement="bottom"
          data-tooltip-align="end"
          onClick={onRefresh}
        >
          ↻
        </button>
      </div>
      <ThreadListPrimitive.Root className="aui-thread-list">
        <ThreadListPrimitive.New
          className="aui-new-thread"
          disabled={activeRunId !== null}
          title={
            activeRunId === null
              ? "Start a new thread"
              : "Stop the current response before starting a new thread"
          }
        >
          New Thread
        </ThreadListPrimitive.New>
        {loading ? (
          <p className="aui-sidebar__note">Loading history...</p>
        ) : null}
        {!loading && conversations.length === 0 ? (
          <p className="aui-sidebar__note">No threads yet.</p>
        ) : null}
        <ThreadListPrimitive.Items>
          {() => (
            <ThreadListItemPrimitive.Root className="aui-thread-list-item">
              <ThreadListItemPrimitive.Trigger
                className="aui-thread-list-item__trigger"
                disabled={activeRunId !== null}
                title={
                  activeRunId === null
                    ? "Open thread"
                    : "Stop the current response before switching threads"
                }
              >
                <ThreadListItemPrimitive.Title />
              </ThreadListItemPrimitive.Trigger>
            </ThreadListItemPrimitive.Root>
          )}
        </ThreadListPrimitive.Items>
      </ThreadListPrimitive.Root>
      <div className="aui-sidebar__footer">
        <button
          className="aui-sidebar-settings"
          type="button"
          title="Open settings"
          onClick={onOpenSettings}
        >
          <span aria-hidden="true">⚙</span>
          Settings
        </button>
      </div>
    </aside>
  );
}
