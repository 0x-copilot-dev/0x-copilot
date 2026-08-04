// ThreadSwitcher — switch conversations without leaving the Run cockpit.
//
// Source: docs/plan/windowed-mode/PRD-01-thread-switching.md
//
// The problem it closes: `run` is in `FULL_BLEED_DESTINATIONS`, so `ChatShell`
// forces `showContextPanel = false` for the cockpit, and `run` is also in
// `SUPPRESS_TOPBAR`, so there is no topbar to hang a control on either. The
// result was that switching conversations cost a full destination change —
// rail → Chats → pick → back to Run — even though Run is the product's front
// door (`defaultDestinationForProfile(_) → "run"`).
//
// D-1.1: this does NOT un-full-bleed `run`. A permanent shell column at every
// width is exactly the full-screen-only thinking the windowed-mode program
// exists to remove, and it would re-open the "unfed panel shows Nothing here
// yet." regression the content gate was written to close. The cockpit owns a
// switcher; the shell grid is untouched.
//
// D-1.2: ONE component, two presentations, chosen by `useShellWidthClass()`:
//   wide / regular → docked 224px column (CONTEXT_PANEL_WIDTH — reusing the
//                    number, since minting a second panel width is the drift
//                    that constant exists to prevent)
//   compact        → 260px overlay + scrim, Esc closes, focus returns
//
// The data half is NOT new: `useChatsArchive` already owns the bucketed fetch,
// the keyset pagination, the pin/archive writes and the `conversation_changed`
// live tail, and `toChatArchiveRow` is already the shared per-row projection.
// This component is presentation over both. No second fetch path (FR-1.1).
//
// D-1.4 (projects scope): the scope control sits BELOW "+ New run", never above
// it. New run is the ACTION; the scope is its qualifier — and because a new run
// inherits the scope, the two must read top-to-bottom as one sentence ("start a
// run" … "in Acme renewal"). Leading with the filter would turn the cockpit's
// front door into a search box with a button attached. Owner decision, asserted
// in DOM order by a test so a later tidy-up cannot quietly transpose them.
//
// The scope is a project or ALL — there is deliberately no "Unfiled" choice.
// The archive's `project_id` filter has no "IS NULL" encoding (an absent filter
// means "no filter"), and client-filtering one paginated page would silently
// drop rows, which is exactly the bucketing bug PRD-09 D3 removed.

import type {
  ChatArchiveRow,
  ConversationId,
  ProjectColorHue,
  ProjectId,
} from "@0x-copilot/api-types";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
  type ReactNode,
} from "react";

// A narrow, deliberate reach across the layering note above: `projectHueRamp`
// is a pure leaf (it imports nothing but React types and never imports back
// into `shell/`, so no cycle appears), and it is the ONE place a per-project
// colour is computed. That layering rule exists to keep the panel off the Chats
// CONTROLLER — a stateful module a structural interface can stand in for. A
// colour formula cannot be stood in for structurally, and restating it here is
// precisely the four-ramps drift `ProjectIconTile` was written to end.
import { projectHueRamp } from "../destinations/_shared/ProjectIconTile";
import { Icon } from "../icons/Icon";
import { formatRelativeTime } from "../util/time";

import { CONTEXT_PANEL_WIDTH } from "./ContextPanel";

// `shell/` sits UNDER `destinations/` in this package's layering, so the panel
// does not import the Chats controller — it declares the shape it needs and
// `ChatsArchiveController` satisfies it structurally. The cockpit's
// `ThreadSwitcherHost` is what actually calls `useChatsArchive()`.
export type ThreadSectionKey = "pinned" | "recent" | "archived";

/** Render order. Matches `CHATS_SECTION_ORDER`; asserted equal by a test so the
 *  two cannot drift into showing different orders for the same data. */
export const THREAD_SECTION_ORDER: readonly ThreadSectionKey[] = [
  "pinned",
  "recent",
  "archived",
];

/** The subset of `ChatsArchiveController` this panel consumes. */
export interface ThreadListSource {
  readonly archive: {
    readonly status: "ok" | "error" | "unavailable";
    readonly data?: Readonly<
      Record<ThreadSectionKey, readonly ChatArchiveRow[]>
    >;
  } | null;
  readonly hasMore: Readonly<Record<ThreadSectionKey, boolean>>;
  readonly onLoadMore: (bucket: ThreadSectionKey) => void;
  readonly retry: () => void;
}

/** Overlay width. Only used below {@link THREAD_SWITCHER_DOCK_FLOOR}, where the
 *  panel genuinely cannot share the row with a usable canvas. */
export const THREAD_SWITCHER_OVERLAY_WIDTH = 260;

/**
 * Panel width when docked at `compact`. Narrower than `CONTEXT_PANEL_WIDTH` so
 * a 640px window still leaves `640 − 48 (rail) − 200 = 392px` of canvas — enough
 * for the composer to stay usable, which is the whole point of docking.
 */
export const THREAD_SWITCHER_COMPACT_WIDTH = 200;

/**
 * **Cockpit-container** width below which the switcher stops docking and becomes
 * an overlay.
 *
 * `200 (panel) + 352 (canvas)`. Below this a docked panel leaves no room to
 * type, so the modal overlay becomes the lesser evil.
 *
 * ⚠️ This is a CONTAINER width, not a window width. The observer sits on the
 * cockpit root, which is already inside the 48px app rail — so a 640px window
 * measures 592px here. Comparing a window-derived number against this would be
 * off by exactly the rail, which is how the first fix still produced a scrim at
 * 640px.
 *
 * The floor is deliberately below `640 − 48 = 592`, the narrowest cockpit the
 * desktop app can produce (PRD-00 OD-02 sets `minWidth: 640` on the
 * BrowserWindow). So a real desktop user ALWAYS gets a dock and never a scrim;
 * the overlay is a safety net for below-floor embeds (a phone-width web
 * viewport, a crushed split pane), not a mode we design people into.
 *
 * The original cut keyed this off the `compact` breakpoint (720px), so an
 * ordinary 640px window opened a modal panel with a scrim over its own composer
 * — you could browse threads or type, never both. That is the opposite of what
 * every reference product does at that size.
 */
export const THREAD_SWITCHER_DOCK_FLOOR = 552;

/** Docked panel width for a width class. */
export function threadSwitcherDockWidth(compact: boolean): number {
  return compact ? THREAD_SWITCHER_COMPACT_WIDTH : CONTEXT_PANEL_WIDTH;
}

/** Section labels for the three buckets. */
const SECTION_LABEL: Readonly<Record<ThreadSectionKey, string>> = {
  pinned: "Pinned",
  recent: "Recent",
  archived: "Archived",
};

export type ThreadSwitcherVariant = "docked" | "overlay";

/** One project the thread list can be scoped to (D-1.4). */
export interface ThreadScopeOption {
  readonly id: ProjectId;
  readonly name: string;
  /** Per-project hue. Drives the monogram tile — never `icon_emoji`. */
  readonly colorHue: ProjectColorHue;
  /**
   * Threads filed here, when the host happens to know it. Optional because the
   * bucketed archive endpoint does not return per-project totals; a host that
   * cannot count must show no number rather than a wrong or zero one.
   */
  readonly count?: number;
}

export interface ThreadSwitcherProps {
  /** Presentation, resolved by the caller from its observed container width. */
  readonly variant: ThreadSwitcherVariant;
  /** Narrow the DOCKED panel (200px) so a small window keeps a usable canvas. */
  readonly compact?: boolean;
  /**
   * The shared archive controller. Injected rather than called here so the
   * cockpit mounts `useChatsArchive` ONCE (NFR-1.1) — a hook call inside a
   * component that unmounts on close would re-subscribe on every open.
   */
  readonly controller: ThreadListSource;
  /** Conversation the cockpit is currently bound to; gets `aria-current`. */
  readonly activeConversationId: ConversationId | null;
  /** Activate a row. The host translates id → route (FR-1.7). */
  readonly onOpenConversation: (id: ConversationId) => void;
  /** Start a new run. Wired to the same intent as ⌘N. */
  readonly onNewRun?: () => void;
  /** The project the list is scoped to; `null` (the default) = All threads. */
  readonly scope?: ProjectId | null;
  /**
   * Projects the list can be scoped to. The control renders ONLY when this is a
   * non-empty array — a host with no projects must get exactly today's panel,
   * not a picker whose only entry is the state it is already in.
   */
  readonly scopeOptions?: ReadonlyArray<ThreadScopeOption>;
  /** Fires with the picked project, or `null` for All threads. */
  readonly onScopeChange?: (next: ProjectId | null) => void;
  /** Close, from Esc / scrim / activation-at-compact (FR-1.8, FR-1.9). */
  readonly onRequestClose?: () => void;
  /** `id` for the toggle's `aria-controls`. */
  readonly id?: string;
}

export function ThreadSwitcher({
  variant,
  compact = false,
  controller,
  activeConversationId,
  onOpenConversation,
  onNewRun,
  scope = null,
  scopeOptions,
  onScopeChange,
  onRequestClose,
  id,
}: ThreadSwitcherProps): ReactElement {
  const isOverlay = variant === "overlay";
  const rootRef = useRef<HTMLElement | null>(null);

  // FR-1.9 — Esc closes the overlay. Scoped to the panel subtree rather than a
  // document listener: this package may not touch `document`, and a keydown on
  // the panel is the only place Esc should mean "close this".
  const handleKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLElement>): void => {
      if (event.key !== "Escape" || !isOverlay) {
        return;
      }
      event.stopPropagation();
      onRequestClose?.();
    },
    [isOverlay, onRequestClose],
  );

  // Move focus into the overlay when it opens so Esc lands and so a keyboard
  // user is not left behind on the toggle with a modal open in front of them.
  // The docked variant deliberately does NOT steal focus — it is not modal.
  useEffect(() => {
    if (!isOverlay) {
      return;
    }
    rootRef.current?.focus();
  }, [isOverlay]);

  // `null` whenever the host supplied no options, an empty list, or a scope id
  // that matches nothing it sent — all three mean "we cannot name a project",
  // and the panel then reads exactly as it did before scopes existed.
  const activeScope: ThreadScopeOption | null =
    scopeOptions?.find((option) => option.id === scope) ?? null;

  const handleActivate = useCallback(
    (rowId: ConversationId): void => {
      onOpenConversation(rowId);
      // FR-1.8 — at compact the overlay closes on activation (you asked for a
      // thread, here it is); docked stays open because you are browsing.
      if (isOverlay) {
        onRequestClose?.();
      }
    },
    [isOverlay, onOpenConversation, onRequestClose],
  );

  return (
    <aside
      ref={rootRef}
      id={id}
      data-component="thread-switcher"
      data-variant={variant}
      aria-label="Threads"
      // The overlay is modal; the dock is a plain labelled region. Two
      // presentations, two honest a11y contracts.
      role={isOverlay ? "dialog" : undefined}
      aria-modal={isOverlay ? true : undefined}
      tabIndex={isOverlay ? -1 : undefined}
      onKeyDown={handleKeyDown}
      style={panelStyle(isOverlay, compact)}
    >
      <style>{THREAD_SWITCHER_CSS}</style>

      <div style={headStyle}>
        <div style={titleStyle} data-testid="thread-switcher-title">
          Threads
        </div>
        {/* D-1.4 — the action FIRST, its qualifier below. */}
        {onNewRun !== undefined ? (
          <button
            type="button"
            data-testid="thread-switcher-new"
            className="cs-thread-switcher__new"
            onClick={onNewRun}
            style={newRunStyle}
          >
            {/* No `title` → the Icon is decorative and the button's own text
                is the accessible name — which, when scoped, is the whole
                sentence "New run in Acme renewal". */}
            <Icon name="plus" size={14} />
            <span>New run</span>
            {/* The ONLY place a user learns that a new run inherits the scope.
                Without it the control below reads as a filter on the list
                alone, and the first run filed somewhere unexpected is a
                surprise the panel had all the information to prevent. */}
            {activeScope !== null ? (
              <span
                data-testid="thread-switcher-new-scope"
                style={newRunScopeStyle}
              >
                in {activeScope.name}
              </span>
            ) : null}
          </button>
        ) : null}

        {scopeOptions !== undefined && scopeOptions.length > 0 ? (
          <ScopePicker
            scope={scope}
            options={scopeOptions}
            onScopeChange={onScopeChange}
          />
        ) : null}
      </div>

      <div style={bodyStyle} data-testid="thread-switcher-body">
        <SwitcherBody
          controller={controller}
          activeConversationId={activeConversationId}
          onActivate={handleActivate}
        />
      </div>

      {/* No account foot. PRD-09 D-9.5 read the rail glyph as the COLLAPSED
          form of an affordance the docked panel would spell out — but the rail
          has no idea whether this panel is docked, so both rendered at once:
          two identical 26px "L" circles ~60px apart on the same baseline, of
          which only the rail one is a button. The rail owns the account (it is
          the design's `.rail-me`, it survives every destination and every
          width, and its `title`/`aria-label` already carry the full name). A
          second, non-interactive copy taught the wrong affordance and added no
          information — a solo desktop has exactly one account. */}
    </aside>
  );
}

// ===========================================================================
// Scope picker — All threads, or one project (D-1.4)
// ===========================================================================

/**
 * The monogram rule, kept byte-identical to `ProjectIconTile` and the composer's
 * `ProjectFilingChip` (first letter, upper-cased, `?` for an empty name) so one
 * project cannot read as three different glyphs in the three places it appears.
 * `icon_emoji` is NEVER rendered: the server defaults it to 📁 for every project
 * (`0043_projects.sql:39`), which is a wall of identical folders.
 */
function scopeMonogram(name: string): string {
  return (name.trim()[0] ?? "?").toUpperCase();
}

const ALL_THREADS_LABEL = "All threads";

function ScopePicker({
  scope,
  options,
  onScopeChange,
}: {
  readonly scope: ProjectId | null;
  readonly options: ReadonlyArray<ThreadScopeOption>;
  readonly onScopeChange?: (next: ProjectId | null) => void;
}): ReactElement {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const active = options.find((option) => option.id === scope) ?? null;

  // Closing always returns focus to the trigger. Without it, closing from a menu
  // row drops focus to <body> and the next Tab restarts at the top of the
  // document — and inside the OVERLAY variant that also means Esc no longer
  // reaches the panel, because the panel's handler is scoped to its own subtree.
  const dismiss = useCallback((): void => {
    setOpen(false);
    triggerRef.current?.focus();
  }, []);

  const pick = useCallback(
    (next: ProjectId | null): void => {
      onScopeChange?.(next);
      dismiss();
    },
    [dismiss, onScopeChange],
  );

  // Escape is bound on the scope ROOT — which spans the trigger AND the menu —
  // and consumes the key ONLY while the menu is open. Both halves are
  // load-bearing:
  //   * binding it on the menu alone would miss a keystroke made while focus is
  //     still on the trigger (opening does not move focus), and that keystroke
  //     bubbles to the panel's `handleKeyDown`, which closes the whole OVERLAY
  //     and leaves the menu standing on a panel that is gone;
  //   * not consuming it while CLOSED is what keeps FR-1.9 intact — Escape must
  //     still reach the panel then.
  const handleKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>): void => {
      if (event.key !== "Escape" || !open) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      dismiss();
    },
    [dismiss, open],
  );

  const renderOption = (option: ThreadScopeOption): ReactElement => {
    const on = option.id === scope;
    return (
      <button
        key={option.id}
        type="button"
        role="menuitemradio"
        aria-checked={on}
        className="ui-pop-row"
        data-testid="thread-switcher-scope-option"
        data-project-id={option.id}
        data-on={on || undefined}
        onClick={() => pick(option.id)}
      >
        <span
          className="ui-pop-row__lg"
          style={scopeRowTileStyle(option.colorHue)}
          aria-hidden="true"
        >
          {scopeMonogram(option.name)}
        </span>
        <span className="ui-pop-row__m">
          <span className="ui-pop-row__nm">
            <span className="ui-pop-row__txt">{option.name}</span>
          </span>
        </span>
        {option.count !== undefined ? (
          <span style={scopeCountStyle}>{option.count}</span>
        ) : null}
        <span
          className="ui-pop-row__rad"
          data-on={on || undefined}
          aria-hidden="true"
        >
          {on ? <Icon name="check" size={9} strokeWidth={3} /> : null}
        </span>
      </button>
    );
  };

  return (
    <div
      style={scopeRootStyle}
      data-testid="thread-switcher-scope"
      data-open={open || undefined}
      onKeyDown={handleKeyDown}
    >
      <button
        ref={triggerRef}
        type="button"
        className="cs-thread-switcher__scope"
        data-testid="thread-switcher-scope-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={
          active === null ? "Scope: all threads" : `Scope: ${active.name}`
        }
        onClick={() => setOpen((current) => !current)}
        style={scopeTriggerStyle}
      >
        {/* No tile in the All-threads state: a blank or placeholder tile reads
            as a project whose name failed to load. */}
        {active !== null ? (
          <span style={scopeTileStyle(active.colorHue)} aria-hidden="true">
            {scopeMonogram(active.name)}
          </span>
        ) : null}
        <span style={scopeLabelStyle}>{active?.name ?? ALL_THREADS_LABEL}</span>
        <Icon name="chevronDown" size={11} />
      </button>

      {/* Dismissal is by pick, by Escape, or by the trigger — deliberately no
          outside-click. The design's `.ui-pop-scrim` is `position: fixed;
          inset: 0`, so inside a docked panel it would cover the transcript and
          eat the user's next click on it; and this package cannot install a
          document listener instead. */}
      {open ? (
        <div
          role="menu"
          aria-label="Scope threads to a project"
          className="ui-pop"
          data-testid="thread-switcher-scope-menu"
          style={scopeMenuStyle}
        >
          <div className="ui-pop__list">
            {/* All threads FIRST, then the rule, then the projects: the widest
                scope is the one you return to, so it is where the cursor
                starts, not a reset hidden under the list. */}
            <button
              type="button"
              role="menuitemradio"
              aria-checked={scope === null}
              className="ui-pop-row"
              data-testid="thread-switcher-scope-all"
              data-on={scope === null || undefined}
              onClick={() => pick(null)}
            >
              <span className="ui-pop-row__m">
                <span className="ui-pop-row__nm">
                  <span className="ui-pop-row__txt">{ALL_THREADS_LABEL}</span>
                </span>
              </span>
              <span
                className="ui-pop-row__rad"
                data-on={scope === null || undefined}
                aria-hidden="true"
              >
                {scope === null ? (
                  <Icon name="check" size={9} strokeWidth={3} />
                ) : null}
              </span>
            </button>

            <div className="ui-pop__div" aria-hidden="true" />

            {options.map(renderOption)}
          </div>
        </div>
      ) : null}
    </div>
  );
}

// ===========================================================================
// Toggle — lives in RunHeader's leading slot (D-1.3)
// ===========================================================================

export interface ThreadSwitcherToggleProps {
  readonly open: boolean;
  readonly onToggle: () => void;
  /** `id` of the panel this controls, for `aria-controls`. */
  readonly controls: string;
}

export function ThreadSwitcherToggle({
  open,
  onToggle,
  controls,
}: ThreadSwitcherToggleProps): ReactElement {
  return (
    <button
      type="button"
      data-testid="thread-switcher-toggle"
      className="cs-thread-switcher-toggle"
      // FR-1.2 — a real disclosure control, not a decorative glyph.
      aria-expanded={open}
      aria-controls={controls}
      aria-label={open ? "Hide threads" : "Show threads"}
      title="Threads"
      onClick={onToggle}
      style={toggleStyle(open)}
    >
      <style>{THREAD_SWITCHER_TOGGLE_CSS}</style>
      {/* Panel glyph: a rectangle split by a vertical rule — the same shape
          both reference products use for their sidebar control. */}
      <svg
        width={16}
        height={16}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.7}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
        focusable="false"
      >
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <path d="M9 4v16" />
      </svg>
    </button>
  );
}

const toggleStyle = (open: boolean): CSSProperties => ({
  width: 24,
  height: 24,
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  padding: 0,
  background: "transparent",
  border: 0,
  borderRadius: "var(--radius-sm)",
  color: open ? "var(--color-text)" : "var(--color-text-subtle)",
  cursor: "pointer",
});

const THREAD_SWITCHER_TOGGLE_CSS = `
.cs-thread-switcher-toggle {
  transition: background-color 120ms ease, color 120ms ease;
}
.cs-thread-switcher-toggle:hover {
  background: var(--color-surface-muted);
  color: var(--color-text);
}
.cs-thread-switcher-toggle:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 2px;
}
`;

// ===========================================================================
// Body — the four states of the shared controller
// ===========================================================================

function SwitcherBody({
  controller,
  activeConversationId,
  onActivate,
}: {
  readonly controller: ThreadListSource;
  readonly activeConversationId: ConversationId | null;
  readonly onActivate: (id: ConversationId) => void;
}): ReactElement {
  const { archive, hasMore, onLoadMore, retry } = controller;

  // FR-1.10 — none of these states may unmount or block the transcript. They
  // render INSIDE the panel body; the cockpit around them is untouched.
  if (archive === null) {
    return <Note testId="thread-switcher-loading">Loading threads…</Note>;
  }
  if (archive.status === "error") {
    return (
      <Note testId="thread-switcher-error">
        <span style={{ color: "var(--color-danger)" }}>
          Couldn&rsquo;t load threads.
        </span>
        <button
          type="button"
          data-testid="thread-switcher-retry"
          className="cs-thread-switcher__retry"
          onClick={retry}
          style={retryStyle}
        >
          Retry
        </button>
      </Note>
    );
  }
  // `SectionResult.data` is optional even on `ok`, so an ok-with-no-payload is
  // "unavailable", not "you have no threads" — those read identically to a user
  // and mean opposite things.
  const data = archive.status === "ok" ? archive.data : undefined;
  if (data === undefined) {
    return (
      <Note testId="thread-switcher-unavailable">Threads unavailable.</Note>
    );
  }

  const sections = THREAD_SECTION_ORDER.map((key) => ({
    key,
    rows: data[key] ?? [],
  })).filter((s) => s.rows.length > 0);

  if (sections.length === 0) {
    return <Note testId="thread-switcher-empty">No threads yet.</Note>;
  }

  return (
    <>
      {sections.map(({ key, rows }) => (
        <section key={key} data-testid={`thread-switcher-section-${key}`}>
          <div style={sectionHeadStyle}>{SECTION_LABEL[key]}</div>
          <ul style={listStyle}>
            {rows.map((row) => (
              <li key={row.id}>
                <ThreadRow
                  row={row}
                  active={row.id === activeConversationId}
                  onActivate={onActivate}
                />
              </li>
            ))}
          </ul>
          {hasMore[key] ? (
            <button
              type="button"
              data-testid={`thread-switcher-more-${key}`}
              className="cs-thread-switcher__more"
              onClick={() => onLoadMore(key)}
              style={moreStyle}
            >
              Show more
            </button>
          ) : null}
        </section>
      ))}
    </>
  );
}

function ThreadRow({
  row,
  active,
  onActivate,
}: {
  readonly row: ChatArchiveRow;
  readonly active: boolean;
  readonly onActivate: (id: ConversationId) => void;
}): ReactElement {
  return (
    <button
      type="button"
      className="cs-thread-switcher__row"
      data-testid={`thread-switcher-row-${row.id}`}
      data-status={row.status}
      // FR-1.6 — the active conversation is marked, and the mark is a real
      // a11y state, not just a background colour.
      aria-current={active ? "true" : undefined}
      onClick={() => onActivate(row.id)}
      style={rowStyle}
      title={row.title}
    >
      <span aria-hidden="true" style={dotStyle} data-status={row.status} />
      <span style={rowNameStyle}>{row.title}</span>
      <span style={rowTimeStyle}>{formatRelativeTime(row.updated_at)}</span>
    </button>
  );
}

function Note({
  children,
  testId,
}: {
  readonly children: ReactNode;
  readonly testId: string;
}): ReactElement {
  return (
    <p style={noteStyle} data-testid={testId}>
      {children}
    </p>
  );
}

// ===========================================================================
// Styles — geometry from `ContextPanel`, colours from design-system tokens
// ===========================================================================

const panelStyle = (isOverlay: boolean, compact: boolean): CSSProperties => {
  const width = isOverlay
    ? THREAD_SWITCHER_OVERLAY_WIDTH
    : threadSwitcherDockWidth(compact);
  return {
    width,
    minWidth: width,
    height: "100%",
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    backgroundColor: "var(--color-bg-elevated)",
    borderRight: "1px solid var(--color-border)",
    color: "var(--color-text)",
    outline: "none",
    ...(isOverlay
      ? {
          position: "absolute" as const,
          top: 0,
          bottom: 0,
          left: 0,
          zIndex: 3,
          boxShadow: "var(--shadow-md)",
        }
      : {}),
  };
};

// `ContextPanel.headStyle` — 16/14/12 padding, hairline, 8px stack.
const headStyle: CSSProperties = {
  flex: "none",
  padding: "16px 14px 12px",
  borderBottom: "1px solid var(--color-border)",
  display: "flex",
  flexDirection: "column",
  gap: 8,
};

const titleStyle: CSSProperties = {
  fontSize: "var(--font-size-sm)",
  fontWeight: 600,
  letterSpacing: 0.2,
  color: "var(--color-text)",
};

// `ContextPanel.primaryActionStyle`.
const newRunStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  gap: 6,
  padding: "6px 10px",
  background: "transparent",
  color: "var(--color-text)",
  border: "1px solid var(--color-border)",
  borderRadius: 6,
  fontFamily: "inherit",
  fontSize: "var(--font-size-xs)",
  fontWeight: 500,
  cursor: "pointer",
};

// The suffix on "New run" naming the scope it will file into. Mono + subtle so
// it reads as metadata ON the action rather than as part of the verb, and it
// truncates rather than pushing the button's own label out of a 200px panel.
const newRunScopeStyle: CSSProperties = {
  minWidth: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-9-5)",
  fontWeight: 400,
  color: "var(--color-text-subtle)",
};

// `position: relative` is what anchors the menu. The head has no overflow clip,
// so the popover lies OVER the bucket list rather than being scrolled with it.
const scopeRootStyle: CSSProperties = {
  position: "relative",
};

// Pairs with `newRunStyle` — same border, radius and type step — but full-width
// and left-aligned on the muted rung, because this is a state readout you can
// change, not a second call to action competing with New run.
const scopeTriggerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  width: "100%",
  padding: "5px 8px",
  background: "transparent",
  color: "var(--color-text-muted)",
  border: "1px solid var(--color-border)",
  borderRadius: 6,
  fontFamily: "inherit",
  fontSize: "var(--font-size-xs)",
  fontWeight: 500,
  textAlign: "left",
  cursor: "pointer",
};

const scopeLabelStyle: CSSProperties = {
  flex: 1,
  minWidth: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

// Opens DOWNWARD and takes the trigger's exact width: the control is the last
// row of the head with the whole bucket list beneath it, so a menu sized to its
// own content would either overflow the 200px compact panel or float free of
// the control it belongs to.
const scopeMenuStyle: CSSProperties = {
  position: "absolute",
  top: "calc(100% + 6px)",
  left: 0,
  right: 0,
};

/**
 * The trigger's monogram tile. `ProjectIconTile` cannot be mounted verbatim: its
 * `size` is typed as the literal `32` (the design's only `.proj-ic` size) and
 * this row is ~26px tall, so a 32px tile would blow the trigger's box open. Only
 * the GEOMETRY is restated — matching the composer's `ProjectFilingChip` trigger
 * tile 1:1 — while the COLOUR still comes from the one `projectHueRamp`, so no
 * `hsl(...)` literal escapes that file.
 */
function scopeTileStyle(colorHue: ProjectColorHue): CSSProperties {
  const ramp = projectHueRamp(colorHue);
  return {
    width: 16,
    height: 16,
    // `--radius-sm` (6px), not a hand-written 4px: the design has no 4px rung.
    borderRadius: "var(--radius-sm)",
    display: "grid",
    placeItems: "center",
    flex: "none",
    boxSizing: "border-box",
    fontFamily: "var(--font-sans)",
    fontSize: 9,
    fontWeight: "var(--font-weight-semibold)",
    backgroundColor: ramp.background,
    border: ramp.border,
    color: ramp.color,
  };
}

/** The menu row's tile. Geometry is the shared `.ui-pop-row__lg` recipe (24px,
 *  radius, centring); only the ramp colours are set here, same reason as above. */
function scopeRowTileStyle(colorHue: ProjectColorHue): CSSProperties {
  const ramp = projectHueRamp(colorHue);
  return {
    backgroundColor: ramp.background,
    border: ramp.border,
    color: ramp.color,
    fontSize: 11,
    fontWeight: "var(--font-weight-semibold)",
  };
}

// The optional per-project count. Same mono/subtle rung the row timestamps use,
// so a number in the menu reads as the same class of metadata as one in a row.
const scopeCountStyle: CSSProperties = {
  flex: "none",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-10)",
  color: "var(--color-text-subtle)",
};

const bodyStyle: CSSProperties = {
  flex: 1,
  minHeight: 0,
  overflowY: "auto",
  padding: "8px 0",
};

// The mono micro-label rung used by every other section head in the shell.
const sectionHeadStyle: CSSProperties = {
  padding: "10px 14px 4px",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-9-5)",
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  color: "var(--color-text-subtle)",
};

const listStyle: CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
};

const rowStyle: CSSProperties = {
  position: "relative",
  width: "100%",
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "6px 14px",
  background: "transparent",
  border: 0,
  textAlign: "left",
  fontFamily: "inherit",
  fontSize: "var(--font-size-xs)",
  color: "var(--color-text-muted)",
  cursor: "pointer",
};

const rowNameStyle: CSSProperties = {
  flex: 1,
  minWidth: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const rowTimeStyle: CSSProperties = {
  flex: "none",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-10)",
  color: "var(--color-text-subtle)",
};

const dotStyle: CSSProperties = {
  flex: "none",
  width: 5,
  height: 5,
  borderRadius: "var(--radius-full)",
  background: "var(--color-text-subtle)",
};

const noteStyle: CSSProperties = {
  margin: 0,
  padding: "24px 14px",
  color: "var(--color-text-subtle)",
  fontSize: "var(--font-size-xs)",
  textAlign: "center",
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  gap: 8,
};

const retryStyle: CSSProperties = {
  padding: "4px 10px",
  background: "transparent",
  color: "var(--color-text)",
  border: "1px solid var(--color-border)",
  borderRadius: 6,
  fontFamily: "inherit",
  fontSize: "var(--font-size-xs)",
  cursor: "pointer",
};

const moreStyle: CSSProperties = {
  margin: "2px 14px 0",
  padding: "4px 0",
  background: "transparent",
  color: "var(--color-text-subtle)",
  border: 0,
  fontFamily: "inherit",
  fontSize: "var(--font-size-xs)",
  cursor: "pointer",
  textAlign: "left",
};

// Interaction states inline styles cannot express. Scoped to the component's
// own class names and shipped INSIDE the package — a host stylesheet owning
// these rules is the stranded-CSS failure this repo has already paid for.
const THREAD_SWITCHER_CSS = `
[data-component="thread-switcher"] .cs-thread-switcher__row {
  transition: background-color 120ms ease, color 120ms ease;
}
[data-component="thread-switcher"] .cs-thread-switcher__row:hover {
  background: var(--color-surface-muted);
  color: var(--color-text);
}
/* The active row reuses the RAIL's active affordance — a fill PLUS a 2px accent
   bar at the left edge (AppRail's \`.rail-item[data-active]::before\`). The fill
   alone is a ~5% lightness step (--color-surface-muted #16161a on
   --color-bg-elevated #0d0d10), which is legible on the rail only because its
   items also carry the bar. Without it the selected thread is genuinely hard to
   find in a list of nine. */
[data-component="thread-switcher"] .cs-thread-switcher__row[aria-current="true"] {
  background: var(--color-surface-muted);
  color: var(--color-text);
  font-weight: 500;
}
[data-component="thread-switcher"] .cs-thread-switcher__row[aria-current="true"]::before {
  content: "";
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 2px;
  height: 16px;
  border-radius: 0 2px 2px 0;
  background: var(--color-accent);
}
[data-component="thread-switcher"] .cs-thread-switcher__row[aria-current="true"] > span:first-child {
  background: var(--color-accent);
}
[data-component="thread-switcher"] .cs-thread-switcher__row[data-status="running"] > span:first-child {
  background: var(--color-accent);
}
[data-component="thread-switcher"] .cs-thread-switcher__row[data-status="paused"] > span:first-child {
  background: var(--color-warning);
}
[data-component="thread-switcher"] .cs-thread-switcher__new:hover,
[data-component="thread-switcher"] .cs-thread-switcher__scope:hover,
[data-component="thread-switcher"] .cs-thread-switcher__retry:hover,
[data-component="thread-switcher"] .cs-thread-switcher__more:hover {
  background: var(--color-surface-muted);
}
/* Open is the same quiet fill as hover, never an accent ring — the composer's
   \`.ui-cpill\` already settled that argument for every trigger in the product. */
[data-component="thread-switcher"] .cs-thread-switcher__scope[aria-expanded="true"] {
  background: var(--color-surface-muted);
  color: var(--color-text);
}
[data-component="thread-switcher"] button:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: -2px;
}
`;
