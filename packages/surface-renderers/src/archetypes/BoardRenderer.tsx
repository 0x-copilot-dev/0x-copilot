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
  type SurfaceSpec,
  type SurfaceState,
} from "../_shared/specTypes";

const KICKER = "Board";

/** Cap on cards painted across all lanes — render-budget guard (PRD-03). */
export const CARD_RENDER_CAP = 200;

const UNGROUPED = "Ungrouped";

const NO_CHANGES: ReadonlySet<number> = new Set<number>();

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

/** A card plus its index in the ORIGINAL item list (grouping reorders cards,
 * and the change marks are keyed by that original index). */
interface LaneCard {
  readonly item: unknown;
  readonly index: number;
}

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
 */
export function BoardRenderer(state: SurfaceState | unknown): ReactElement {
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
          ? renderWithSpec(
              spec,
              data,
              changedItemIndexes(state, spec.items_path),
            )
          : renderFallback(state, data)}
      </section>
    </article>
  );
}

/**
 * Which items the surface state says changed, as indexes into `items_path`.
 *
 * The current-state view has no per-card change flag of its own, and it must
 * not grow one out of the payload: `data` is untrusted tool output and the
 * accent register means "a decision you still owe", so a tool that could set it
 * could manufacture urgency. The one trusted signal at this boundary is the
 * `SurfaceFieldChange` list, whose `field` is a dotted path into the same
 * payload — `cards.2.status` marks card 2, and a bare `cards.2` marks the whole
 * card. Everything else marks nothing: a bare `cards` (the whole list) would
 * light every lane, and a path into another branch is not about a card at all.
 * Absent beats wrong for a mark that means "look here".
 *
 * Reads defensively and never throws. With no change list riding along — the
 * shape `renderCurrent` gets today — the set is empty and every card is plain.
 */
function changedItemIndexes(
  state: unknown,
  itemsPath: string | undefined,
): ReadonlySet<number> {
  const changes = changesFromDiff(state);
  if (changes.length === 0 || !itemsPath) {
    return NO_CHANGES;
  }
  const prefix = `${itemsPath}.`;
  const marked = new Set<number>();
  for (const change of changes) {
    if (!change.field.startsWith(prefix)) {
      continue;
    }
    const head = change.field.slice(prefix.length).split(".")[0];
    if (head === undefined || !/^\d+$/.test(head)) {
      continue;
    }
    marked.add(Number(head));
  }
  return marked;
}

function renderWithSpec(
  spec: SurfaceSpec,
  data: unknown,
  changed: ReadonlySet<number>,
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
                const isChanged = changed.has(index);
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
                    {meta.length > 0 ? (
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
  background: PALETTE.border,
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
  color: PALETTE.textLo,
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
  color: PALETTE.textLo,
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
    border: `1px solid ${changed ? ACCENT_LINE : PALETTE.border}`,
    borderRadius: 8,
    background: PALETTE.surfaceMute,
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
  color: PALETTE.textLo,
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

const capStyle: CSSProperties = {
  fontSize: 12,
  color: PALETTE.textLo,
  letterSpacing: 0.3,
};
