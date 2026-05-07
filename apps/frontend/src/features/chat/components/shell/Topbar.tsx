import {
  IconButton,
  StatusPill,
  type StatusTone,
} from "@enterprise-search/design-system";
import type { ModelCatalogModel } from "@enterprise-search/api-types";
import {
  useEffect,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import type { RunUiState, RunUiPhase } from "../../chatRunState";
import { depthLabelForModel, type ThinkingDepth } from "../../depth";
import { LogoMark } from "../thread/LogoMark";
import { Crumb } from "./Crumb";
import { ConversationTitle } from "./ConversationTitle";
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
  /**
   * PR 4.5 — optional render-prop slot. When supplied, replaces the default
   * share `IconButton` with the supplied node (typically `<SharePopover>`).
   * Falls back to the legacy click-to-copy `IconButton` when omitted so the
   * topbar remains usable without the popover wired in.
   */
  shareSlot?: ReactNode;
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
    usagePct,
    onOpenUsage,
    models,
    selectedModel,
    onModelChange,
    depth,
    onDepthChange,
    depthVisible,
    onShare,
    shareSlot,
    onOpenSettings,
    chromeDisabled,
  } = props;

  const tone = TONE_BY_PHASE[runUiState.phase];

  // Announce depth changes to assistive tech without stealing visible
  // chrome. Polite live region so it never interrupts a stream. Skips
  // the initial mount (we don't want to announce the persisted default
  // on every reload). Reads the *latest* selected model at announcement
  // time via a ref so changing models alone doesn't fire an extra
  // announcement, but the model-catalog `depth_label` (PR 3.5 / G3)
  // override still wins when present.
  const [depthAnnouncement, setDepthAnnouncement] = useState("");
  const lastDepthRef = useRef<ThinkingDepth | null>(null);
  const activeModelRef = useRef<ModelCatalogModel | null>(null);
  activeModelRef.current = models.find((m) => m.id === selectedModel) ?? null;
  useEffect(() => {
    const previous = lastDepthRef.current;
    lastDepthRef.current = depth;
    if (previous === null || previous === depth) {
      return;
    }
    setDepthAnnouncement(
      `Depth: ${depthLabelForModel(depth, activeModelRef.current)} — applies to your next message.`,
    );
    const id = window.setTimeout(() => setDepthAnnouncement(""), 2000);
    return () => window.clearTimeout(id);
  }, [depth]);

  return (
    <header
      className="atlas-topbar"
      data-chrome-disabled={chromeDisabled || undefined}
    >
      <span className="sr-only" role="status" aria-live="polite">
        {depthAnnouncement}
      </span>
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
          <UsageMeter pct={usagePct} onOpen={onOpenUsage} />
          {shareSlot ?? (
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
          )}
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
      {/* PR 8.0.2 — model + thinking-depth moved into the composer's
          tools row. The topbar now collapses to a single row matching
          the design's mock. The composer is the canonical anchor for
          run-time controls (model, depth, connectors); the topbar
          carries identity + status only. */}
    </header>
  );
}
