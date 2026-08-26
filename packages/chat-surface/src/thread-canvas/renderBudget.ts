// renderBudget — bound what a long run MOUNTS, without measuring anything.
//
// ── The finding ────────────────────────────────────────────────────────────
//
// Nothing in this package windows the transcript. A run that made 300 tool
// calls mounts 300 `ToolCallCard`s, every reasoning span, and every file diff
// under them, simultaneously and forever.
//
// PRD-03's density work looks like it already solved this and does not:
// `groupActivityStream` folds a run of activity into one `ToolRunGroup`, but
// that group is a native `<details>` that renders `{children}` unconditionally.
// A collapsed `<details>` HIDES its subtree; it does not unmount it. So the
// visual noise went away and the mount cost did not move at all. The same is
// true of `ReasoningGroup` and of every `<details>` inside a tool card.
//
// ── Why a budget and not a virtualizer ─────────────────────────────────────
//
// A measuring virtualizer (window + spacer + `ResizeObserver`) is the textbook
// answer and it is the wrong one here, for three reasons that are properties of
// THIS transcript rather than opinions about virtualization:
//
//  1. IT CANNOT BE VERIFIED IN THE ONLY HARNESS THIS PACKAGE HAS. jsdom runs no
//     layout: `getBoundingClientRect` returns zeros and `ResizeObserver` never
//     fires. Every height such a windower reads would be 0 under test, so it
//     would either render everything (the tests prove nothing) or render
//     nothing (the tests prove the opposite of what ships). This repo has
//     already paid for "a green DOM assertion is not a green screen".
//  2. UNMOUNTING A ROW DESTROYS STATE THE DOM OWNS AND REACT DOES NOT SEE — an
//     open `<details>`, a payload scrolled to line 400, a half-typed answer in
//     `QuestionCard`'s textarea. A scroll-driven window loses those every time
//     the reader scrolls past.
//  3. A ROW OUTSIDE THE WINDOW IS NOT IN THE DOCUMENT. Seven fs journeys press
//     approvals by `[data-testid^=tc-chat-approval-approve-]` at document
//     level; an approval that is merely off-screen is still findable, one that
//     is virtualized out is not. That is the same class of failure this file's
//     neighbours already warn about — hiding a parked run's only way out.
//
// A render budget gives up the last increment of the win (it never elides the
// newest rows, and it never elides prose) and buys all three back: it measures
// nothing, so it costs nothing at the substrate boundary; it is a pure fold, so
// it is a `expect(...)` away from being provable; and what it withholds is
// PROCESS, chosen by an opt-in predicate, so a kind nobody thought about
// defaults to visible rather than to hidden.
//
// The contract is deliberately the same shape as `groupActivity.ts`'s, and for
// the same documented reason: the caller names what may be folded, the boundary
// set is never enumerated here, and a stream kind added later is SAFE by
// default. The consequence of getting it wrong is sharper here than there,
// though — a wrongly-grouped card is hidden behind a disclosure and still in
// the document, a wrongly-elided one is gone — so the predicate at the call
// site is the whole safety argument, not a detail of it.

/** What the fold emits. */
export type BudgetedEntry<TItem> =
  | { readonly kind: "rendered"; readonly item: TItem }
  | {
      /** A maximal run of elidable items withheld from the mount. */
      readonly kind: "elided";
      /** Index of the run's first member — stable enough for a React key. */
      readonly id: number;
      /** Rows withheld (the members' summed `weightOf`), for the summary line. */
      readonly weight: number;
      /** The members themselves, so a caller can count or expand them. */
      readonly items: readonly TItem[];
    };

export interface RenderBudgetOptions<TItem> {
  /**
   * Rows of the tail that always mount. `Infinity` disables the fold entirely,
   * which is how a caller expresses "the reader asked for all of it".
   */
  readonly budget: number;
  /**
   * Hysteresis. See `keepWeight` below — this is how far the tail is allowed to
   * grow before the boundary moves, and therefore how RARELY content is removed
   * from above the reader.
   */
  readonly slack?: number;
  /**
   * True for items that may be withheld. OPT-IN: everything else is rendered,
   * including any kind added after this was written.
   */
  readonly isElidable: (item: TItem) => boolean;
  /**
   * Rows this one item mounts. Defaults to 1.
   *
   * Load-bearing, not decoration: the point of the budget is to bound MOUNTED
   * ROWS, and one stream entry does not always mount one row. A reasoning part
   * that `absorbThoughtActivity` folded 30 tool cards into is a single entry
   * that mounts 31 — a budget counting entries would let it straight through.
   */
  readonly weightOf?: (item: TItem) => number;
}

/** Rows of the tail that always mount. ~4–6 screens of scrollback at 13px. */
export const DEFAULT_RENDER_BUDGET = 60;

/** How far the tail grows before the boundary is allowed to move. */
export const DEFAULT_RENDER_BUDGET_SLACK = 40;

/**
 * Fold a transcript into the rows that mount and the runs that do not.
 *
 * Reading order is never changed, only membership: an elided run appears
 * exactly where its members were, and a non-elidable item inside that stretch
 * BREAKS the run and renders in place rather than being hoisted anywhere.
 * That is what lets an approval keep its anchor — it is drawn beside the tool
 * call that provoked it, with the work around it summarised.
 *
 * ── Why the tail is `keepWeight` and not `budget` ──────────────────────────
 *
 * The naive boundary — "keep the last `budget` rows" — advances by one on every
 * streamed token, so one row is torn out from above the reader per frame. That
 * is more layout work than mounting everything, and it is content disappearing
 * under someone's eyes while they read.
 *
 * `keepWeight` grows with the transcript and snaps back every `slack` rows, so
 * the boundary index is STATIONARY between snaps: the suffix gains a row and
 * the allowance gains a row, and they cancel. Content is removed from above the
 * reader once per `slack` arrivals, not once per arrival.
 *
 * The residual is honest and worth stating: at a snap, `slack` rows do leave
 * the top of the rendered region. A reader scrolled to the bottom (the normal
 * case while streaming) never notices — the browser's default scroll anchoring
 * absorbs a change above the anchor. A reader scrolled UP into exactly that
 * stretch can be jumped. The escape hatch is the summary row's expand, which is
 * sticky for the same reason `ToolRunGroup` pins on interaction: a transcript
 * that re-hides something you deliberately opened is hostile.
 */
export function applyRenderBudget<TItem>(
  items: readonly TItem[],
  options: RenderBudgetOptions<TItem>,
): readonly BudgetedEntry<TItem>[] {
  const { budget, isElidable } = options;
  const slack = Math.max(
    1,
    Math.floor(options.slack ?? DEFAULT_RENDER_BUDGET_SLACK),
  );
  const weightOf = options.weightOf ?? (() => 1);

  const weights = items.map((item) => Math.max(0, weightOf(item)));
  let totalWeight = 0;
  for (const weight of weights) totalWeight += weight;

  // The identity case, and it must stay byte-identical: a transcript that fits
  // renders exactly as it did before this module existed. `Infinity` lands here
  // too, which is how the expanded state is expressed with no second code path.
  if (totalWeight <= budget) {
    return items.map((item) => ({ kind: "rendered", item }) as const);
  }

  const keepWeight = budget + ((totalWeight - budget) % slack);

  // The untouchable tail: walk back from the end until the next row would not
  // fit in the allowance. Everything from `tailStart` on renders, whatever it
  // is — the newest cards are the ones being watched.
  let tailStart = 0;
  let suffix = 0;
  for (let i = items.length - 1; i >= 0; i -= 1) {
    if (suffix + weights[i] > keepWeight) {
      // Never past the last item. One entry CAN outweigh the whole allowance —
      // a reasoning span with 100 tool cards absorbed into it is a single item
      // worth 101 rows — and without this clamp the newest thing in the
      // transcript would be the first thing withheld.
      tailStart = Math.min(i + 1, items.length - 1);
      break;
    }
    suffix += weights[i];
  }

  const out: BudgetedEntry<TItem>[] = [];
  let run: TItem[] = [];
  let runWeight = 0;
  let runStart = 0;

  const flush = (): void => {
    if (run.length === 0) return;
    out.push({ kind: "elided", id: runStart, weight: runWeight, items: run });
    run = [];
    runWeight = 0;
  };

  for (let i = 0; i < tailStart; i += 1) {
    const item = items[i];
    if (!isElidable(item)) {
      flush();
      out.push({ kind: "rendered", item });
      continue;
    }
    if (run.length === 0) runStart = i;
    run.push(item);
    runWeight += weights[i];
  }
  flush();

  for (let i = tailStart; i < items.length; i += 1) {
    out.push({ kind: "rendered", item: items[i] });
  }
  return out;
}

/** Rows this fold actually mounts — the number the budget exists to bound. */
export function renderedWeight<TItem>(
  entries: readonly BudgetedEntry<TItem>[],
  weightOf: (item: TItem) => number = () => 1,
): number {
  let total = 0;
  for (const entry of entries) {
    if (entry.kind === "rendered") total += weightOf(entry.item);
  }
  return total;
}
