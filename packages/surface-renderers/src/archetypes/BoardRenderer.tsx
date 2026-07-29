import type { CSSProperties, ReactElement } from "react";

import type { SaaSRendererAdapter } from "@0x-copilot/chat-surface";

import { SURFACE_PALETTE as PALETTE } from "../_shared/palette";
import {
  cardStyle,
  DiffFieldRow,
  EmptyBody,
  fieldGridStyle,
  NoSpecView,
  pageStyle,
  SurfaceHeader,
  SurfaceLinkRow,
  toolNameFromState,
} from "../_shared/primitives";
import { formatValue, resolvePath } from "../_shared/path";
import {
  changesFromDiff,
  dataFromState,
  specFromState,
  type SurfaceColumn,
  type SurfaceDiff,
  type SurfaceFieldChange,
  type SurfaceSpec,
  type SurfaceState,
} from "../_shared/specTypes";

const KICKER = "Board";

/** Cap on cards painted across all lanes — render-budget guard (PRD-03). */
export const CARD_RENDER_CAP = 200;

const UNGROUPED = "Ungrouped";

/** The design's `--mono` at its 9.5px rung, and the eyebrow tracking step the
 * design system folds the design's 0.11em into. Both carry a literal fallback:
 * a host that renders a surface without the design-system sheet loaded must
 * still get mono micro-type, not a 16px sans meta row. */
const MONO_FAMILY = "var(--font-mono, ui-monospace, SFMono-Regular, monospace)";
const MONO_MICRO = "var(--font-size-mono-9-5, 9.5px)";
const CAPS_TRACKING = "var(--tracking-eyebrow, 0.1em)";

/**
 * The design's `--accent-line`: the accent at hairline strength, one rung below
 * the inset bar, so a changed card is findable without shouting.
 *
 * Named here rather than read from `SURFACE_PALETTE` because the palette has no
 * hairline-accent rung and PRD-01 does not own that file. The fallback keeps a
 * host that has not loaded the design-system sheet on the palette's accent
 * instead of on nothing at all.
 */
const ACCENT_LINE = `var(--color-accent-line, ${PALETTE.lime})`;

/**
 * Three rungs the board needs that `SURFACE_PALETTE` does not name, each one
 * step QUIETER than the palette's nearest entry.
 *
 * The palette resolves `border` → `--color-border-strong`, `surfaceMute` →
 * `--color-surface-elevated`, `textLo` → `--color-text-muted`. Measured against
 * the design those are the `--line2` / `--panel3` / `--mut` rungs, and the board
 * is drawn one step below all three: `--line` for the lane grid and the card
 * hairline, `--panel2` for the card ground, `--mut2` for every label on it.
 *
 * `--panel2` is the one worth reading twice. PRD-01's token map says
 * `--panel2` → `--color-surface-elevated`, and that is wrong by measurement:
 * `--color-surface-elevated` is `#1d1d23`, which is the design's `--panel3`.
 * The card ground the design actually paints is `--panel2` = `#16161a` =
 * `--color-surface-muted`. The rung that MEASURES right wins over the one whose
 * name reads right.
 *
 * Named here rather than added to `SURFACE_PALETTE` because that file is shared
 * by eleven renderers and this change does not own it — same reason
 * `ACCENT_LINE` above is local. Every value is still a design-system token, so
 * theme and accent switches reach them; none is a literal.
 */
const HAIRLINE = "var(--color-border)";
const CARD_GROUND = "var(--color-surface-muted)";
// NOT `--color-text-subtle`, which is the literal counterpart of the design's
// `--mut2` and is what parity measures against. Matching the token NAME across
// two different neutral ladders is not fidelity — it is a contrast regression.
// The design draws `--mut2` on its own lighter `--panel`; on our darker ground
// the same rung lands at 3.22:1 against `--color-surface` and 3.08:1 on the card,
// under the 4.5:1 AA floor these 9.5px labels need. `--color-text-muted` holds
// 6.58:1 and reads as the same quiet register to the eye.
//
// So four `--mut2` rows in the parity report will stay open by choice. That is
// the honest outcome: a legible label the design did not specify beats an
// illegible one it did.
const LABEL_QUIET = "var(--color-text-muted)";

/** A card plus its index in the ORIGINAL item list (grouping reorders cards,
 * and the change marks are keyed by that original index). */
interface LaneCard {
  readonly item: unknown;
  readonly index: number;
}

/**
 * The attention mark on one card. Presence in the map IS the mark; `transition`
 * is the lane the card is moving TO, and exists only when a change names the
 * spec's `group_by_path` — a card whose owner was edited has moved nowhere, so
 * there is no transition to state.
 */
interface CardMark {
  readonly transition: string | undefined;
}

const NO_MARKS: ReadonlyMap<number, CardMark> = new Map<number, CardMark>();

/** One fact in a card's meta row. */
interface MetaEntry {
  readonly key: string;
  readonly label: string;
  readonly value: string;
}

/**
 * The facts a card actually has, in spec column order.
 *
 * Columns whose value resolves to nothing are DROPPED, not rendered empty. A
 * table keeps an empty cell because the grid has to stay aligned; a card meta
 * row has no alignment to keep, so an absent value leaves a bare label —
 * "OWNER unassigned DUE" — which reads as a fact that got cut off rather than
 * as one that was never there. Real boards are full of half-filled cards, so
 * this is the common case, not the edge case.
 */
function metaEntries(
  item: unknown,
  columns: readonly SurfaceColumn[],
): readonly MetaEntry[] {
  const entries: MetaEntry[] = [];
  columns.forEach((column, index) => {
    const value = formatValue(resolvePath(item, column.path), column.format);
    if (value === "") {
      return;
    }
    entries.push({
      key: `${column.path}:${index}`,
      label: column.label,
      value,
    });
  });
  return entries;
}

/**
 * The `board://` archetype — lanes grouped by `group_by_path`, cards from
 * `items_path` (title = first column, remaining columns as card fields).
 * Spec-less state falls back to the generic list.
 *
 * `trustedChanges` is the attention mark's ONLY input, and it is a second
 * PARAMETER rather than a key of `state` on purpose. See the note on
 * `cardMarks` — reading it out of `state` made the mark forgeable and honestly
 * unreachable at the same time. The name states the caller's obligation: the
 * type cannot enforce provenance, and nothing in the product supplies it today.
 */
export function BoardRenderer(
  state: SurfaceState | unknown,
  trustedChanges?: readonly SurfaceFieldChange[],
): ReactElement {
  const spec = specFromState(state);
  const data = dataFromState(state);
  return (
    <article
      style={pageStyle}
      data-testid="board-renderer"
      data-mode="current"
      data-spec={spec ? "present" : "absent"}
      aria-label="Board surface"
    >
      <section style={cardStyle}>
        {spec
          ? renderWithSpec(spec, data, cardMarks(trustedChanges, spec))
          : renderFallback(state, data)}
      </section>
    </article>
  );
}

/**
 * Which cards carry the attention mark, keyed by their index into `items_path`.
 *
 * WHY THIS TAKES A PARAMETER AND NOT THE STATE. This function used to read the
 * change list off the rendered `state`, and that had exactly one reachable
 * trigger — a forged one:
 *
 *  - `SurfaceState` is `{spec?, data}` in `@0x-copilot/api-types` and in
 *    ai-backend's `spec_models.py`, where it is a `RuntimeContract`
 *    (`extra="forbid"`); `SurfaceProjector.resolve()` puts the whole tool
 *    output under `data`. No trusted producer can put a `changes` sibling in a
 *    current-state payload, so no honest card was ever marked.
 *  - `TcSurfaceMount` calls `pendingDiff ? renderDiff(diff) : renderCurrent(state)`,
 *    so a real `SurfaceDiff` never reaches this render either.
 *  - But `applySurfaceEvent` stores `{...prior, ...(envelope.state ?? payload.state
 *    ?? payload.result)}` verbatim, and `tool_result` payloads pass no
 *    allow-list, so EVERY key of an untrusted tool's output survives into what
 *    `renderCurrent` is handed. A tool emitting
 *    `state:{spec,data,changes:[{field:"cards.0"}]}` lit the accent bar on card
 *    0 — i.e. a tool could manufacture "you owe a decision here".
 *
 * The mark cannot move to `BoardDiffRenderer` instead: `SurfaceDiff` is
 * `{spec?, changes}` and carries no `data`, so the diff render has no cards to
 * mark. Marking a card needs the items AND the change list in one call, and no
 * caller passes both today. So the mark now waits on a trusted per-card change
 * signal that the current-state contract does not have yet — reachable only
 * through this parameter, which nothing in the product passes. Unreachable is
 * the correct resting state for it; forgeable was not.
 *
 * The path grammar is unchanged: `cards.2.status` marks card 2, a bare
 * `cards.2` marks the whole card, and everything else marks nothing — a bare
 * `cards` would light every lane, and a path into another branch is not about a
 * card at all. Absent beats wrong for a mark that means "look here".
 *
 * Total over its input: the parameter is typed, but a JS caller can hand it
 * anything, so entries are narrowed the same way a payload would be.
 */
function cardMarks(
  trustedChanges: readonly SurfaceFieldChange[] | undefined,
  spec: SurfaceSpec,
): ReadonlyMap<number, CardMark> {
  const itemsPath = spec.items_path;
  if (!Array.isArray(trustedChanges) || !itemsPath) {
    return NO_MARKS;
  }
  const prefix = `${itemsPath}.`;
  const groupPath = spec.group_by_path;
  const marks = new Map<number, CardMark>();
  for (const change of trustedChanges) {
    if (change === null || typeof change !== "object") {
      continue;
    }
    const field: unknown = (change as { field?: unknown }).field;
    if (typeof field !== "string" || !field.startsWith(prefix)) {
      continue;
    }
    const rest = field.slice(prefix.length);
    const dot = rest.indexOf(".");
    const head = dot === -1 ? rest : rest.slice(0, dot);
    if (!/^\d+$/.test(head)) {
      continue;
    }
    const tail = dot === -1 ? "" : rest.slice(dot + 1);
    const index = Number(head);
    // A card can carry several changes; the transition is the one that names
    // the lane axis, and the first such change wins so a later field edit
    // cannot erase it.
    const prior = marks.get(index);
    marks.set(index, {
      transition:
        prior?.transition ??
        // Truthy, not `!== undefined`, and it has to match `renderWithSpec`'s
        // test exactly: a spec with `group_by_path: ""` groups nothing, so
        // every card sits in "Ungrouped" and no card can move between lanes.
        // Under `!== undefined` the empty path also matched the empty `tail` of
        // a bare `cards.0` change, and the chip stated the whole card object as
        // its destination — "→ {"title":"…","status":"…"}".
        (groupPath && tail === groupPath
          ? transitionLabel((change as { new?: unknown }).new)
          : undefined),
    });
  }
  return marks;
}

/** The destination lane as the chip states it, or `undefined` when the change
 * says nothing about where the card lands. */
function transitionLabel(value: unknown): string | undefined {
  const label = formatValue(value);
  return label === "" ? undefined : label;
}

function renderWithSpec(
  spec: SurfaceSpec,
  data: unknown,
  marks: ReadonlyMap<number, CardMark>,
): ReactElement {
  const title = formatValue(resolvePath(data, spec.title_path));
  const rawItems = spec.items_path
    ? resolvePath(data, spec.items_path)
    : undefined;
  const items = Array.isArray(rawItems) ? rawItems : [];
  const visibleItems = items.slice(0, CARD_RENDER_CAP);
  const truncated = items.length > CARD_RENDER_CAP;
  const columns: readonly SurfaceColumn[] = spec.columns ?? [];
  const [titleColumn, ...fieldColumns] = columns;
  const groupPath = spec.group_by_path;

  // Lanes keep first-appearance order. Each card carries its index in the
  // original list so a change mark survives grouping; the `-card-N` testid
  // index stays lane-local, which is what it has always meant.
  const lanes = new Map<string, LaneCard[]>();
  visibleItems.forEach((item, index) => {
    const laneKey = groupPath
      ? formatValue(resolvePath(item, groupPath)) || UNGROUPED
      : UNGROUPED;
    const bucket = lanes.get(laneKey);
    if (bucket) {
      bucket.push({ item, index });
    } else {
      lanes.set(laneKey, [{ item, index }]);
    }
  });

  return (
    <>
      <SurfaceHeader
        kicker={KICKER}
        title={title}
        // The TRUE total, not the painted slice: the cap note below says how
        // many of them are on screen. A badge that counted only what survived
        // the cap would state the wrong size of the board.
        badge={`${items.length} card${items.length === 1 ? "" : "s"}`}
      />
      {items.length === 0 ? (
        <EmptyBody>No cards to display.</EmptyBody>
      ) : (
        <div style={lanesStyle} data-testid="board-lanes">
          {[...lanes.entries()].map(([laneKey, cards], laneIndex) => (
            <div
              key={laneKey}
              style={laneStyle}
              data-testid={`board-lane-${laneIndex}`}
            >
              <div
                style={laneHeaderStyle}
                data-testid={`board-lane-${laneIndex}-header`}
              >
                <span style={laneNameStyle}>{laneKey}</span>
                <span
                  style={laneCountStyle}
                  data-testid={`board-lane-${laneIndex}-count`}
                >
                  {cards.length}
                </span>
              </div>
              {cards.map(({ item, index }, cardIndex) => {
                const mark = marks.get(index);
                const isChanged = mark !== undefined;
                const transition = mark?.transition;
                const meta = metaEntries(item, fieldColumns);
                return (
                  <div
                    key={index}
                    style={cardChromeStyle(isChanged)}
                    data-changed={isChanged ? "true" : undefined}
                    data-testid={`board-lane-${laneIndex}-card-${cardIndex}`}
                  >
                    {isChanged ? (
                      // The mark itself is a border and a bar, i.e. colour
                      // only. This off-screen word is what a screen reader
                      // gets instead. Absolutely positioned, so it is out of
                      // the card's flex flow and costs no layout.
                      <span
                        style={changedMarkerStyle}
                        data-testid={`board-lane-${laneIndex}-card-${cardIndex}-changed`}
                      >
                        Changed
                      </span>
                    ) : null}
                    <div
                      style={cardTitleStyle}
                      data-testid={`board-lane-${laneIndex}-card-${cardIndex}-title`}
                    >
                      {titleColumn
                        ? formatValue(
                            resolvePath(item, titleColumn.path),
                            titleColumn.format,
                          )
                        : formatValue(resolvePath(item, spec.title_path))}
                    </div>
                    {meta.length > 0 || transition !== undefined ? (
                      <div
                        style={cardMetaStyle}
                        data-testid={`board-lane-${laneIndex}-card-${cardIndex}-meta`}
                      >
                        {meta.map((entry) => (
                          <span key={entry.key} style={cardMetaItemStyle}>
                            <span style={cardMetaLabelStyle}>
                              {entry.label}
                            </span>
                            <span style={cardMetaValueStyle}>
                              {entry.value}
                            </span>
                          </span>
                        ))}
                        {transition === undefined ? null : (
                          // The design's `.sfn`: the mark's colour says LOOK
                          // HERE, this says WHERE TO. A card that moved lanes
                          // still renders in the lane its payload puts it in —
                          // the move has not been committed — so without this
                          // the surface shows an accent bar and no statement of
                          // what the pending change actually does.
                          <span
                            style={transitionChipStyle}
                            data-testid={`board-lane-${laneIndex}-card-${cardIndex}-transition`}
                          >
                            {`→ ${transition}`}
                          </span>
                        )}
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      )}
      {truncated ? (
        <div style={capStyle} data-testid="board-card-cap">
          Showing {CARD_RENDER_CAP} of {items.length} cards.
        </div>
      ) : null}
      {spec.link ? (
        <SurfaceLinkRow
          label={spec.link.label}
          value={resolvePath(data, spec.link.url_path)}
        />
      ) : null}
    </>
  );
}

/**
 * Both halves of the boundary value: `state` is where a tool identity would
 * ride, `data` is the payload the note is about.
 *
 * A bare array of cards has no top-level fields of its own, so the note's "the
 * payload as the tool sent it" is served by the FIRST card — the shape the rest
 * of them share. Everything else goes in whole.
 *
 * The badge is what keeps that honest. Without it a 40-card payload rendered as
 * one card's fields would read as a tool that returned a single object, so the
 * header states the true count in the same words the spec-driven path uses.
 */
function renderFallback(state: unknown, data: unknown): ReactElement {
  const items = Array.isArray(data) ? data : [];
  return (
    <>
      <SurfaceHeader
        kicker={KICKER}
        title="Board"
        badge={
          items.length > 0
            ? `${items.length} card${items.length === 1 ? "" : "s"}`
            : undefined
        }
      />
      <NoSpecView
        // The first row that actually CARRIES something, not merely the first.
        // `rows[0]` can be a hole: `[null, {id:2}]` handed NoSpecView `null`, so
        // the note said "The tool returned no payload." directly beneath a badge
        // reading "2 rows" — two mutually exclusive statements in one card, and
        // the payload's real shape never shown. Falling back to the whole value
        // when every entry is a hole keeps the badge and the note describing the
        // same thing.
        data={items.find((row) => row !== null && row !== undefined) ?? data}
        tool={toolNameFromState(state)}
      />
    </>
  );
}

/** Diff view — one before→after row per moved/changed card field. */
export function BoardDiffRenderer(diff: SurfaceDiff | unknown): ReactElement {
  const spec = specFromState(diff);
  const changes = changesFromDiff(diff);
  const labelFor = new Map<string, string>(
    (spec?.columns ?? []).map((column) => [column.path, column.label]),
  );
  return (
    <article
      style={pageStyle}
      data-testid="board-renderer"
      data-mode="diff"
      aria-label="Board surface — proposed changes"
    >
      <section style={cardStyle}>
        <SurfaceHeader
          kicker={KICKER}
          title="Proposed changes"
          badge={`${changes.length} change${changes.length === 1 ? "" : "s"}`}
        />
        {changes.length > 0 ? (
          <div style={fieldGridStyle} data-testid="board-diff-rows">
            {changes.map((change, index) => (
              <DiffFieldRow
                key={`${change.field}:${index}`}
                fieldKey={change.field}
                label={labelFor.get(change.field) ?? change.field}
                previousValue={formatValue(change.old)}
                nextValue={formatValue(change.new)}
              />
            ))}
          </div>
        ) : (
          <EmptyBody>No pending changes.</EmptyBody>
        )}
      </section>
    </article>
  );
}

export const boardAdapter: SaaSRendererAdapter<SurfaceState, SurfaceDiff> = {
  scheme: "board",
  matches: (uri: string) => uri.startsWith("board://"),
  // One argument, deliberately. The host's `state` is untrusted tool output;
  // forwarding it into `trustedChanges` — or writing `renderCurrent:
  // BoardRenderer` and letting a future second host argument land there — is
  // exactly the forgery `cardMarks` documents.
  renderCurrent: (state: SurfaceState): ReactElement => BoardRenderer(state),
  renderDiff: (diff: SurfaceDiff): ReactElement => BoardDiffRenderer(diff),
  metadata: {
    origin: "first-party",
    schemaVersion: 1,
  },
};

/**
 * The lane grid.
 *
 * The 1px gap IS the divider: the grid paints the hairline colour, each lane
 * paints the surface over it, and the 1px the lanes do not cover is the rule
 * between them. There is deliberately no border anywhere — drawing one instead
 * doubles the line wherever two lanes meet and puts a stray edge on the two
 * ends, which is a different thing that only looks similar.
 */
const lanesStyle: CSSProperties = {
  display: "grid",
  gridAutoFlow: "column",
  gridAutoColumns: "minmax(196px, 1fr)",
  gap: "1px",
  background: HAIRLINE,
  minHeight: 230,
  overflowX: "auto",
  // A board scrolled to its last lane must not start scrolling the page behind
  // it: the surface is the thing the user is reading.
  overscrollBehaviorX: "contain",
};

const laneStyle: CSSProperties = {
  background: PALETTE.surface,
  padding: 10,
  display: "flex",
  flexDirection: "column",
  gap: 7,
  minWidth: 0,
  maxHeight: 352,
  overflowY: "auto",
  overscrollBehaviorY: "contain",
};

/**
 * The lane header, sticky inside its own lane.
 *
 * `top` and `margin` are one pair, not two values: the lane pads to 10px, the
 * negative margin cancels that padding so the header spans the full lane width,
 * and the matching negative `top` is what parks it flush against the lane's
 * padding box instead of leaving it hovering 10px down while cards slide under
 * the gap. Change one and the other has to move with it.
 */
const laneHeaderStyle: CSSProperties = {
  position: "sticky",
  top: -10,
  zIndex: 1,
  margin: "-10px -10px 0",
  padding: "10px 10px 7px",
  // Opaque, and the same ground as the lane: this is what the cards scroll
  // under.
  background: PALETTE.surface,
  fontFamily: MONO_FAMILY,
  fontSize: MONO_MICRO,
  letterSpacing: CAPS_TRACKING,
  textTransform: "uppercase",
  color: LABEL_QUIET,
  display: "flex",
  alignItems: "center",
  gap: 6,
};

/** The lane name is untrusted group output — it truncates rather than pushing
 * the count out of the lane. */
const laneNameStyle: CSSProperties = {
  minWidth: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const laneCountStyle: CSSProperties = {
  marginLeft: "auto",
  flex: "none",
  color: LABEL_QUIET,
};

/**
 * Card chrome. `changed` swaps the hairline for the accent and adds the 2px
 * inset bar down the leading edge — the ATTENTION register, which is the one
 * thing on this surface that means "you still owe a decision here". It is
 * deliberately NOT the board's identity hue: `--surface-src` says where the
 * card came from, and a card that needs a decision must not have to compete
 * with the kicker dot that is already saying that.
 */
function cardChromeStyle(changed: boolean): CSSProperties {
  return {
    border: `1px solid ${changed ? ACCENT_LINE : HAIRLINE}`,
    borderRadius: 8,
    background: CARD_GROUND,
    padding: "8px 9px",
    display: "flex",
    flexDirection: "column",
    gap: 6,
    minWidth: 0,
    boxShadow: changed ? `inset 2px 0 0 ${PALETTE.lime}` : undefined,
  };
}

const changedMarkerStyle: CSSProperties = {
  position: "absolute",
  width: 1,
  height: 1,
  overflow: "hidden",
  clip: "rect(0 0 0 0)",
  clipPath: "inset(50%)",
  whiteSpace: "nowrap",
};

const cardTitleStyle: CSSProperties = {
  fontSize: 12,
  color: PALETTE.textHi,
  lineHeight: 1.4,
  textWrap: "pretty",
  // Not in the design, and kept anyway: a title is untrusted tool output, and
  // one unbroken 300-character token would otherwise widen the lane past its
  // track and hand the board a horizontal scrollbar it never asked for.
  overflowWrap: "anywhere",
};

const cardMetaStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  // The design's row holds two facts and does not wrap. A spec may declare
  // more columns than that, and a row that refuses to wrap would push them out
  // of a 196px lane rather than showing them.
  flexWrap: "wrap",
  fontFamily: MONO_FAMILY,
  fontSize: MONO_MICRO,
  color: LABEL_QUIET,
};

const cardMetaItemStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
  minWidth: 0,
};

/** Label and value share the row's colour — they are separated typographically
 * (caps + tracking on the label) rather than chromatically, because the meta
 * row is one quiet register and a second colour in it would read as a state. */
const cardMetaLabelStyle: CSSProperties = {
  textTransform: "uppercase",
  letterSpacing: CAPS_TRACKING,
};

const cardMetaValueStyle: CSSProperties = {
  overflowWrap: "anywhere",
};

/**
 * The design's `.sfn` — the transition chip, in the ATTENTION register the
 * marked card is already wearing. Ink at full strength because it is a
 * statement to read, not a label; ground and hairline are the accent's soft and
 * line rungs, so it reads as part of the mark rather than as a second state.
 *
 * `maxWidth`/`overflow`/`textOverflow` are not in the design and are kept: the
 * lane track is 196px at its minimum and the chip states a value that came from
 * the payload, so an over-long one must ellipsize inside the row instead of
 * handing the board a horizontal scrollbar. `whiteSpace: nowrap` keeps
 * "→ In review" one statement rather than two stacked words.
 */
const transitionChipStyle: CSSProperties = {
  marginLeft: "auto",
  color: PALETTE.textHi,
  background: PALETTE.limeBgSoft,
  boxShadow: `inset 0 0 0 1px ${ACCENT_LINE}`,
  borderRadius: 4,
  padding: "1px 5px",
  maxWidth: "100%",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

/** The truncation line sits in the design's cap register (`.sft-cap`), whose
 * colour is `--mut2` — the same quiet rung as every other label on this
 * surface, and one below the palette's `textLo`. */
const capStyle: CSSProperties = {
  fontSize: 12,
  color: LABEL_QUIET,
  letterSpacing: 0.3,
};
