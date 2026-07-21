import type { CSSProperties, ReactElement } from "react";

import type { SaaSRendererAdapter } from "@0x-copilot/chat-surface";

import { SURFACE_PALETTE as PALETTE } from "../_shared/palette";
import {
  cardStyle,
  DiffFieldRow,
  EmptyBody,
  fieldGridStyle,
  GenericFieldList,
  pageStyle,
  PreparingHint,
  SurfaceHeader,
  SurfaceLinkRow,
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
        {spec ? renderWithSpec(spec, data) : renderFallback(data)}
      </section>
    </article>
  );
}

function renderWithSpec(spec: SurfaceSpec, data: unknown): ReactElement {
  const title = formatValue(resolvePath(data, spec.title_path));
  const rawItems = spec.items_path
    ? resolvePath(data, spec.items_path)
    : undefined;
  const items = Array.isArray(rawItems)
    ? rawItems.slice(0, CARD_RENDER_CAP)
    : [];
  const columns: readonly SurfaceColumn[] = spec.columns ?? [];
  const [titleColumn, ...fieldColumns] = columns;
  const groupPath = spec.group_by_path;

  const lanes = new Map<string, unknown[]>();
  for (const item of items) {
    const laneKey = groupPath
      ? formatValue(resolvePath(item, groupPath)) || UNGROUPED
      : UNGROUPED;
    const bucket = lanes.get(laneKey);
    if (bucket) {
      bucket.push(item);
    } else {
      lanes.set(laneKey, [item]);
    }
  }

  return (
    <>
      <SurfaceHeader
        kicker={KICKER}
        title={title}
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
              <div style={laneHeaderStyle}>
                <span>{laneKey}</span>
                <span style={laneCountStyle}>{cards.length}</span>
              </div>
              <div style={laneBodyStyle}>
                {cards.map((card, cardIndex) => (
                  <div
                    key={cardIndex}
                    style={cardItemStyle}
                    data-testid={`board-lane-${laneIndex}-card-${cardIndex}`}
                  >
                    <div style={cardTitleStyle}>
                      {titleColumn
                        ? formatValue(
                            resolvePath(card, titleColumn.path),
                            titleColumn.format,
                          )
                        : formatValue(resolvePath(card, spec.title_path))}
                    </div>
                    {fieldColumns.map((column, columnIndex) => (
                      <div
                        key={`${column.path}:${columnIndex}`}
                        style={cardFieldStyle}
                      >
                        <span style={cardFieldLabelStyle}>{column.label}</span>
                        <span style={cardFieldValueStyle}>
                          {formatValue(
                            resolvePath(card, column.path),
                            column.format,
                          )}
                        </span>
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
      {spec.link ? (
        <SurfaceLinkRow
          label={spec.link.label}
          value={resolvePath(data, spec.link.url_path)}
        />
      ) : null}
    </>
  );
}

function renderFallback(data: unknown): ReactElement {
  const items = Array.isArray(data) ? data : [];
  return (
    <>
      <SurfaceHeader kicker={KICKER} title="Board" />
      <PreparingHint />
      <GenericFieldList
        data={items.length > 0 ? items[0] : data}
        format={(v) => formatValue(v)}
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

const lanesStyle: CSSProperties = {
  display: "flex",
  gap: 12,
  overflowX: "auto",
  paddingBottom: 4,
};

const laneStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  minWidth: 220,
  flex: "0 0 220px",
  background: PALETTE.surfaceMute,
  border: `1px solid ${PALETTE.border}`,
  borderRadius: 10,
  padding: 10,
};

const laneHeaderStyle: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  fontSize: 11,
  letterSpacing: 0.4,
  textTransform: "uppercase",
  fontWeight: 600,
  color: PALETTE.textLo,
};

const laneCountStyle: CSSProperties = {
  color: PALETTE.textMid,
};

const laneBodyStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
};

const cardItemStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  background: PALETTE.surface,
  border: `1px solid ${PALETTE.border}`,
  borderRadius: 8,
  padding: 10,
};

const cardTitleStyle: CSSProperties = {
  fontSize: 13,
  fontWeight: 600,
  color: PALETTE.textHi,
  overflowWrap: "anywhere",
};

const cardFieldStyle: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  gap: 8,
  fontSize: 12,
};

const cardFieldLabelStyle: CSSProperties = {
  color: PALETTE.textLo,
};

const cardFieldValueStyle: CSSProperties = {
  color: PALETTE.textMid,
  overflowWrap: "anywhere",
};
