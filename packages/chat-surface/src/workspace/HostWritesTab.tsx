// HostWritesTab — what this run changed on the user's real disk, and the one
// control that puts a single tool call's changes back.
//
// Pure presentational, like every other rail body: it owns no fetch and no
// projection. `useHostWrites` reads the two facade routes through the
// `Transport` port and hands the grouped result down.
//
// THREE PROPERTIES THIS BODY OWNS, AND WHY EACH IS HERE RATHER THAN ELSEWHERE.
//
// 1. AN UNDO IS NOT REACHABLE IN ONE CLICK. This is the write gate's rule
//    (`TcWriteGateRow`: "no approve control for an irreversible write is
//    reachable in one click"), and it applies here for a sharper reason than it
//    does there. The journal captures the content that existed BEFORE the agent
//    wrote. It does not capture what is there NOW. So pressing Undo overwrites
//    the current bytes — including anything the user typed after the run — with
//    a pre-image, and nothing captures THAT. The undo of an undo does not
//    exist. So the row ARMS a confirmation which prints the full paths, and the
//    confirmation is what posts.
//
//    The two testids therefore have to be distinct names and the confirm must
//    not live under the arm's prefix — `host-writes-confirm-<key>`, never
//    `host-writes-undo-confirm-<key>`. A journey pressing every
//    `[data-testid^=host-writes-undo-]` would otherwise press the control the
//    arming step exists to withhold, and every "no blind undo" assertion would
//    still pass. The safety property is carried by the SHAPE of the name.
//
// 2. EVERY NAME THAT TAKES A DECISION IS SCOPED BY ITS GROUP. A run that
//    wrote from three tool calls draws three rows, each with its own Undo. A
//    global `host-writes-undo` would be ambiguous in exactly that state —
//    Playwright refuses an ambiguous selector, so "three groups on screen"
//    becomes an undo that never happened. Structural nodes keep global names
//    (`host-writes-tab`, `host-writes-tab-empty`); anything deciding is
//    `…-<group key>`.
//
// 3. THE OUTCOME IS RENDERED, ALWAYS. The server audits the revert — including
//    one that restored nothing, because that is the event an operator needs to
//    see. A surface that showed a spinner and then went quiet would leave the
//    user's only record of it in a log they cannot read. So the receipt is one
//    row per path with the server's own status word, and a group whose undo
//    restored nothing says so rather than looking successful.
//
// Boundary: framework-agnostic — no bare window/document/fetch; design-system
// tokens only. Paths are untrusted host strings and are rendered as text nodes.

import {
  useState,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import type {
  HostWriteGroup,
  HostWriteRevertSummary,
} from "../destinations/run/hostWrites";
import {
  hostWriteFileName,
  hostWriteKindLabel,
} from "../destinations/run/hostWrites";
import type { HostWriteRevertState } from "../destinations/run/useHostWrites";

export interface HostWritesTabProps {
  readonly groups: readonly HostWriteGroup[];
  readonly error?: string | null;
  readonly states?: Readonly<Record<string, HostWriteRevertState>>;
  readonly reports?: Readonly<Record<string, HostWriteRevertSummary>>;
  readonly failures?: Readonly<Record<string, string>>;
  /**
   * Undo one group. Optional: omitted ⇒ the rows render read-only, which is the
   * honest state for a host that can show the journal but has no way to post.
   */
  readonly onUndo?: (group: HostWriteGroup) => void;
}

const NO_STATES: Readonly<Record<string, HostWriteRevertState>> = {};
const NO_REPORTS: Readonly<Record<string, HostWriteRevertSummary>> = {};
const NO_FAILURES: Readonly<Record<string, string>> = {};

export function HostWritesTab({
  groups,
  error = null,
  states = NO_STATES,
  reports = NO_REPORTS,
  failures = NO_FAILURES,
  onUndo,
}: HostWritesTabProps): ReactElement {
  if (error !== null) {
    return <Notice testId="host-writes-tab-error">{error}</Notice>;
  }
  if (groups.length === 0) {
    return (
      <Notice testId="host-writes-tab-empty">
        This run hasn&apos;t changed any files on this computer.
      </Notice>
    );
  }

  const fileCount = groups.reduce((total, group) => total + group.pathCount, 0);

  return (
    <div className="atlas-workspace-tab" data-testid="host-writes-tab">
      <section style={cardStyle} aria-label="Files this run changed">
        <div style={headerStyle}>
          <span style={eyebrowStyle}>{`CHANGED · ${fileCount}`}</span>
          <span style={noteStyle}>
            {groups.length === 1
              ? "One tool call wrote to disk."
              : `${groups.length} tool calls wrote to disk.`}
          </span>
        </div>
        <div role="list">
          {groups.map((group) => (
            <GroupRow
              key={group.key}
              group={group}
              state={states[group.key]}
              report={reports[group.key]}
              failure={failures[group.key]}
              onUndo={onUndo}
            />
          ))}
        </div>
      </section>
    </div>
  );
}

function GroupRow({
  group,
  state,
  report,
  failure,
  onUndo,
}: {
  group: HostWriteGroup;
  state: HostWriteRevertState | undefined;
  report: HostWriteRevertSummary | undefined;
  failure: string | undefined;
  onUndo: ((group: HostWriteGroup) => void) | undefined;
}): ReactElement {
  const [armed, setArmed] = useState(false);
  const busy = state === "reverting";
  // Settled here means "the server answered", never "the files came back" —
  // whether they did is the receipt's to say, one row at a time.
  const settled = state === "reverted";
  const actionable = group.undoable && onUndo !== undefined && !settled;

  return (
    <div
      role="listitem"
      data-testid={`host-writes-group-${group.key}`}
      data-undoable={group.undoable ? "true" : "false"}
      data-state={state ?? "idle"}
      style={rowStyle}
    >
      <div style={rowHeadStyle}>
        {/* The unbounded item — an absolute host path — is the one that gives
            way, exactly as the write gate's vendor meta does. If this were
            `flex: none` the Undo control would be what a narrow rail clipped,
            which is an undo nobody can reach. The FULL path is not lost: the
            confirmation below prints every one of them unabridged, because an
            undo is consented to by reading which files. */}
        <div style={rowMainStyle}>
          <span style={rowTitleStyle} title={group.entries[0]?.path ?? ""}>
            {summaryTitle(group)}
          </span>
          <span style={rowSubtitleStyle}>{group.entries[0]?.path ?? ""}</span>
        </div>
        <span style={actionsStyle}>
          {actionable && !armed ? (
            <button
              type="button"
              className="ui-button ui-button--sm"
              data-testid={`host-writes-undo-${group.key}`}
              onClick={() => setArmed(true)}
            >
              Undo…
            </button>
          ) : null}
          {!group.undoable ? (
            <span style={metaStyle}>
              {group.toolCallId === null ? "No tool call" : "Not revertible"}
            </span>
          ) : null}
          {settled ? <span style={metaStyle}>Undo requested</span> : null}
        </span>
      </div>

      {/* The confirmation. Present only once armed, so the posting control is
          never one click from rest — and it prints the paths in full, wrapped
          rather than truncated, because that list IS the thing being consented
          to. */}
      {armed && actionable ? (
        <div
          style={confirmStyle}
          data-testid={`host-writes-armed-${group.key}`}
        >
          <p style={confirmLeadStyle}>
            {`Put ${plural(group.pathCount, "file")} back as ${group.pathCount === 1 ? "it was" : "they were"} before this tool call?`}
          </p>
          <ul style={pathListStyle}>
            {distinctPaths(group).map((path) => (
              <li key={path} style={pathStyle}>
                {path}
              </li>
            ))}
          </ul>
          <p style={confirmWarnStyle}>
            Anything written to these files since is not captured and will be
            lost. This cannot be undone.
          </p>
          <div style={confirmActionsStyle}>
            <button
              type="button"
              className="ui-button ui-button--sm ui-button--danger"
              data-testid={`host-writes-confirm-${group.key}`}
              disabled={busy}
              onClick={() => onUndo?.(group)}
            >
              {busy ? "Undoing…" : "Undo these changes"}
            </button>
            <button
              type="button"
              className="ui-button ui-button--sm"
              data-testid={`host-writes-cancel-${group.key}`}
              disabled={busy}
              onClick={() => setArmed(false)}
            >
              Keep them
            </button>
          </div>
        </div>
      ) : null}

      {/* The receipt — never a silent mutation. */}
      {report !== undefined ? (
        <div
          style={receiptStyle}
          data-testid={`host-writes-receipt-${group.key}`}
          data-complete={report.complete ? "true" : "false"}
        >
          <p style={receiptHeadStyle}>{report.headline}</p>
          <ul style={pathListStyle}>
            {report.rows.map((row) => (
              <li
                key={`${row.path}:${row.status}`}
                style={pathStyle}
                data-undone={row.undone ? "true" : "false"}
              >
                <span style={statusStyle(row.undone)}>{row.status}</span>{" "}
                {row.path}
                {row.detail !== null ? (
                  <span style={detailStyle}>{` — ${row.detail}`}</span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {failure !== undefined ? (
        <p
          style={failureStyle}
          data-testid={`host-writes-failure-${group.key}`}
        >
          {failure}
        </p>
      ) : null}
    </div>
  );
}

function Notice({
  testId,
  children,
}: {
  testId: string;
  children: ReactNode;
}): ReactElement {
  return (
    <div
      className="atlas-workspace-tab atlas-workspace-tab--empty"
      data-testid={testId}
    >
      <p>{children}</p>
    </div>
  );
}

/**
 * The row's one line: what the call did, to how many files.
 *
 * The count is `pathCount` — DISTINCT paths — because that is what an undo
 * restores. Two writes to one file collapse to one restore server-side
 * (`HostWriteReverter.select` keeps the oldest record per path), so counting
 * entries would print a number the button cannot deliver.
 */
function summaryTitle(group: HostWriteGroup): string {
  const kinds = new Set(group.entries.map((entry) => entry.kind));
  const verb =
    kinds.size === 1
      ? hostWriteKindLabel([...kinds][0]!)
      : `${group.entries.length} changes to`;
  if (group.pathCount === 1) {
    return `${verb} ${hostWriteFileName(group.entries[0]?.path ?? "")}`;
  }
  return `${verb} ${plural(group.pathCount, "file")}`;
}

function distinctPaths(group: HostWriteGroup): readonly string[] {
  return [...new Set(group.entries.map((entry) => entry.path))];
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

// Geometry lifted from `ApprovalsTab`, which lifted it from `CompactSourceList`
// — the rail's house list recipe. Inline token styles rather than host class
// names for the reason that tab documents: `atlas-*` rules live in the WEB app's
// stylesheet and never load in the Electron host, so a rail body styled by them
// renders as unstyled markup on desktop.
const cardStyle: CSSProperties = {
  background: "var(--color-surface)",
  border: "1px solid var(--color-border)",
  borderRadius: 10,
  color: "var(--color-text)",
  overflow: "hidden",
};

const headerStyle: CSSProperties = {
  alignItems: "center",
  borderBottom: "1px solid var(--color-border)",
  display: "flex",
  gap: 8,
  minWidth: 0,
  padding: "8px 11px",
};

const eyebrowStyle: CSSProperties = {
  color: "var(--color-text-subtle)",
  fontFamily: "var(--font-mono)",
  fontSize: 9,
  letterSpacing: "0.1em",
  lineHeight: "13.5px",
};

const noteStyle: CSSProperties = {
  color: "var(--color-text-muted)",
  fontFamily: "var(--font-mono)",
  fontSize: 9,
  lineHeight: 1.3,
  marginLeft: "auto",
  textAlign: "right",
};

const rowStyle: CSSProperties = {
  borderBottom: "1px solid var(--color-border)",
  display: "grid",
  gap: 6,
  minWidth: 0,
  padding: "8px 11px",
  width: "100%",
};

const rowHeadStyle: CSSProperties = {
  alignItems: "center",
  display: "flex",
  gap: 9,
  minWidth: 0,
};

// `1 1 auto` + `minWidth: 0` — the shrinkable half of the row. See the comment
// at the call site: this is the item that must give way so the actions do not.
const rowMainStyle: CSSProperties = {
  display: "grid",
  flex: "1 1 auto",
  gap: 1,
  minWidth: 0,
};

const rowTitleStyle: CSSProperties = {
  fontSize: "var(--font-size-sm)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const rowSubtitleStyle: CSSProperties = {
  color: "var(--color-text-muted)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-2xs)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

// `flex: 0 0 auto` — the control never yields. The frame clips at its END,
// which is where this box sits.
const actionsStyle: CSSProperties = {
  alignItems: "center",
  display: "flex",
  flex: "0 0 auto",
  gap: 4,
  marginLeft: "auto",
};

const metaStyle: CSSProperties = {
  color: "var(--color-text-subtle)",
  fontFamily: "var(--font-mono)",
  fontSize: 9,
};

const confirmStyle: CSSProperties = {
  background: "var(--color-surface-muted)",
  border: "1px solid var(--color-border)",
  borderRadius: "var(--radius-md, 8px)",
  display: "grid",
  gap: 6,
  padding: 9,
};

const confirmLeadStyle: CSSProperties = {
  color: "var(--color-text)",
  fontSize: "var(--font-size-xs)",
  margin: 0,
};

const confirmWarnStyle: CSSProperties = {
  color: "var(--color-danger)",
  fontSize: "var(--font-size-xs)",
  margin: 0,
};

const confirmActionsStyle: CSSProperties = {
  display: "flex",
  gap: 6,
  justifySelf: "start",
};

const pathListStyle: CSSProperties = {
  display: "grid",
  gap: 2,
  listStyle: "none",
  margin: 0,
  padding: 0,
};

// The one string nothing is allowed to shorten. An absolute host path has no
// break opportunity in its longest segment, so `overflowWrap: anywhere` is what
// keeps it from setting the panel's min-content width — and the absence of an
// ellipsis is the point: consent to a truncated path is not consent.
const pathStyle: CSSProperties = {
  color: "var(--color-text)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-10, 10px)",
  minWidth: 0,
  overflowWrap: "anywhere",
};

const receiptStyle: CSSProperties = {
  borderLeft: "2px solid var(--color-border-strong, var(--color-border))",
  display: "grid",
  gap: 4,
  paddingLeft: 8,
};

const receiptHeadStyle: CSSProperties = {
  color: "var(--color-text-muted)",
  fontSize: "var(--font-size-xs)",
  margin: 0,
};

const detailStyle: CSSProperties = {
  color: "var(--color-text-subtle)",
};

const failureStyle: CSSProperties = {
  color: "var(--color-danger)",
  fontSize: "var(--font-size-xs)",
  margin: 0,
};

function statusStyle(undone: boolean): CSSProperties {
  return {
    color: undone
      ? "var(--color-success, #57c785)"
      : "var(--color-danger, #f0764f)",
    fontFamily: "var(--font-mono)",
    textTransform: "uppercase",
  };
}
