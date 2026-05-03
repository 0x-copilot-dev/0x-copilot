import type { ModelCatalogModel } from "@enterprise-search/api-types";
import type { ReactElement, ReactNode } from "react";
import { LogoMark } from "./LogoMark";
import { ModelSelector } from "./ModelSelector";

export function AssistantThread({
  sidebarCollapsed,
  status,
  models,
  selectedModel,
  onModelChange,
  modelDisabled,
  onShare,
  onToggleSidebar,
  children,
}: {
  sidebarCollapsed: boolean;
  status: string;
  models: Array<ModelCatalogModel & { disabled?: boolean }>;
  selectedModel: string;
  onModelChange: (modelId: string) => void;
  modelDisabled?: boolean;
  onShare: () => void;
  onToggleSidebar: () => void;
  children: ReactNode;
}): ReactElement {
  return (
    <section className="aui-chat-panel">
      <header className="aui-chat-header">
        <div className="aui-chat-header__left">
          <button
            className="aui-icon-button"
            type="button"
            aria-label={sidebarCollapsed ? "Show sidebar" : "Hide sidebar"}
            data-tooltip={sidebarCollapsed ? "Show sidebar" : "Hide sidebar"}
            data-tooltip-placement="bottom"
            data-tooltip-align="start"
            onClick={onToggleSidebar}
          >
            {sidebarCollapsed ? "☰" : "◧"}
          </button>
          {sidebarCollapsed ? <LogoMark compact /> : null}
          <ModelSelector
            models={models}
            value={selectedModel}
            onChange={onModelChange}
            disabled={modelDisabled}
          />
        </div>
        <div className="aui-chat-header__actions">
          <span className="aui-status-pill">{status}</span>
          <button
            className="aui-ghost-button"
            type="button"
            title="Copy share link"
            onClick={onShare}
          >
            Share
          </button>
        </div>
      </header>
      <div className="not-prose aui-demo-frame">{children}</div>
    </section>
  );
}
