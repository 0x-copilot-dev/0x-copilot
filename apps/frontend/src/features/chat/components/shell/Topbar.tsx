import {
  IconButton,
  StatusPill,
  type StatusTone,
} from "@enterprise-search/design-system";
import type { ModelCatalogModel } from "@enterprise-search/api-types";
import type { ReactElement } from "react";
import type { RunUiState, RunUiPhase } from "../../chatRunState";
import type { ThinkingDepth } from "../../depth";
import { LogoMark } from "../thread/LogoMark";
import { Crumb } from "./Crumb";
import { ConversationTitle } from "./ConversationTitle";
import { ConnectorsPill, type ActiveConnectorGlyph } from "./ConnectorsPill";
import { ModelPill } from "./ModelPill";
import { ThinkingDepthControl } from "./ThinkingDepthControl";
import { UsageMeter } from "./UsageMeter";

export interface TopbarProps {
  /* identity row */
  workspace: string | null;
  folder: string | null;
  title: string | null;
  onRenameTitle?: (next: string) => Promise<void> | void;

  /* run state */
  runUiState: RunUiState;

  /* sidebar / workspace pane */
  sidebarCollapsed: boolean;
  onToggleSidebar: () => void;
  panelOpen: boolean;
  onTogglePanel: () => void;

  /* connectors */
  connectors: ActiveConnectorGlyph[];
  connectorsOpen: boolean;
  onOpenConnectors: () => void;

  /* usage */
  usagePct: number | null;
  onOpenUsage: () => void;

  /* model + depth */
  models: Array<ModelCatalogModel & { disabled?: boolean }>;
  selectedModel: string;
  onModelChange: (id: string) => void;
  depth: ThinkingDepth;
  onDepthChange: (depth: ThinkingDepth) => void;
  depthVisible: boolean;

  /* nav */
  onShare: () => void;
  onOpenSettings: () => void;

  /**
   * When true, all interactive pills become read-only (shared-chat
   * recipient view, PR 6.1). Prevents the model/depth/connector controls
   * from acting on the upstream conversation.
   */
  chromeDisabled?: boolean;
}

const TONE_BY_PHASE: Record<RunUiPhase, StatusTone> = {
  idle: "idle",
  starting: "running",
  working: "running",
  acting: "running",
  writing: "running",
  reasoning: "running",
  waiting_for_permission: "ready",
  terminal: "ready",
};

/**
 * Atlas topbar — pure composition. No fetches, no derived async work.
 *
 * Two rows on desktop: identity (left) + global state pills (right) on
 * row 1; per-run controls (model + depth) on row 2. Below 1100px the
 * second row collapses inline; below 760px the crumb hides and the
 * status pill becomes a dot. Layout decisions live in `styles.css`
 * under `.atlas-topbar`.
 */
export function Topbar(props: TopbarProps): ReactElement {
  const {
    workspace,
    folder,
    title,
    onRenameTitle,
    runUiState,
    sidebarCollapsed,
    onToggleSidebar,
    panelOpen,
    onTogglePanel,
    connectors,
    connectorsOpen,
    onOpenConnectors,
    usagePct,
    onOpenUsage,
    models,
    selectedModel,
    onModelChange,
    depth,
    onDepthChange,
    depthVisible,
    onShare,
    onOpenSettings,
    chromeDisabled,
  } = props;

  const tone = TONE_BY_PHASE[runUiState.phase];

  return (
    <header
      className="atlas-topbar"
      data-chrome-disabled={chromeDisabled || undefined}
    >
      <div className="atlas-topbar__row atlas-topbar__row--identity">
        <div className="atlas-topbar__left">
          <IconButton
            type="button"
            variant="ghost"
            onClick={onToggleSidebar}
            aria-label={sidebarCollapsed ? "Show sidebar" : "Hide sidebar"}
            data-tooltip={sidebarCollapsed ? "Show sidebar" : "Hide sidebar"}
            data-tooltip-placement="bottom"
            aria-pressed={!sidebarCollapsed}
          >
            {sidebarCollapsed ? "☰" : "◧"}
          </IconButton>
          {sidebarCollapsed ? <LogoMark compact /> : null}
          <div className="atlas-topbar__identity">
            <Crumb workspace={workspace} folder={folder} />
            <ConversationTitle
              title={title}
              onRename={chromeDisabled ? undefined : onRenameTitle}
              disabled={chromeDisabled}
            />
          </div>
        </div>
        <div className="atlas-topbar__right">
          <StatusPill
            tone={tone}
            label={runUiState.headerStatus}
            role="status"
            aria-live="polite"
            className="atlas-topbar__status"
          />
          <ConnectorsPill
            active={connectors}
            onOpen={onOpenConnectors}
            open={connectorsOpen}
            disabled={chromeDisabled}
          />
          <UsageMeter pct={usagePct} onOpen={onOpenUsage} />
          <IconButton
            type="button"
            variant="ghost"
            onClick={onShare}
            aria-label="Share this conversation"
            data-tooltip="Share"
            data-tooltip-placement="bottom"
          >
            ⤴
          </IconButton>
          <IconButton
            type="button"
            variant="ghost"
            onClick={onOpenSettings}
            aria-label="Open settings"
            data-tooltip="Settings"
            data-tooltip-placement="bottom"
          >
            ⚙
          </IconButton>
          <IconButton
            type="button"
            variant="ghost"
            onClick={onTogglePanel}
            aria-label={
              panelOpen ? "Close workspace pane" : "Open workspace pane"
            }
            data-tooltip={
              panelOpen ? "Close workspace pane" : "Open workspace pane"
            }
            data-tooltip-placement="bottom"
            aria-pressed={panelOpen}
          >
            ◫
          </IconButton>
        </div>
      </div>
      <div className="atlas-topbar__row atlas-topbar__row--controls">
        <ModelPill
          models={models}
          value={selectedModel}
          onChange={onModelChange}
          disabled={chromeDisabled}
        />
        <ThinkingDepthControl
          value={depth}
          onChange={onDepthChange}
          visible={depthVisible}
          disabled={chromeDisabled}
        />
      </div>
    </header>
  );
}
