// The agent's writes to the user's real disk, projected for a surface that can
// undo one of them.
//
// The wire is `GET /v1/agent/runs/{run_id}/host-writes` — a FLAT, oldest-first
// list of captured pre-images (`HostWriteEntry`). The revert route takes at most
// a `tool_call_id`, so the flat list is not the unit a person acts on: the unit
// is ONE TOOL CALL. `agent_runtime/api/host_write_undo_service` says why in the
// producer's own words — "a turn that did five things and one bad thing should
// cost the user the one thing" — so this projection groups by exactly the key
// the route accepts and nothing else. A grouping the route cannot address would
// be a button that undoes more than it names.
//
// TWO FACTS ABOUT THE BACKEND THAT THE COPY HERE MUST NOT CONTRADICT:
//
//   * A revert collapses PER PATH. `HostWriteReverter.select` keeps the OLDEST
//     record per path in the selection, because "undo this set" means "restore
//     the state before the set began". Three records over two paths therefore
//     restore two files, not three. So a group counts DISTINCT PATHS — counting
//     records would print a number the undo cannot deliver.
//   * A revert does not consume the journal. Records survive the undo, so the
//     listing is unchanged afterwards and re-fetching it would tell the user
//     nothing new. What changed lives in the RECEIPT — one row per path — which
//     is why `summariseRevert` exists and why the outcome is rendered rather
//     than swallowed. An undo the surface does not report is indistinguishable
//     from the agent quietly writing again.

import type {
  HostWriteEntry,
  HostWriteKind,
  HostWriteRevertOutcome,
  HostWriteRevertReport,
} from "@0x-copilot/api-types";

/**
 * One tool call's worth of undoable changes — the unit the revert route
 * addresses, and therefore the only unit this surface offers a control for.
 */
export interface HostWriteGroup {
  /**
   * Stable list key. The tool-call id when there is one; the sentinel below
   * otherwise, so the unbound bucket cannot collide with a real id.
   */
  readonly key: string;
  /**
   * What `POST /host-writes/revert` would be given. `null` means these writes
   * were made outside a bound tool call and are reachable ONLY through a
   * whole-run revert — see `undoable`.
   */
  readonly toolCallId: string | null;
  /** The entries, oldest first, in the order the wire listed them. */
  readonly entries: readonly HostWriteEntry[];
  /**
   * Distinct paths this group would restore — the number of files an undo
   * actually touches, after the server's per-path collapse.
   */
  readonly pathCount: number;
  /**
   * Can this surface undo it? Requires BOTH a tool-call id to address (there is
   * no per-entry revert route) and at least one entry whose pre-image was
   * stored. A group of `revertible: false` captures would return a receipt of
   * `not_revertible` rows and change nothing — offering the button would be
   * promising an undo the backend already knows it cannot perform.
   */
  readonly undoable: boolean;
  /** The group's position in the run, for a stable oldest-first order. */
  readonly firstSequence: number;
}

/**
 * The key for writes the wire could not attribute to a tool call.
 *
 * A literal rather than the empty string so it can never be mistaken for a
 * tool-call id, and exported so a test names the contract instead of the
 * spelling.
 */
export const UNBOUND_HOST_WRITE_KEY = "host-writes:unbound";

/**
 * Group the flat listing into the units the revert route can address.
 *
 * Order is the wire's own: groups are sorted by their oldest entry, so the list
 * reads in the order the run did things. The unbound bucket takes the same rule
 * rather than being pinned to an end — it is not a footnote, it is writes that
 * happened at a particular moment.
 */
export function groupHostWrites(
  entries: readonly HostWriteEntry[],
): readonly HostWriteGroup[] {
  if (entries.length === 0) {
    return [];
  }
  const buckets = new Map<string, HostWriteEntry[]>();
  for (const entry of entries) {
    const key = groupKey(entry.tool_call_id ?? null);
    const bucket = buckets.get(key);
    if (bucket === undefined) {
      buckets.set(key, [entry]);
    } else {
      bucket.push(entry);
    }
  }
  const groups: HostWriteGroup[] = [];
  for (const [key, bucket] of buckets) {
    const toolCallId = key === UNBOUND_HOST_WRITE_KEY ? null : key;
    const paths = new Set(bucket.map((entry) => entry.path));
    groups.push({
      key,
      toolCallId,
      entries: bucket,
      pathCount: paths.size,
      undoable:
        toolCallId !== null &&
        bucket.some((entry) => entry.revertible === true),
      firstSequence: bucket.reduce(
        (lowest, entry) => Math.min(lowest, entry.sequence),
        Number.POSITIVE_INFINITY,
      ),
    });
  }
  return groups.sort((a, b) => a.firstSequence - b.firstSequence);
}

function groupKey(toolCallId: string | null): string {
  return toolCallId === null || toolCallId.length === 0
    ? UNBOUND_HOST_WRITE_KEY
    : toolCallId;
}

/** How one path's undo ended, in the words this surface prints. */
export interface HostWriteOutcomeRow {
  readonly path: string;
  readonly kind: HostWriteKind;
  /** The wire's status, verbatim — never re-spelled. */
  readonly status: string;
  /** True only for `restored` / `removed`: the file really moved back. */
  readonly undone: boolean;
  /** The producer's own explanation ("target is a symlink"), when it sent one. */
  readonly detail: string | null;
}

/**
 * The receipt for one revert — what an audit row would say, in a sentence.
 *
 * `undone` counts only `restored` and `removed`, mirroring
 * `HostWriteRevertReport.reverted` on the server so the two never disagree
 * about whether an undo worked. Every other status — including one this client
 * has never heard of — counts as NOT undone, which is the safe direction: a
 * client that treated an unknown status as success would tell the user their
 * file came back when the server said something else entirely.
 */
export interface HostWriteRevertSummary {
  readonly rows: readonly HostWriteOutcomeRow[];
  readonly undone: number;
  readonly total: number;
  /** Every path came back. */
  readonly complete: boolean;
  /** One line, safe to render on its own. */
  readonly headline: string;
}

/** The two statuses that mean the disk actually changed back. */
const UNDONE_STATUSES: ReadonlySet<string> = new Set(["restored", "removed"]);

export function summariseRevert(
  report: HostWriteRevertReport,
): HostWriteRevertSummary {
  const rows: HostWriteOutcomeRow[] = report.outcomes.map(toOutcomeRow);
  const undone = rows.filter((row) => row.undone).length;
  const total = rows.length;
  return {
    rows,
    undone,
    total,
    complete: total > 0 && undone === total,
    headline: headlineFor(undone, total),
  };
}

function toOutcomeRow(outcome: HostWriteRevertOutcome): HostWriteOutcomeRow {
  const status = String(outcome.status);
  return {
    path: outcome.path,
    kind: outcome.kind,
    status,
    undone: UNDONE_STATUSES.has(status),
    detail:
      typeof outcome.detail === "string" && outcome.detail.length > 0
        ? outcome.detail
        : null,
  };
}

/**
 * The receipt line.
 *
 * "Nothing was undone" is a real, reportable outcome, not an error: the server
 * audits an undo that restored nothing precisely because that is the event an
 * operator needs to see. Saying it plainly is the client half of that.
 */
function headlineFor(undone: number, total: number): string {
  if (total === 0) {
    return "Nothing to undo — no captured changes were selected.";
  }
  if (undone === 0) {
    return `Nothing was undone. ${plural(total, "file")} could not be put back.`;
  }
  if (undone === total) {
    return `Undone — ${plural(undone, "file")} put back.`;
  }
  return `Partly undone — ${undone} of ${total} files put back.`;
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

/** The word this surface prints for what the agent did to a path. */
export function hostWriteKindLabel(kind: HostWriteKind): string {
  switch (kind) {
    case "created":
      return "Created";
    case "deleted":
      return "Deleted";
    default:
      return "Modified";
  }
}

/**
 * The trailing filename, for a row that has to stay one line.
 *
 * The FULL path is still rendered — `RunHostWritesTab` prints it under the name
 * and the title carries it too. This is the recognisable half, not a
 * replacement: a user deciding whether to undo a write has to know which file,
 * and a middle-truncated absolute path is the shape that hides exactly that.
 */
export function hostWriteFileName(path: string): string {
  const trimmed = path.replace(/\/+$/, "");
  const cut = trimmed.lastIndexOf("/");
  const name = cut === -1 ? trimmed : trimmed.slice(cut + 1);
  return name.length > 0 ? name : path;
}
