// <TriageStrip> — four numeric tiles, the visual centerpiece of the
// 5-second test. Sub-PRD §3.1.2.
//
// Each tile is a single clickable tile that:
//   - shows a count via <StatusPill> (SP-1 primitive)
//   - tone is derived from the count: 0 -> muted (gray); 1-4 -> warning
//     (yellow); >=5 -> error (red).
//   - click target is expressed as an `ItemRef` (cross-audit §1.1) —
//     never a raw route string. The host supplies an `onSelect`
//     callback; pure presentation does NOT route on its own.
//
// ARIA: <nav aria-label="Triage"> wraps the four tiles so screen-reader
// users hear "Triage navigation" and the tiles are listed in document
// order.

import type { CSSProperties, ReactElement } from "react";

import type {
  ApprovalId,
  ItemRef,
  RunId,
  TodoId,
  TriageCounts,
} from "@0x-copilot/api-types";

import { StatusPill, type StatusTone } from "../../../shell/StatusPill";

export interface TriageStripProps {
  readonly counts: TriageCounts;
  /**
   * Host-supplied router shim. Given an `ItemRef`, the host navigates
   * (or opens the corresponding destination). Pure-presentation: this
   * component never calls a router directly.
   */
  readonly onSelect?: (ref: ItemRef) => void;
}

type TriageKey =
  | "approvals_waiting"
  | "runs_failed_24h"
  | "todos_overdue"
  | "todos_due_today";

interface TileSpec {
  readonly key: TriageKey;
  readonly label: string;
  readonly target: ItemRef;
}

/**
 * The four tiles, in fixed display order. `target.id` is a synthetic
 * sentinel that the host's router maps to the filtered destination view
 * (sub-PRD §3.1.2 table). Sentinels are branded through `as unknown as`
 * casts; the host resolver inspects `kind` only.
 */
function tileSpecs(): ReadonlyArray<TileSpec> {
  return [
    {
      key: "approvals_waiting",
      label: "Approvals waiting",
      target: {
        kind: "approval",
        id: "__triage_approvals_waiting__" as unknown as ApprovalId,
      },
    },
    {
      key: "runs_failed_24h",
      label: "Failed runs (24h)",
      target: {
        kind: "run",
        id: "__triage_runs_failed_24h__" as unknown as RunId,
      },
    },
    {
      key: "todos_overdue",
      label: "Overdue todos",
      target: {
        kind: "todo",
        id: "__triage_todos_overdue__" as unknown as TodoId,
      },
    },
    {
      key: "todos_due_today",
      label: "Due today",
      target: {
        kind: "todo",
        id: "__triage_todos_due_today__" as unknown as TodoId,
      },
    },
  ];
}

function toneForCount(count: number): StatusTone {
  if (count <= 0) return "muted";
  if (count >= 5) return "error";
  return "warning";
}

const stripStyle: CSSProperties = {
  display: "flex",
  flexDirection: "row",
  gap: 12,
  flexWrap: "wrap",
};

const tileStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "flex-start",
  gap: 6,
  padding: "12px 14px",
  backgroundColor: "var(--color-surface)",
  border: "1px solid var(--color-border)",
  borderRadius: "var(--radius-md, 12px)",
  color: "var(--color-text)",
  cursor: "pointer",
  textAlign: "left",
  minWidth: 140,
  font: "inherit",
};

const tileLabelStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-subtle)",
};

function getCount(counts: TriageCounts, key: TriageKey): number {
  switch (key) {
    case "approvals_waiting":
      return counts.approvals_waiting;
    case "runs_failed_24h":
      return counts.runs_failed_24h;
    case "todos_overdue":
      return counts.todos_overdue;
    case "todos_due_today":
      return counts.todos_due_today;
  }
}

export function TriageStrip({
  counts,
  onSelect,
}: TriageStripProps): ReactElement {
  const tiles = tileSpecs();
  return (
    <nav aria-label="Triage" data-testid="home-triage-strip" style={stripStyle}>
      {tiles.map((tile) => {
        const count = getCount(counts, tile.key);
        const tone = toneForCount(count);
        return (
          <button
            key={tile.key}
            type="button"
            style={tileStyle}
            data-testid={`home-triage-tile-${tile.key}`}
            data-triage-key={tile.key}
            data-triage-tone={tone}
            data-triage-count={count}
            onClick={() => onSelect?.(tile.target)}
            aria-label={`${tile.label}: ${count}`}
          >
            <span style={tileLabelStyle}>{tile.label}</span>
            <StatusPill status={tone} label={String(count)} />
          </button>
        );
      })}
    </nav>
  );
}
