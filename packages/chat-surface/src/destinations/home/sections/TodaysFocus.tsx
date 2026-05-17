// <TodaysFocus> — P2-B3 home section.
//
// Pure presentation: renders the server-determined top-3 focus items
// (the slicing is the BACKEND's concern — see home-prd §4.4: "The home
// backend picks top-3 by composite score"). This component does NOT
// re-sort or re-slice. If the server hands us four rows by mistake, we
// render four — but the contract says exactly top-3 OR FEWER.
//
// Each row: kind icon + title + due_at (formatRelativeTime) + urgency_score
// pill. Branches on SectionResult status (ok / error / unavailable).
//
// Source: docs/atlas-new-design/destinations/home-prd.md §4.4 +
// cross-audit.md §1.6.
//
// TODO(merge): _home-stub.ts is local; repoint to api-types when P2-A1
// merges. The "top-3 enforcement" decision (deviation §9.5 Q5) lives
// server-side; the UI trusts the wire.

import type { CSSProperties, ReactElement } from "react";

import type { SectionResult } from "@enterprise-search/api-types";

import { EmptyState } from "../../../shell/EmptyState";
import { StatusPill, type StatusTone } from "../../../shell/StatusPill";
import { formatRelativeTime } from "../../../util/time";

import type {
  HomeFocusItem,
  HomeFocusKind,
  HomeFocusPriority,
} from "../_home-stub";

export interface TodaysFocusProps {
  readonly focus: SectionResult<HomeFocusItem[]>;
  /** Optional reference instant for relative time (test seam). */
  readonly now?: number;
}

const KIND_ICON: Readonly<Record<HomeFocusKind, string>> = {
  todo: "•",
  approval: "?",
  review: "*",
};

const KIND_LABEL: Readonly<Record<HomeFocusKind, string>> = {
  todo: "todo",
  approval: "approval",
  review: "review",
};

const PRIORITY_TONE: Readonly<Record<HomeFocusPriority, StatusTone>> = {
  high: "error",
  med: "warning",
  low: "muted",
};

const URGENCY_TONE_HIGH = 70; // >= -> error
const URGENCY_TONE_MED = 40; // >= -> warning

function urgencyTone(score: number): StatusTone {
  if (score >= URGENCY_TONE_HIGH) return "error";
  if (score >= URGENCY_TONE_MED) return "warning";
  return "muted";
}

const listStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  margin: 0,
  padding: 0,
  listStyle: "none",
};

const rowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  padding: "8px 10px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border, #232325)",
  backgroundColor: "var(--color-bg-elevated, #161617)",
};

const iconStyle: CSSProperties = {
  flexShrink: 0,
  width: 18,
  textAlign: "center",
  color: "var(--color-text-muted, #b4b4b8)",
  fontSize: "var(--font-size-sm, 13px)",
};

const titleStyle: CSSProperties = {
  flex: 1,
  minWidth: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-sm, 13px)",
};

const dueStyle: CSSProperties = {
  flexShrink: 0,
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-subtle, #7e7e84)",
};

const overdueStyle: CSSProperties = {
  ...dueStyle,
  color: "var(--color-danger, #d97777)",
  fontWeight: 600,
};

export function TodaysFocus({ focus, now }: TodaysFocusProps): ReactElement {
  if (focus.status === "error") {
    return (
      <div
        role="alert"
        data-testid="home-todays-focus-error"
        data-section-status="error"
      >
        <EmptyState
          title="Couldn't load today's focus"
          body={focus.error ?? "Try again in a moment."}
        />
      </div>
    );
  }

  if (focus.status === "unavailable") {
    return (
      <div
        data-testid="home-todays-focus-unavailable"
        data-section-status="unavailable"
      >
        <EmptyState
          title="Today's focus unavailable"
          body={focus.error ?? "This section is temporarily unavailable."}
        />
      </div>
    );
  }

  const items = focus.data ?? [];
  if (items.length === 0) {
    return (
      <div data-testid="home-todays-focus-empty" data-section-status="ok">
        <EmptyState title="Nothing urgent." />
      </div>
    );
  }

  return (
    <ul
      style={listStyle}
      data-testid="home-todays-focus"
      data-section-status="ok"
      aria-label="Today's focus"
    >
      {items.map((item) => {
        const overdue = item.is_overdue;
        return (
          <li
            key={item.todo_id}
            style={rowStyle}
            data-testid="home-todays-focus-row"
            data-priority={item.priority}
            data-overdue={overdue ? "true" : "false"}
          >
            <span
              style={iconStyle}
              data-testid="home-todays-focus-kind-icon"
              data-kind={item.kind}
              aria-label={KIND_LABEL[item.kind]}
              role="img"
            >
              {KIND_ICON[item.kind]}
            </span>
            <span style={titleStyle} data-testid="home-todays-focus-title">
              {item.title}
            </span>
            {item.due_at !== undefined ? (
              <span
                style={overdue ? overdueStyle : dueStyle}
                data-testid="home-todays-focus-due"
              >
                {overdue ? "overdue · " : ""}
                {formatRelativeTime(item.due_at, now)}
              </span>
            ) : null}
            <StatusPill
              status={
                overdue ? PRIORITY_TONE.high : urgencyTone(item.urgency_score)
              }
              label={`urgency ${item.urgency_score}`}
            />
          </li>
        );
      })}
    </ul>
  );
}
