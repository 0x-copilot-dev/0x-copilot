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

import type {
  CSSProperties,
  KeyboardEvent as ReactKeyboardEvent,
  ReactElement,
  ReactNode,
} from "react";

import type {
  ChatArchiveRow,
  ChatArchiveStatus,
  ChatsArchive as ChatsArchiveData,
  ConversationId,
  SectionResult,
} from "@0x-copilot/api-types";

import { EmptyState } from "../../shell/EmptyState";
import { PageHeader } from "../../shell/PageHeader";
import { StatusPill, type StatusTone } from "../../shell/StatusPill";
import { formatRelativeTime } from "../../util/time";

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
  archived: "Archived",
};

// ===========================================================================
// Status → tone / label (DESIGN-SPEC §9 — single-accent discipline)
// ===========================================================================
//
// running → jade (`ok`); paused → amber (`warning`); done / archived →
// muted. Only `--color-accent` is reserved for interactive accent; status
// chips carry semantic tone, never decorative colour.

function statusTone(status: ChatArchiveStatus): StatusTone {
  switch (status) {
    case "running":
      return "ok";
    case "paused":
      return "warning";
    case "done":
      return "muted";
    case "archived":
      return "muted";
  }
}

function statusLabel(status: ChatArchiveStatus): string {
  switch (status) {
    case "running":
      return "Running";
    case "paused":
      return "Paused";
    case "done":
      return "Done";
    case "archived":
      return "Archived";
  }
}

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
  const { archive = null, onReopen, onNewChat, onRetry, now } = props;
  const nowMs = now ?? Date.now();

  const newChatAction = { label: "New chat", onClick: onNewChat };

  // === Loading ==========================================================
  if (archive === null) {
    return (
      <Frame state="loading" subtitle="Loading…" newChatAction={newChatAction}>
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
      <Frame state="error" newChatAction={newChatAction}>
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
      <Frame state="unavailable" newChatAction={newChatAction}>
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
  const sections: ReadonlyArray<{
    readonly key: ChatsSectionKey;
    readonly rows: ReadonlyArray<ChatArchiveRow>;
  }> = CHATS_SECTION_ORDER.map((key) => ({
    key,
    rows: (data?.[key] ?? []).slice(),
  }));

  const total = sections.reduce((sum, s) => sum + s.rows.length, 0);

  if (total === 0) {
    return (
      <Frame
        state="empty"
        subtitle="No conversations yet"
        newChatAction={newChatAction}
      >
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

  const subtitle = `${total} conversation${total === 1 ? "" : "s"}`;

  return (
    <Frame state="ready" subtitle={subtitle} newChatAction={newChatAction}>
      <div
        style={sectionsStyle}
        data-testid="chats-sections"
        data-state="ready"
      >
        {sections.map(({ key, rows }) =>
          rows.length === 0 ? null : (
            <ChatsSection
              key={key}
              sectionKey={key}
              rows={rows}
              onReopen={onReopen}
              now={nowMs}
            />
          ),
        )}
      </div>
    </Frame>
  );
}

// ===========================================================================
// Frame — shared `.pg` shell (root + container + PageHeader)
// ===========================================================================

interface FrameProps {
  readonly state: "loading" | "error" | "unavailable" | "empty" | "ready";
  readonly subtitle?: string;
  readonly newChatAction: {
    readonly label: string;
    readonly onClick: () => void;
  };
  readonly children: ReactNode;
}

function Frame({
  state,
  subtitle,
  newChatAction,
  children,
}: FrameProps): ReactElement {
  return (
    <section
      aria-label="Chats destination"
      data-testid="chats-archive"
      data-state={state}
      style={rootStyle}
    >
      <div style={containerStyle}>
        <PageHeader
          title="Chats"
          subtitle={subtitle}
          primaryAction={newChatAction}
        />
        {children}
      </div>
    </section>
  );
}

// ===========================================================================
// ChatsSection — one bucket (mono `.sect-h` heading + count + rows)
// ===========================================================================

interface ChatsSectionProps {
  readonly sectionKey: ChatsSectionKey;
  readonly rows: ReadonlyArray<ChatArchiveRow>;
  readonly onReopen: (id: ConversationId) => void;
  readonly now: number;
}

const sectionWrapStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 10,
};

const sectionHeaderRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
};

// `.sect-h` — mono, uppercase (DESIGN-SPEC §3).
const sectionHeadingStyle: CSSProperties = {
  margin: 0,
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-2xs, 11px)",
  fontWeight: 600,
  letterSpacing: 0.6,
  textTransform: "uppercase",
  color: "var(--color-text-muted, #b4b4b8)",
};

const listStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  margin: 0,
  padding: 0,
  listStyle: "none",
};

function ChatsSection({
  sectionKey,
  rows,
  onReopen,
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
      <div style={sectionHeaderRowStyle}>
        <h2 id={headingId} style={sectionHeadingStyle}>
          {SECTION_HEADINGS[sectionKey]}
        </h2>
        <StatusPill status="muted" label={String(rows.length)} />
      </div>
      <ul
        style={listStyle}
        aria-label={SECTION_HEADINGS[sectionKey]}
        data-testid={`chats-section-${sectionKey}-list`}
      >
        {rows.map((row) => (
          <li key={row.id} style={{ listStyle: "none" }}>
            <ChatArchiveRowView row={row} onReopen={onReopen} now={now} />
          </li>
        ))}
      </ul>
    </section>
  );
}

// ===========================================================================
// ChatArchiveRowView — one conversation row (FR-4.6)
// ===========================================================================
//
// The whole row is the reopen target: `role="button"`, focusable, and
// Enter/Space activate (DESIGN-SPEC §9 keyboard). We use an explicit
// keydown handler (not a native <button>) so Enter/Space fire the same
// path in jsdom and the browser, and so the rich row content (title,
// chip, preview, mono model + time) composes without nested-interactive
// concerns.

interface ChatArchiveRowViewProps {
  readonly row: ChatArchiveRow;
  readonly onReopen: (id: ConversationId) => void;
  readonly now: number;
}

const rowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 12,
  padding: "10px 12px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border, #232325)",
  backgroundColor: "var(--color-bg-elevated, #161617)",
  color: "var(--color-text, #ededee)",
  cursor: "pointer",
  textAlign: "left",
  width: "100%",
  boxSizing: "border-box",
};

const rowMainStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
  flex: 1,
  minWidth: 0,
};

const rowTitleRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  minWidth: 0,
};

const rowTitleStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  color: "var(--color-text, #ededee)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  minWidth: 0,
};

const rowPreviewStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-muted, #b4b4b8)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const rowMetaStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "flex-end",
  gap: 4,
  flexShrink: 0,
};

const monoStyle: CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-2xs, 11px)",
  color: "var(--color-text-muted, #b4b4b8)",
  whiteSpace: "nowrap",
};

function ChatArchiveRowView({
  row,
  onReopen,
  now,
}: ChatArchiveRowViewProps): ReactElement {
  const reopen = (): void => onReopen(row.id);
  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>): void => {
    if (
      event.key === "Enter" ||
      event.key === " " ||
      event.key === "Spacebar"
    ) {
      // Space would otherwise scroll the page; Enter is a no-op default.
      event.preventDefault();
      reopen();
    }
  };

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`Reopen ${row.title}`}
      data-testid="chat-archive-row"
      data-conversation-id={row.id}
      data-status={row.status}
      data-pinned={row.pinned ? "true" : "false"}
      style={rowStyle}
      onClick={reopen}
      onKeyDown={onKeyDown}
    >
      <div style={rowMainStyle}>
        <div style={rowTitleRowStyle}>
          <span style={rowTitleStyle} data-testid="chat-archive-row-title">
            {row.title}
          </span>
          <StatusPill
            status={statusTone(row.status)}
            label={statusLabel(row.status)}
          />
        </div>
        <div style={rowPreviewStyle} data-testid="chat-archive-row-preview">
          {row.preview}
        </div>
      </div>
      <div style={rowMetaStyle}>
        {row.model.length > 0 ? (
          <span style={monoStyle} data-testid="chat-archive-row-model">
            {row.model}
          </span>
        ) : null}
        <span style={monoStyle} data-testid="chat-archive-row-time">
          {formatRelativeTime(row.updated_at, now)}
        </span>
      </div>
    </div>
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
