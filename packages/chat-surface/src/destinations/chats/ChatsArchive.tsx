// Chats — archive destination component (desktop redesign, Phase 4 · PR-4.2).
//
// Source: docs/plan/desktop-redesign/phase-4/PRD.md §3 (US-4.1),
// FR-4.1..4.9, §8 (test map) and
// docs/plan/desktop-redesign/design-reference/DESIGN-SPEC.md §3 (Chats:
// pinned / recent / archived sections; row = title + status chip
// [running/done/paused/archived] + preview + mono model + time;
// "New chat" → Run; reopen → Run) + §9 (4-state machine, a11y).
//
// This is a PURE-PRESENTATION component (FR-4.3): it takes an already
// bucketed `ChatsArchive` payload wrapped in a `SectionResult` plus
// `onReopen` / `onNewChat` callbacks, and renders the shared `.pg` list
// surface with the mandatory 4-state machine (loading / error+Retry /
// empty / ready). It performs NO data fetching, NO routing — a row click
// (or Enter/Space) invokes `onReopen(conversationId)` and the host binder
// (PR-4.3) translates that into `ArtifactRoute.run` (reopen → Run);
// "New chat" invokes `onNewChat()` and the host opens Run on a fresh
// conversation. It renders no inline thread canvas (FR-4.7).
//
// The archive is pre-bucketed by the host binder from
// `/v1/agent/conversations` (incl. archived) — see api-types `chats.ts`.

import { useState } from "react";
import type { CSSProperties, ReactElement, ReactNode } from "react";

import type {
  ChatArchiveRow,
  ChatArchiveStatus,
  ChatsArchive as ChatsArchiveData,
  ConversationId,
  SectionResult,
} from "@0x-copilot/api-types";
import { Button } from "@0x-copilot/design-system";

import { Icon } from "../../icons/Icon";
import { BrandMark } from "../../shell/BrandMark";
import { EmptyState } from "../../shell/EmptyState";
import { StatusPill, type StatusTone } from "../../shell/StatusPill";
import { statusTone as runStatusTone } from "../../shell/statusTone";
import { formatRelativeTime } from "../../util/time";
import { PageLead } from "../_shared/PageLead";
import { Row } from "../_shared/Row";
import { RowList } from "../_shared/RowList";
import { SectionHeader } from "../_shared/SectionHeader";

/** The `.pg-lead` intro copy for the Chats surface. */
export const CHATS_LEAD_COPY =
  "Every conversation with the agent — each chat is a run you can reopen, continue, or archive.";

// ===========================================================================
// Section order (DESIGN-SPEC §3 — Chats: pinned / recent / archived)
// ===========================================================================

/** Section keys of the bucketed `ChatsArchive`, in DESIGN-SPEC §3 order. */
export const CHATS_SECTION_ORDER = ["pinned", "recent", "archived"] as const;

/** One of the three archive sections. */
export type ChatsSectionKey = (typeof CHATS_SECTION_ORDER)[number];

const SECTION_HEADINGS: Readonly<Record<ChatsSectionKey, string>> = {
  pinned: "Pinned",
  recent: "Recent",
  archived: "Archived · history",
};

// ===========================================================================
// Status → tone / label (DESIGN-SPEC §9 — single-accent discipline)
// ===========================================================================
//
// running → jade (`ok`); paused → amber (`warning`); done / archived →
// muted. Only `--color-accent` is reserved for interactive accent; status
// chips carry semantic tone, never decorative colour.

// Delegate to the shell's status-tone SSOT so Chats and Activity can't disagree
// (PRD-B). The design maps done → success (jade), not muted/grey.
function statusTone(status: ChatArchiveStatus): StatusTone {
  return runStatusTone(status).tone;
}

// NOTE (PRD-02): the former local `statusLabel` is deleted. The chip label comes
// from the shell SSOT `runStatusTone(status).label` (already computed as
// `presentation` in the row view) — one lowercase vocabulary shared with
// Activity, matching the design's `.chip` text.

// ===========================================================================
// Public props
// ===========================================================================

export interface ChatsArchiveProps {
  /**
   * Server-projected, pre-bucketed archive result (FR-4.2 4-state driver):
   *   * `null`                       → loading skeleton (`data-state="loading"`)
   *   * `status === "error"`         → error empty-state + Retry
   *   * `status === "unavailable"`   → "not enabled" empty-state
   *   * `status === "ok"` + 0 rows   → per-view empty copy ("Start your first run")
   *   * `status === "ok"` + rows     → ready section list
   *
   * Wrapped in `SectionResult` (cross-audit §2.3) for a uniform
   * "couldn't load" branch, matching Inbox / Projects / Connectors.
   */
  readonly archive?: SectionResult<ChatsArchiveData> | null;

  /**
   * Reopen a conversation (FR-4.7). Fired on row click OR Enter/Space.
   * The host translates it to `ArtifactRoute.run` — reopen → Run. The
   * component never navigates or renders a thread canvas itself.
   */
  readonly onReopen: (id: ConversationId) => void;

  /** Start a fresh conversation (FR-4.8). The host opens Run. */
  readonly onNewChat: () => void;

  /** Retry callback for the `status === "error"` branch. */
  readonly onRetry?: () => void;

  /**
   * Pin / unpin a conversation from its row's ⋯ overflow (PRD-09 D2). When
   * omitted the pin control is not rendered (e.g. a read-only host).
   */
  readonly onTogglePin?: (id: ConversationId, pinned: boolean) => void;

  /**
   * Archive / unarchive a conversation from its row's ⋯ overflow (PRD-09 D2).
   * When omitted the archive control is not rendered.
   */
  readonly onToggleArchive?: (id: ConversationId, archived: boolean) => void;

  /**
   * Fetch the next keyset page for a bucket (PRD-09 D3). When provided together
   * with `hasMore[bucket]`, a ghost "Load more" foot renders under Recent and
   * Archived.
   */
  readonly onLoadMore?: (bucket: ChatsSectionKey) => void;

  /** Per-bucket "older rows remain" flags driving the "Load more" foot (D3). */
  readonly hasMore?: Partial<Record<ChatsSectionKey, boolean>>;

  /**
   * Reference instant for relative-time rendering (FR-4.4 test seam).
   * Defaults to `Date.now()` at render.
   */
  readonly now?: number;
}

// ===========================================================================
// Styles (token-only — Settings → Appearance flows through the var refs)
// ===========================================================================

const rootStyle: CSSProperties = {
  width: "100%",
  height: "100%",
  minHeight: 0,
  boxSizing: "border-box",
  backgroundColor: "var(--color-bg)",
  color: "var(--color-text)",
  display: "flex",
  flexDirection: "column",
  overflow: "auto",
};

// `.pg` — content column max-width 960, centred (FR-4.1).
const containerStyle: CSSProperties = {
  width: "100%",
  maxWidth: 960,
  margin: "0 auto",
  padding: "24px 28px 96px",
  boxSizing: "border-box",
  display: "flex",
  flexDirection: "column",
  gap: 20,
};

const sectionsStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 24,
};

// ===========================================================================
// Top-level component
// ===========================================================================

export function ChatsArchive(props: ChatsArchiveProps): ReactElement {
  const {
    archive = null,
    onReopen,
    onNewChat,
    onRetry,
    onTogglePin,
    onToggleArchive,
    onLoadMore,
    hasMore,
    now,
  } = props;
  const nowMs = now ?? Date.now();

  const newChatAction = { label: "New chat", onClick: onNewChat };

  // === Loading ==========================================================
  if (archive === null) {
    return (
      <Frame state="loading">
        <div
          role="status"
          aria-label="Loading chats"
          data-testid="chats-skeleton"
          data-state="loading"
          style={sectionsStyle}
        >
          {Array.from({ length: 3 }).map((_, i) => (
            <RowSkeleton key={i} />
          ))}
        </div>
      </Frame>
    );
  }

  // === Error ============================================================
  if (archive.status === "error") {
    return (
      <Frame state="error">
        <div role="alert" data-testid="chats-error">
          <EmptyState
            title="Couldn't load chats"
            body={archive.error ?? "Network error — try again."}
            action={
              onRetry !== undefined
                ? { label: "Retry", onClick: onRetry }
                : undefined
            }
          />
        </div>
      </Frame>
    );
  }

  // === Unavailable ======================================================
  if (archive.status === "unavailable") {
    return (
      <Frame state="unavailable">
        <EmptyState
          title="Chats unavailable"
          body={
            archive.error ??
            "This destination is not enabled for your workspace."
          }
        />
      </Frame>
    );
  }

  // === Ready / empty ====================================================
  const data = archive.data;
  const sectionRows = (key: ChatsSectionKey): ReadonlyArray<ChatArchiveRow> =>
    (data?.[key] ?? []).slice();

  const total = CHATS_SECTION_ORDER.reduce(
    (sum, key) => sum + sectionRows(key).length,
    0,
  );

  if (total === 0) {
    return (
      <Frame state="empty">
        <div data-testid="chats-empty">
          <EmptyState
            title="Start your first run"
            body="Your conversations will appear here — pinned, recent, and archived."
            action={newChatAction}
          />
        </div>
      </Frame>
    );
  }

  return (
    <Frame state="ready">
      <div
        style={sectionsStyle}
        data-testid="chats-sections"
        data-state="ready"
      >
        {CHATS_SECTION_ORDER.map((key) => {
          const rows = sectionRows(key);
          // Pinned always renders — it hosts the "＋ New chat" primary. Recent
          // and Archived hide when empty (FR-G.3).
          if (key !== "pinned" && rows.length === 0) return null;
          return (
            <ChatsSection
              key={key}
              sectionKey={key}
              rows={rows}
              onReopen={onReopen}
              onNewChat={key === "pinned" ? onNewChat : undefined}
              onTogglePin={onTogglePin}
              onToggleArchive={onToggleArchive}
              // "Load more" only makes sense under Recent + Archived (Pinned is
              // curated, small, and hosts the New-chat CTA).
              onLoadMore={
                key !== "pinned" && hasMore?.[key] === true
                  ? () => onLoadMore?.(key)
                  : undefined
              }
              now={nowMs}
            />
          );
        })}
      </div>
    </Frame>
  );
}

// ===========================================================================
// Frame — shared `.pg` shell (root + container + `.pg-lead`)
// ===========================================================================
//
// The v3 design opens the surface with the `.pg-lead` intro — the rail already
// labels the screen, so there is NO 22px page title (README decision 1).

interface FrameProps {
  readonly state: "loading" | "error" | "unavailable" | "empty" | "ready";
  readonly children: ReactNode;
}

function Frame({ state, children }: FrameProps): ReactElement {
  return (
    <section
      aria-label="Chats destination"
      data-testid="chats-archive"
      data-state={state}
      style={rootStyle}
    >
      <div style={containerStyle}>
        <PageLead data-testid="chats-lead">{CHATS_LEAD_COPY}</PageLead>
        {children}
      </div>
    </section>
  );
}

// ===========================================================================
// NewChatButton — the small primary "＋ New chat" on the Pinned header
// ===========================================================================

function NewChatButton({
  onClick,
}: {
  readonly onClick: () => void;
}): ReactElement {
  return (
    <Button
      type="button"
      variant="primary"
      size="sm"
      onClick={onClick}
      data-testid="chats-new-chat"
      aria-label="New chat"
      style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
    >
      <Icon name="plus" size={14} />
      New chat
    </Button>
  );
}

// ===========================================================================
// ChatsSection — one bucket (mono `.sect-h` heading + count + rows)
// ===========================================================================

interface ChatsSectionProps {
  readonly sectionKey: ChatsSectionKey;
  readonly rows: ReadonlyArray<ChatArchiveRow>;
  readonly onReopen: (id: ConversationId) => void;
  /** When set (Pinned only), renders the small primary "＋ New chat". */
  readonly onNewChat?: () => void;
  readonly onTogglePin?: (id: ConversationId, pinned: boolean) => void;
  readonly onToggleArchive?: (id: ConversationId, archived: boolean) => void;
  /** When set, renders a ghost "Load more" foot fetching the next keyset page. */
  readonly onLoadMore?: () => void;
  readonly now: number;
}

const sectionWrapStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 10,
};

function ChatsSection({
  sectionKey,
  rows,
  onReopen,
  onNewChat,
  onTogglePin,
  onToggleArchive,
  onLoadMore,
  now,
}: ChatsSectionProps): ReactElement {
  const headingId = `chats-section-${sectionKey}-heading`;
  return (
    <section
      aria-labelledby={headingId}
      data-testid={`chats-section-${sectionKey}`}
      data-section-key={sectionKey}
      data-row-count={rows.length}
      style={sectionWrapStyle}
    >
      <SectionHeader
        headingId={headingId}
        count={
          rows.length > 0 ? (
            <StatusPill
              status="muted"
              label={String(rows.length)}
              showDot={false}
            />
          ) : undefined
        }
        action={
          onNewChat !== undefined ? (
            <NewChatButton onClick={onNewChat} />
          ) : undefined
        }
      >
        {SECTION_HEADINGS[sectionKey]}
      </SectionHeader>
      {rows.length > 0 ? (
        <RowList<ChatArchiveRow>
          items={rows}
          keyFor={(row) => row.id}
          ariaLabel={SECTION_HEADINGS[sectionKey]}
          data-testid={`chats-section-${sectionKey}-list`}
          renderRow={(row) => (
            <ChatArchiveRowView
              row={row}
              onReopen={onReopen}
              onTogglePin={onTogglePin}
              onToggleArchive={onToggleArchive}
              now={now}
            />
          )}
        />
      ) : null}
      {onLoadMore !== undefined ? (
        <div style={loadMoreFootStyle}>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onLoadMore}
            data-testid={`chats-section-${sectionKey}-load-more`}
          >
            Load more
          </Button>
        </div>
      ) : null}
    </section>
  );
}

// The ghost "Load more" foot — the house pattern (ToolInvocationsTable /
// ReadAuditTab), NOT infinite scroll and not a fourth pattern (PRD-09 D3).
const loadMoreFootStyle: CSSProperties = {
  display: "flex",
  justifyContent: "center",
  paddingTop: 4,
};

// ===========================================================================
// ChatArchiveRowView — one conversation row (FR-4.6 · FR-G.3)
// ===========================================================================
//
// Built on the shared `.lrow` <Row>: a leading icon (live → brand mark in
// success, else the chats glyph), the title + status chip, a body-font
// sub-line ("preview · <mono>model</mono>", rendered gracefully when either is
// empty), and a mono time. The whole row reopens the conversation on click and
// Enter/Space (Row's activatable mode).

interface ChatArchiveRowViewProps {
  readonly row: ChatArchiveRow;
  readonly onReopen: (id: ConversationId) => void;
  readonly onTogglePin?: (id: ConversationId, pinned: boolean) => void;
  readonly onToggleArchive?: (id: ConversationId, archived: boolean) => void;
  readonly now: number;
}

// Live-run icon slot tint — the brand turbine reads as "live" in success.
const liveIconStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  color: "var(--color-success)",
};

// Inline mono model marker within the body-font sub-line. PRD-09 D7 / README G1:
// the design's `.mono` is a FAMILY switch only (copilot.css:138-140), so the tag
// inherits its container's `.lrow__sub` tone (`--mut2` #64646d). The former
// `color: var(--color-text-muted)` #98989f re-coloured it one tone brighter than
// the line it sits in — a HIGH on every row. The fix is a DELETION, not a
// re-point: dropping `color` lets the span inherit `Row`'s `subStyle`
// (`--color-text-subtle` = #64646d), so a later change to the sub-line tone
// carries through instead of re-breaking.
const modelMonoStyle: CSSProperties = {
  fontFamily: "var(--font-mono)",
  whiteSpace: "nowrap",
};

// Build the "preview · <mono>model</mono>" sub-line, degrading to whichever
// parts are present. Returns undefined when both are empty (title + chip + time
// only) so the row renders gracefully before PRD-H populates the metadata.
function buildSubLine(preview: string, model: string): ReactNode {
  const hasPreview = preview.length > 0;
  const hasModel = model.length > 0;
  if (!hasPreview && !hasModel) return undefined;
  return (
    <>
      {hasPreview ? (
        <span data-testid="chat-archive-row-preview">{preview}</span>
      ) : null}
      {hasPreview && hasModel ? " · " : null}
      {hasModel ? (
        <span style={modelMonoStyle} data-testid="chat-archive-row-model">
          {model}
        </span>
      ) : null}
    </>
  );
}

function ChatArchiveRowView({
  row,
  onReopen,
  onTogglePin,
  onToggleArchive,
  now,
}: ChatArchiveRowViewProps): ReactElement {
  const isLive = row.status === "running";
  const presentation = runStatusTone(row.status);

  const icon = isLive ? (
    <span
      style={liveIconStyle}
      data-testid="chat-archive-row-icon"
      data-live="true"
    >
      <BrandMark size={18} />
    </span>
  ) : (
    <span data-testid="chat-archive-row-icon" data-live="false">
      <Icon name="chats" size={18} />
    </span>
  );

  const chip = (
    <StatusPill
      status={statusTone(row.status)}
      label={presentation.label}
      showDot={presentation.showDot}
    />
  );

  const meta = (
    <span data-testid="chat-archive-row-time">
      {formatRelativeTime(row.updated_at, now)}
    </span>
  );

  const isArchived = row.status === "archived";
  const overflow =
    onTogglePin !== undefined || onToggleArchive !== undefined ? (
      <RowOverflowMenu
        row={row}
        isArchived={isArchived}
        onTogglePin={onTogglePin}
        onToggleArchive={onToggleArchive}
      />
    ) : undefined;

  return (
    <Row
      data-testid="chat-archive-row"
      data-conversation-id={row.id}
      data-status={row.status}
      data-pinned={row.pinned ? "true" : "false"}
      icon={icon}
      title={<span data-testid="chat-archive-row-title">{row.title}</span>}
      chip={chip}
      sub={buildSubLine(row.preview, row.model)}
      meta={meta}
      overflow={overflow}
      onActivate={() => onReopen(row.id)}
      ariaLabel={`Reopen ${row.title}`}
    />
  );
}

// ===========================================================================
// RowOverflowMenu — the ⋯ → Pin / Unpin / Archive / Unarchive control (D2)
// ===========================================================================
//
// A deliberate live-only addition (the design ships no ⋯): the row is the right
// home for pin/archive because curation is a LIST operation (you pin THIS one out
// of the twenty you're looking at) and Chats is the only surface with an Archived
// section, so unarchive must live where its result is visible. Always mounted,
// keyboard reachable; `Row`'s overflow slot isolates it from row activation.

const overflowTriggerStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 22,
  height: 22,
  padding: 0,
  border: "none",
  background: "transparent",
  color: "var(--color-text-subtle)",
  cursor: "pointer",
  borderRadius: "var(--radius-sm, 6px)",
  lineHeight: 1,
};

const overflowMenuStyle: CSSProperties = {
  position: "absolute",
  top: "100%",
  right: 0,
  zIndex: 20,
  minWidth: 140,
  display: "flex",
  flexDirection: "column",
  padding: 4,
  gap: 2,
  background: "var(--color-surface-elevated)",
  border: "1px solid var(--color-border)",
  borderRadius: "var(--radius-md, 8px)",
  boxShadow: "0 8px 24px rgba(0,0,0,0.32)",
};

const overflowItemStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  width: "100%",
  padding: "6px 8px",
  border: "none",
  background: "transparent",
  color: "var(--color-text)",
  fontSize: "var(--font-size-2xs)",
  textAlign: "left",
  cursor: "pointer",
  borderRadius: "var(--radius-sm, 6px)",
};

function RowOverflowMenu({
  row,
  isArchived,
  onTogglePin,
  onToggleArchive,
}: {
  readonly row: ChatArchiveRow;
  readonly isArchived: boolean;
  readonly onTogglePin?: (id: ConversationId, pinned: boolean) => void;
  readonly onToggleArchive?: (id: ConversationId, archived: boolean) => void;
}): ReactElement {
  const [open, setOpen] = useState(false);
  const close = () => setOpen(false);
  return (
    <span style={{ position: "relative", display: "inline-flex" }}>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Chat actions"
        data-testid="chat-archive-row-overflow-trigger"
        style={overflowTriggerStyle}
        onClick={() => setOpen((v) => !v)}
      >
        ⋯
      </button>
      {open ? (
        <div
          role="menu"
          aria-label={`Actions for ${row.title}`}
          data-testid="chat-archive-row-overflow-menu"
          style={overflowMenuStyle}
        >
          {onTogglePin !== undefined && !isArchived ? (
            <button
              type="button"
              role="menuitem"
              style={overflowItemStyle}
              data-testid="chat-archive-row-pin"
              onClick={() => {
                onTogglePin(row.id, !row.pinned);
                close();
              }}
            >
              {row.pinned ? "Unpin" : "Pin to top"}
            </button>
          ) : null}
          {onToggleArchive !== undefined ? (
            <button
              type="button"
              role="menuitem"
              style={overflowItemStyle}
              data-testid="chat-archive-row-archive"
              onClick={() => {
                onToggleArchive(row.id, !isArchived);
                close();
              }}
            >
              {isArchived ? "Unarchive" : "Archive"}
            </button>
          ) : null}
        </div>
      ) : null}
    </span>
  );
}

// ===========================================================================
// RowSkeleton — loading placeholder row
// ===========================================================================

function RowSkeleton(): ReactElement {
  const style: CSSProperties = {
    height: 56,
    borderRadius: "var(--radius-sm, 6px)",
    border: "1px solid var(--color-border, #232325)",
    backgroundColor: "var(--color-surface-muted, #222224)",
    opacity: 0.5,
  };
  return (
    <div style={style} data-testid="chats-skeleton-row" aria-hidden="true" />
  );
}
