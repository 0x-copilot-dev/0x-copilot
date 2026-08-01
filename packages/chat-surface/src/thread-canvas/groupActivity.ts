// groupActivity — fold the merged transcript into tool-run groups (PRD-03).
//
// Source: docs/plan/windowed-mode/PRD-03-transcript-density.md D-3.1.
//
// The finding: a run that did one small thing rendered six bordered tool cards
// above a one-line answer, so process was loud and the conclusion was quiet.
// The individual card is already compact (`activityCardChrome`); what was
// missing is any layer ABOVE it. Codex collapses the same thing to
// `Worked for 26s ›`.
//
// This is a PURE fold over what `TcChat` already merges — no new state, no new
// events, and `useEventProjector` remains the single projection (FR-3.3).
//
// ── The contract is OPT-IN, and that is the load-bearing decision ───────────
//
// The caller names what is groupable; everything else passes through untouched.
// The fold does NOT enumerate boundaries, because the boundary set grows: while
// this was being written, `mergeStream` gained an `approval` kind. A fold that
// listed boundaries would have swallowed approvals into a collapsed group —
// burying the only control a parked run gives the user, which is precisely the
// class of bug `TcChat`'s own comments warn about ("must never early-return
// past the cards — inline, that hid a parked run's only way out").
//
// Opt-in means a new stream kind defaults to SAFE (visible, ungrouped) rather
// than to hidden.

/** What the fold emits. `passthrough` is anything the caller did not opt in. */
export type GroupedStreamItem<TItem> =
  | { readonly kind: "passthrough"; readonly item: TItem }
  | {
      /**
       * A LONE groupable item. D-3.4 — wrapping one card in a group adds a
       * frame to save nothing, so it renders exactly as it does today.
       */
      readonly kind: "solo";
      readonly item: TItem;
    }
  | {
      /** >= 2 groupable items that render inside one collapsible group. */
      readonly kind: "group";
      /** Stable across re-renders: the first member's id. */
      readonly id: string;
      readonly members: readonly TItem[];
    };

/** Minimum members before a run is worth a group wrapper (D-3.4). */
export const GROUP_MIN_MEMBERS = 2;

export interface GroupActivityOptions<TItem> {
  /** True for items that may be folded together (tool calls, subagent fleets). */
  readonly isGroupable: (item: TItem) => boolean;
  /** Stable id for a groupable item; only the first member's is used. */
  readonly idOf: (item: TItem) => string;
}

/**
 * Fold a merged transcript into groups.
 *
 * A group is a maximal consecutive run of groupable items. Everything else —
 * messages, approvals, and any kind added later — passes through in place, so
 * the transcript's reading order is never changed, only its framing.
 */
export function groupActivityStream<TItem>(
  items: readonly TItem[],
  options: GroupActivityOptions<TItem>,
): readonly GroupedStreamItem<TItem>[] {
  const { isGroupable, idOf } = options;
  const out: GroupedStreamItem<TItem>[] = [];
  let run: TItem[] = [];

  const flush = (): void => {
    if (run.length === 0) {
      return;
    }
    if (run.length < GROUP_MIN_MEMBERS) {
      for (const item of run) {
        out.push({ kind: "solo", item });
      }
    } else {
      out.push({ kind: "group", id: idOf(run[0]), members: run });
    }
    run = [];
  };

  for (const item of items) {
    if (isGroupable(item)) {
      run.push(item);
      continue;
    }
    flush();
    out.push({ kind: "passthrough", item });
  }
  flush();
  return out;
}

// ===========================================================================
// Summary-line state
// ===========================================================================

export type GroupRunState = "running" | "settled" | "failed";

export interface GroupSummary {
  readonly state: GroupRunState;
  /** Members that have settled (any non-running status). */
  readonly done: number;
  readonly total: number;
  /** Members whose failure the run recovered from (PRD-04's `recovered`). */
  readonly retried: number;
  /** `max(updatedAt) - min(startedAt)` in ms, or `null` when unknowable. */
  readonly elapsedMs: number | null;
}

/** The member shape the summary reads. Structural, so the caller's own union
 *  (a `StreamItem`) satisfies it without this module importing it. */
export interface GroupMemberLike {
  readonly status?: string;
  readonly createdAtMs?: number | null;
  readonly durationMs?: number;
}

/**
 * Derive the group's summary from its members.
 *
 * `runFailed` is the RUN's own terminal state, not a member's: a single failed
 * step that the agent worked around must not keep the group open (D-3.5 vs
 * PRD-04's `recovered`).
 */
export function summariseGroup(
  members: readonly GroupMemberLike[],
  runFailed = false,
): GroupSummary {
  let done = 0;
  let running = 0;
  let retried = 0;
  let minStart: number | null = null;
  let maxEnd: number | null = null;

  for (const member of members) {
    if (member.status === "running") {
      running += 1;
    } else {
      done += 1;
    }
    // PRD-04 threads `outcome` onto error entries; until it lands, an error
    // that the run survived is indistinguishable from one that killed it, so
    // we only count what we can honestly know.
    if (member.status === "error" && !runFailed) {
      retried += 1;
    }
    const started = member.createdAtMs ?? null;
    if (started !== null) {
      minStart = minStart === null ? started : Math.min(minStart, started);
      const ended =
        member.durationMs !== undefined ? started + member.durationMs : started;
      maxEnd = maxEnd === null ? ended : Math.max(maxEnd, ended);
    }
  }

  const state: GroupRunState = runFailed
    ? "failed"
    : running > 0
      ? "running"
      : "settled";
  const elapsedMs =
    minStart !== null && maxEnd !== null && maxEnd >= minStart
      ? maxEnd - minStart
      : null;

  return { state, done, total: members.length, retried, elapsedMs };
}
