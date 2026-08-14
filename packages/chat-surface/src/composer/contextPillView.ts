// contextPillView — the pure mapping from the two context read APIs onto what
// the composer's context meter draws.
//
// Lives beside the component (like `modelCatalog.ts`) rather than in
// `projections/`, but obeys the same rule: it is the SHARED half of the host
// binders. Web and desktop cannot share their fetch, so if this mapping were
// written twice the two hosts would drift — which is exactly how the model
// catalog opened the same user on two different models.
//
// TWO ENDPOINTS, TWO QUESTIONS, AND THEY ARE NOT INTERCHANGEABLE:
//
//   GET /v1/agent/conversations/{id}/context            -> how full is the window
//   GET /v1/agent/conversations/{id}/context/occupancy  -> who filled it
//
// The first owns `headroom_pct`, which the server computes and this module
// passes through UNTOUCHED (see `ContextPillView.headroomPct`). The second owns
// the decomposition. Neither is derivable from the other.
//
// NOT A STREAM PROJECTION. `context_occupancy` exists in the event vocabulary
// (`RuntimeApiEventType.CONTEXT_OCCUPANCY`) and `events.py` projects a payload
// and an activity_kind for it, but NOTHING emits it — the type is absent from
// `runtime_worker/streaming_executor.py`, where its sibling `usage_recorded` is
// emitted. A selector over `session.events` would therefore be correct, tested,
// green, and permanently empty. Read the API; revisit if §7's emitter lands.

import type {
  ConversationContextOccupancyResponse,
  ConversationContextResponse,
  ContextCacheEligibility,
  ContextLifecycle,
  ContextOccupancySegment,
  ContextOccupancySnapshot,
  ContextSegmentClass,
} from "@0x-copilot/api-types";

/** The reserved label for measured bytes matching no declaration.
 *  Mirrors `UNDECLARED_CONTEXT_LABEL` (`context_origin.py`). */
const UNDECLARED_LABEL = "UNDECLARED";

/** List key for the provider-overhead slice. Carries the `::` joiner that
 *  {@link segmentKey} uses so it can never collide with a real segment, whose
 *  left half is always a non-empty `owner:name`. */
const UNATTRIBUTED_SLICE_KEY = "::unattributed";

/** Headroom at or above this reads as unremarkable — no colour, no urgency. */
const WARM_BELOW_PCT = 40;
/** Headroom below this is the "compact before you send" band. */
const CRITICAL_BELOW_PCT = 15;

/**
 * Opacity steps applied to a class hue so sibling segments of the SAME class
 * stay distinguishable in the bar without spending a second hue on them.
 * Beyond the fourth, everything shares the floor — a fifth `tools:` server is
 * already below the resolution of a 4px meter.
 */
const TONE_STEPS: readonly number[] = [1, 0.72, 0.5, 0.38];
const TONE_FLOOR = 0.38;

/** Render order. Resident first: it is the group whose bytes recur on every
 *  call, so it is both the largest lever and the least obvious one. */
const LIFECYCLE_ORDER: readonly ContextLifecycle[] = [
  "resident",
  "per_result",
  "per_turn",
  "on_demand",
];

const LIFECYCLE_LABEL: Record<ContextLifecycle, string> = {
  resident: "Resident",
  per_result: "Per result",
  per_turn: "Per turn",
  on_demand: "On demand",
};

/** How much pressure the meter is under. Derived ONLY from the server's
 *  `headroom_pct`; an unknown window has no pressure to report. */
export type ContextPressure = "quiet" | "warm" | "critical";

export interface ContextSegmentRow {
  /** Stable list key — the wire label plus its detail, never re-ordered into. */
  readonly key: string;
  /** Display label: the owner namespace is stripped (`tools:linear` -> `linear`)
   *  because the group heading already says which lifecycle owns the row and the
   *  swatch already says which class. */
  readonly label: string;
  readonly detail: string | null;
  readonly segmentClass: ContextSegmentClass;
  /** Multiplier on the class hue — see {@link TONE_STEPS}. */
  readonly tone: number;
  readonly tokens: number;
  /** Share of the window, or `null` when the window size is unknown. */
  readonly pctOfWindow: number | null;
  /** Bytes the user did not author and can remove by disconnecting a server. */
  readonly thirdParty: boolean;
  /** `stable_prefix` — bills at roughly a tenth of a fresh send. */
  readonly cacheable: boolean;
  /** `counter_source: "proxy"` — the fail-open signature. The figure is an
   *  estimate the ledger fell back to, and the row says so rather than
   *  presenting a worse number with the same confidence as a tokenized one. */
  readonly approximate: boolean;
  /** Which group this row belongs to. Carried ON the row since it folds by
   *  `(lifecycle, label)` — a side-lookup keyed on label alone would misfile
   *  `conversation:tool_result` (per_result) into the transcript's group. */
  readonly lifecycle: ContextLifecycle;
  /** True for the synthetic "N more" row {@link foldTail} appends. It is a SUM,
   *  so it carries no markers of its own and must never be read as one
   *  declaration. */
  readonly remainder?: boolean;
}

export interface ContextLifecycleGroup {
  readonly lifecycle: ContextLifecycle;
  readonly label: string;
  /** The multiplier that makes the group actionable ("every call", "× 24
   *  results"), or `null` when the group has no meaningful one. */
  readonly note: string | null;
  readonly rows: readonly ContextSegmentRow[];
}

export interface ContextBarSlice {
  readonly key: string;
  /** `null` marks the unattributed provider delta, which is not a class. */
  readonly segmentClass: ContextSegmentClass | null;
  readonly tone: number;
  /** Share of the window, 0–100. */
  readonly pct: number;
}

export interface ContextPillView {
  /**
   * The server's integer percent of window REMAINING, passed through verbatim.
   *
   * Never re-derived from `available_tokens / context_window_tokens` — the
   * contract on `ContextCurrentSlice` forbids it, and the meter renders
   * headroom ("60% free") rather than consumption precisely so that no
   * `100 - x` arithmetic has to happen on the client either.
   *
   * `null` when the model is absent from the pricing catalogue.
   */
  readonly headroomPct: number | null;
  readonly pressure: ContextPressure;
  readonly modelLabel: string;
  /** `null` = unknown denominator. Every percentage in this view is then null
   *  too, and the meter falls back to absolute tokens. */
  readonly windowTokens: number | null;
  /** Headline input count. The PROVIDER's number when it reported one — it is
   *  authoritative and matches billing — falling back to our estimate. */
  readonly inputTokens: number;
  readonly cachedTokens: number;
  readonly freeTokens: number | null;
  readonly slices: readonly ContextBarSlice[];
  readonly groups: readonly ContextLifecycleGroup[];
  /** `provider_input_tokens - estimated_input_tokens`, SIGNED. Rendered as its
   *  own row rather than absorbed, so the segments visibly do not have to sum
   *  to the headline. Negative means we over-counted. */
  readonly unattributedDelta: number;
  /** Expected 0. Above it, a first-party contract defect — surfaced inside the
   *  popover only, never on the meter: it is our bug, and it does not change
   *  what the user should do next. */
  readonly undeclaredTokens: number;
  /** Most recent compaction, when the conversation has one. */
  readonly compaction: {
    readonly before: number;
    readonly after: number;
  } | null;
}

export interface BuildContextPillViewInput {
  /** `GET /v1/agent/conversations/{id}/context`. */
  readonly context: ConversationContextResponse | null;
  /** `GET /v1/agent/conversations/{id}/context/occupancy`. */
  readonly occupancy: ConversationContextOccupancyResponse | null;
}

/**
 * Build the meter's view model, or `null` when there is nothing honest to show.
 *
 * `null` covers every not-yet-measured case — a fresh conversation, a run that
 * predates the ledger, another tenant's id — deliberately without
 * distinguishing them, exactly as the endpoint does. The meter then renders
 * NOTHING: no skeleton, no spinner, no zero. A zeroed meter is a claim.
 */
export function buildContextPillView({
  context,
  occupancy,
}: BuildContextPillViewInput): ContextPillView | null {
  const snapshot = occupancy?.snapshot ?? null;
  if (snapshot === null && context === null) return null;

  // EVERY read below optional-chains through the nested objects, not just
  // through the response itself. A 200 carrying a partial or error-shaped body
  // gives a truthy `context` whose `model` / `current` never arrived, and a
  // meter is not worth crashing a composer over: the failure mode has to be a
  // missing meter, never a missing send button.
  //
  // The window denominator. The snapshot's is preferred — it is the window the
  // model call actually ran against, including on an alternate-route retry to a
  // differently sized deployment.
  //
  // Branch on the SNAPSHOT's presence, not on `??` over its field. Both sources
  // use `null` to mean "this model is not in the pricing catalogue", so `??`
  // would read that deliberate unknown as "absent" and quietly substitute the
  // other endpoint's number — reporting a confident percentage against a
  // denominator the measured call never had.
  const windowTokens =
    snapshot !== null
      ? snapshot.context_window_tokens
      : (context?.model?.context_window_tokens ?? null);

  const headroomPct = context?.current?.headroom_pct ?? null;

  const inputTokens =
    snapshot?.provider_input_tokens ??
    snapshot?.estimated_input_tokens ??
    context?.current?.input_tokens ??
    0;

  // Nothing measured and nothing counted: there is no meter to draw.
  if (snapshot === null && inputTokens === 0 && headroomPct === null) {
    return null;
  }

  const cachedTokens =
    snapshot?.cached_input_tokens ?? context?.current?.cached_input_tokens ?? 0;
  // Same deliberate-null rule as `windowTokens` above: an unknown window has no
  // remainder, and borrowing the other endpoint's would name a figure this
  // scope cannot support.
  const freeTokens =
    snapshot !== null
      ? snapshot.free_tokens
      : (context?.current?.available_tokens ?? null);

  const segments = Array.isArray(snapshot?.segments) ? snapshot.segments : [];
  const rows = buildRows(segments, windowTokens);

  return {
    headroomPct,
    pressure: pressureFor(headroomPct),
    modelLabel: modelLabelFor(context, snapshot),
    windowTokens,
    inputTokens,
    cachedTokens,
    freeTokens,
    slices: buildSlices(rows, snapshot, windowTokens),
    groups: buildGroups(rows, windowTokens),
    unattributedDelta: snapshot?.unattributed_delta ?? 0,
    undeclaredTokens: snapshot?.undeclared_tokens ?? 0,
    compaction: latestCompaction(context),
  };
}

/**
 * Pressure bands.
 *
 * An unknown window is `quiet`, not `critical`. Escalating on missing data
 * would put an ember meter in front of every user whose model is simply absent
 * from the pricing catalogue.
 */
function pressureFor(headroomPct: number | null): ContextPressure {
  if (headroomPct === null) return "quiet";
  if (headroomPct < CRITICAL_BELOW_PCT) return "critical";
  if (headroomPct < WARM_BELOW_PCT) return "warm";
  return "quiet";
}

function modelLabelFor(
  context: ConversationContextResponse | null,
  snapshot: ContextOccupancySnapshot | null,
): string {
  const name = context?.model?.name ?? snapshot?.model_family ?? "";
  return name.trim() === "" ? "Model" : name;
}

/**
 * One row per DECLARATION, not per measured segment.
 *
 * The ledger emits a segment per contribution, and `detail` is its bounded
 * sub-identity — a message ordinal, a tool name. So a real snapshot carries
 * `conversation:tool_result` eight times as `msg[10]…msg[17]`, the transcript
 * ten times, and `UNDECLARED` once per undeclared tool. Rendered one-to-one
 * that is 44 rows in a 300px frame, and `tool_result · msg[13]` is not a thing
 * anyone can act on — the actionable fact is "tool results, ×8, 8,492".
 *
 * So segments fold on `(lifecycle, label)`, the summed `item_count` becomes the
 * multiplicity, and the ordinals are dropped. Measured against a live run this
 * is 44 rows -> 16; {@link foldTail} then handles the long tail.
 *
 * Fixtures hid this: one segment per label reads identically either way. The
 * shape only shows up against a stack that has actually run.
 *
 * Zero-token segments are dropped — a declaration that measured to nothing is a
 * true fact about the request and a useless row.
 */
function buildRows(
  segments: readonly ContextOccupancySegment[],
  windowTokens: number | null,
): ContextSegmentRow[] {
  const folded = new Map<string, FoldedSegment>();
  for (const segment of segments) {
    if (segment.estimated_tokens <= 0) continue;
    const key = `${segment.lifecycle}::${segment.label}`;
    const prior = folded.get(key);
    if (prior === undefined) {
      folded.set(key, {
        key,
        label: segment.label,
        segmentClass: segment.segment_class,
        lifecycle: segment.lifecycle,
        tokens: segment.estimated_tokens,
        items: Math.max(1, segment.item_count),
        // ANY contributor being third-party makes the group removable by
        // disconnecting something, which is the point of the marker.
        thirdParty: segment.third_party,
        // ALL, not any: claiming a cached prefix for a group that is only
        // partly cacheable understates what the next turn will be billed.
        cacheable: isCacheable(segment.cache_eligibility),
        // ANY proxy count makes the SUM approximate.
        approximate: segment.counter_source === "proxy",
      });
      continue;
    }
    prior.tokens += segment.estimated_tokens;
    prior.items += Math.max(1, segment.item_count);
    prior.thirdParty ||= segment.third_party;
    prior.cacheable &&= isCacheable(segment.cache_eligibility);
    prior.approximate ||= segment.counter_source === "proxy";
  }

  const scored = [...folded.values()].sort((a, b) => b.tokens - a.tokens);
  const seenPerClass = new Map<ContextSegmentClass, number>();

  return scored.map((entry) => {
    const rank = seenPerClass.get(entry.segmentClass) ?? 0;
    seenPerClass.set(entry.segmentClass, rank + 1);
    return {
      key: entry.key,
      label: displayLabel(entry.label),
      // The multiplicity IS the detail now. `× 8` is what makes "shrink the
      // per-result note" the obvious move; `msg[13]` never did.
      detail: entry.items > 1 ? `× ${String(entry.items)}` : null,
      segmentClass: entry.segmentClass,
      tone: TONE_STEPS[rank] ?? TONE_FLOOR,
      tokens: entry.tokens,
      pctOfWindow: shareOfWindow(entry.tokens, windowTokens),
      thirdParty: entry.thirdParty,
      cacheable: entry.cacheable,
      approximate: entry.approximate,
      lifecycle: entry.lifecycle,
    };
  });
}

interface FoldedSegment {
  key: string;
  label: string;
  segmentClass: ContextSegmentClass;
  lifecycle: ContextLifecycle;
  tokens: number;
  items: number;
  thirdParty: boolean;
  cacheable: boolean;
  approximate: boolean;
}

/** Rows shown per lifecycle group before the tail is folded. */
const ROWS_PER_GROUP = 4;

/**
 * Keep the rows that carry the finding; fold the rest into ONE row that names
 * what it swallowed.
 *
 * Even folded by declaration, `resident` on a live run is eleven first-party
 * tool schemas at 200–1,400 tokens each. Nobody acts on `revise_artifact: 722`
 * — they act on the total. But a silent top-4 would read as "that is
 * everything", so the remainder is a row with its own count and tokens. This is
 * the same rule as "no silent caps": the disclosure IS the cap.
 */
function foldTail(
  rows: readonly ContextSegmentRow[],
  windowTokens: number | null,
): ContextSegmentRow[] {
  if (rows.length <= ROWS_PER_GROUP + 1) return [...rows];
  const head = rows.slice(0, ROWS_PER_GROUP);
  const tail = rows.slice(ROWS_PER_GROUP);
  const tokens = tail.reduce((sum, row) => sum + row.tokens, 0);
  return [
    ...head,
    {
      key: `${head[0]?.key ?? ""}::more`,
      label: `${String(tail.length)} more`,
      detail: null,
      segmentClass: tail[0]!.segmentClass,
      tone: TONE_FLOOR,
      tokens,
      pctOfWindow: shareOfWindow(tokens, windowTokens),
      thirdParty: tail.some((row) => row.thirdParty),
      cacheable: false,
      approximate: tail.some((row) => row.approximate),
      lifecycle: tail[0]!.lifecycle,
      remainder: true,
    },
  ];
}

/**
 * Identity of one segment within a snapshot.
 *
 * `label` alone is not unique — the same declaration can contribute several
 * rows distinguished only by `detail` (a tool name, a message ordinal range).
 * `::` is the joiner because neither half may contain it: a label is
 * `owner:name` with a single colon, and a detail is a bounded sub-identity.
 */
function segmentKey(segment: ContextOccupancySegment): string {
  return `${segment.label}::${segment.detail ?? ""}`;
}

/**
 * Strip the owner namespace for display.
 *
 * The label is an OPAQUE grouping key per the contract — never switch on its
 * value. Splitting on the first `:` is presentation, and it degrades to the
 * whole string when there is no namespace, so an unrecognised shape still
 * renders its own name rather than an empty cell.
 */
function displayLabel(label: string): string {
  if (label === UNDECLARED_LABEL) return "undeclared";
  const separator = label.indexOf(":");
  if (separator < 0) return label;
  const tail = label.slice(separator + 1).trim();
  return tail === "" ? label : tail;
}

function isCacheable(eligibility: ContextCacheEligibility | null): boolean {
  return eligibility === "stable_prefix";
}

function shareOfWindow(
  tokens: number,
  windowTokens: number | null,
): number | null {
  if (windowTokens === null || windowTokens <= 0) return null;
  return (tokens / windowTokens) * 100;
}

/** Class order along the bar. Fixed rather than data-derived, so the same
 *  conversation does not reshuffle its bar between turns as sizes change. */
const CLASS_BAR_ORDER: readonly ContextSegmentClass[] = [
  "tools",
  "messages",
  "system",
  "response_format",
];

/**
 * The stacked bar: one slice per row, plus the unattributed delta.
 *
 * Slices are the CONSUMED portion only — the track behind them is the headroom
 * the number names, so the bar and the percent are two independent server
 * values that agree rather than one derived from the other.
 *
 * GROUPED BY CLASS, unlike the rows below it. The rows answer "what should I
 * do about this", so they group by lifecycle; the bar answers "what is this
 * window made of", and a class whose slices are scattered along it cannot be
 * read as a quantity at all. Sorting by token size alone interleaved them
 * (tools · messages · messages · tools · system …), which drew seven stripes
 * and communicated none of the four totals. Correspondence with the rows is
 * carried by COLOUR, not position, so regrouping costs nothing.
 *
 * A negative `unattributed_delta` (we over-counted) contributes no slice: a
 * bar cannot draw negative width, and the signed figure is still reported in
 * full on its own row.
 */
function buildSlices(
  rows: readonly ContextSegmentRow[],
  snapshot: ContextOccupancySnapshot | null,
  windowTokens: number | null,
): ContextBarSlice[] {
  const slices: ContextBarSlice[] = [];
  for (const segmentClass of CLASS_BAR_ORDER) {
    for (const row of rows) {
      if (row.segmentClass !== segmentClass) continue;
      if (row.pctOfWindow === null || row.pctOfWindow <= 0) continue;
      slices.push({
        key: row.key,
        segmentClass: row.segmentClass,
        tone: row.tone,
        pct: row.pctOfWindow,
      });
    }
  }
  const delta = snapshot?.unattributed_delta ?? 0;
  const deltaPct = delta > 0 ? shareOfWindow(delta, windowTokens) : null;
  if (deltaPct !== null && deltaPct > 0) {
    slices.push({
      key: UNATTRIBUTED_SLICE_KEY,
      segmentClass: null,
      tone: 1,
      pct: deltaPct,
    });
  }
  return slices;
}

/** Group rows by lifecycle, in {@link LIFECYCLE_ORDER}, dropping empties. */
function buildGroups(
  rows: readonly ContextSegmentRow[],
  windowTokens: number | null,
): ContextLifecycleGroup[] {
  const groups: ContextLifecycleGroup[] = [];
  for (const lifecycle of LIFECYCLE_ORDER) {
    const owned = rows.filter((row) => row.lifecycle === lifecycle);
    if (owned.length === 0) continue;
    groups.push({
      lifecycle,
      label: LIFECYCLE_LABEL[lifecycle],
      note: groupNote(lifecycle),
      rows: foldTail(owned, windowTokens),
    });
  }
  return groups;
}

/**
 * What recurrence the group describes.
 *
 * Deliberately NOT a count. Each row now carries its own exact `× N` from the
 * fold, so a group-level number would be a second, vaguer one — and the obvious
 * formulations are all wrong: a max over `item_count` reads "× 1 results" once
 * segments are one-per-contribution, and a sum counts budget notes as results.
 * The heading names the RECURRENCE; the rows own the arithmetic.
 */
function groupNote(lifecycle: ContextLifecycle): string | null {
  if (lifecycle === "resident") return "every call";
  if (lifecycle === "per_result") return "scales with results";
  if (lifecycle === "per_turn") return "scales with turns";
  return null;
}

/** The most recent compaction, by `at`. Sorting rather than trusting order:
 *  the contract does not promise one. */
function latestCompaction(
  context: ConversationContextResponse | null,
): { readonly before: number; readonly after: number } | null {
  const events = context?.breakdown?.compression_events ?? [];
  if (events.length === 0) return null;
  const latest = [...events].sort((a, b) => a.at.localeCompare(b.at)).at(-1);
  return latest === undefined
    ? null
    : { before: latest.before, after: latest.after };
}
