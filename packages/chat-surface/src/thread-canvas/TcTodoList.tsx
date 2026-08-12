// The agent's working checklist, pinned above the composer.
//
// Replaces two surfaces: the raw `write_todos` tool card (the backend already
// marks those frames internal — see `projectToolCalls`) and the Focus "Plan",
// which invented steps from tool-call frames and so presented tool names as if
// they were a plan. Every row here is a `Todo` the agent actually wrote,
// replayable from the run's event ledger.
//
// Presentational only: the projection is `projectRunTodos` over the single
// canonical event array, and the host owns nothing — there is no callback
// surface because the list is agent-owned. A user cannot tick a row here, the
// same way they cannot edit the agent's reasoning.

import { useState, type CSSProperties, type ReactElement } from "react";

import type { RunTodoStatus, RunTodosProjection } from "./eventProjector";

export interface TcTodoListProps {
  readonly projection: RunTodosProjection;
  /**
   * The run is parked on a decision the USER owes — an approval was requested
   * and not yet resolved. The in-progress row then reads *waiting*, not
   * *working*, because nothing is executing while the graph is interrupted.
   *
   * This is not the inference `SUBAGENT_PAUSED` exists to forbid. That event
   * was added so a paused row is never derived from the ABSENCE of a completion
   * event; a pending `approval_requested` with no matching `approval_resolved`
   * is a positive fact, already computed by `projectApprovals` over the same
   * canonical array.
   */
  readonly blocked?: boolean;
}

/**
 * The pinned checklist card. Renders nothing for an empty list — the agent
 * clearing its todos should remove the panel, not leave an empty frame.
 *
 * A finished list folds to a one-line summary rather than disappearing: the run
 * is usually still speaking at that point, and the summary keeps the work
 * visible without competing with the answer. It stays expandable, and a fresh
 * list from the agent opens the panel again at its next generation.
 */
export function TcTodoList({
  projection,
  blocked = false,
}: TcTodoListProps): ReactElement | null {
  const { todos, completedCount, isComplete, generation } = projection;
  // A finished list folds by default; the user's toggle OVERRIDES that default
  // in either direction, which is why this is a disclosure record rather than a
  // collapsed flag ORed with `isComplete` — that shape cannot express "expand
  // the finished list", so a completed checklist could never be reopened.
  // Scoped to a list id so a rollover falls back to the default: the user
  // collapsing list 1 is not a statement about list 2.
  const [disclosure, setDisclosure] = useState<Disclosure | null>(null);
  const collapsed =
    disclosure !== null && disclosure.listId === projection.listId
      ? !disclosure.open
      : isComplete;

  // The terminal, folded state — the one that sits pinned above the composer
  // for the rest of the conversation. It is the only state that gets the
  // compact treatment; an unfinished list keeps its full chrome because the
  // user is still reading progress off it.
  const foldedComplete = isComplete && collapsed;

  if (todos.length === 0) {
    return null;
  }

  return (
    <section
      aria-label="Agent todos"
      data-testid="tc-todo-list"
      data-collapsed={collapsed ? "true" : "false"}
      data-complete={isComplete ? "true" : "false"}
      data-blocked={blocked ? "true" : "false"}
      data-generation={generation}
      style={foldedComplete ? rootStyleFoldedComplete : rootStyle}
    >
      <style>{TODO_LIST_CSS}</style>
      <div style={foldedComplete ? headerStyleFoldedComplete : headerStyle}>
        <span style={titleStyle}>Todos</span>
        {generation > 1 ? (
          <span data-testid="tc-todo-list-generation" style={generationStyle}>
            List {generation}
          </span>
        ) : null}
        <span data-testid="tc-todo-list-count" style={countStyle}>
          {completedCount}/{todos.length}
        </span>
        <button
          type="button"
          data-testid="tc-todo-list-toggle"
          aria-label={collapsed ? "Expand agent todos" : "Collapse agent todos"}
          aria-expanded={!collapsed}
          onClick={() =>
            setDisclosure({ listId: projection.listId, open: collapsed })
          }
          style={toggleStyle(collapsed)}
        >
          <ChevronIcon />
        </button>
      </div>
      {/* A finished, folded list said the same thing three times: the header
          count (`3/3`), a full-width bar at 100%, and the summary line. The
          bar is the one with no extra information, and dropping it is what
          turns a three-row band pinned above the composer back into a quiet
          one-line receipt. It stays for every unfinished list, where the fill
          is the only at-a-glance read of progress. */}
      {foldedComplete ? null : (
        <div aria-hidden="true" style={progressTrackStyle}>
          <span
            style={progressFillStyle(completedCount, todos.length, isComplete)}
          />
        </div>
      )}
      {collapsed ? (
        <p data-testid="tc-todo-list-summary" style={summaryStyle}>
          {isComplete ? (
            <>
              <CheckIcon tone="done" />
              {`All ${todos.length} todos complete`}
            </>
          ) : (
            `${completedCount} of ${todos.length} todos complete`
          )}
        </p>
      ) : (
        <ol style={listStyle}>
          {todos.map((todo, index) => (
            <li
              // Content is the row's identity — `write_todos` assigns no ids.
              // A stable key is what makes the glyph swap (spinner → tick) a
              // mount of the new glyph inside a surviving row, which is exactly
              // when the tick should draw itself on.
              key={`${todo.content}-${index}`}
              data-testid="tc-todo-row"
              data-status={todo.status}
              data-waiting={
                blocked && todo.status === "in_progress" ? "true" : "false"
              }
              style={rowStyle(todo.status)}
            >
              <TodoGlyph status={todo.status} blocked={blocked} />
              <span style={rowTextStyle}>{todo.content}</span>
              {blocked && todo.status === "in_progress" ? (
                <span style={waitingLabelStyle}>waiting for you</span>
              ) : null}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

/** The user's explicit open/closed choice, scoped to the list it was made on. */
interface Disclosure {
  readonly listId: string;
  readonly open: boolean;
}

function TodoGlyph({
  status,
  blocked,
}: {
  readonly status: RunTodoStatus;
  readonly blocked: boolean;
}): ReactElement {
  if (status === "completed") {
    return (
      <span style={glyphSlotStyle}>
        <CheckIcon tone="row" />
      </span>
    );
  }
  if (status === "in_progress") {
    // A spinner asserts motion. While the run is parked there is none, and the
    // step is not "taking a while" — it is stopped on the user. A still glyph
    // is the honest one, and it is also what makes the pending approval legible
    // as the reason nothing is moving.
    if (blocked) {
      return (
        <span style={glyphSlotStyle}>
          <span
            aria-hidden="true"
            data-testid="tc-todo-waiting"
            style={waitingGlyphStyle}
          />
        </span>
      );
    }
    return (
      <span style={glyphSlotStyle}>
        <span
          aria-hidden="true"
          className="tc-todo__spinner"
          data-testid="tc-todo-spinner"
          style={spinnerStyle}
        />
      </span>
    );
  }
  return (
    <span style={glyphSlotStyle}>
      <span aria-hidden="true" style={ringStyle} />
    </span>
  );
}

function CheckIcon({ tone }: { readonly tone: "row" | "done" }): ReactElement {
  return (
    <svg aria-hidden="true" style={checkStyle(tone)} viewBox="0 0 16 16">
      <path
        className="tc-todo__check"
        d="m3.5 8.4 3 3 6-6.4"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.9"
      />
    </svg>
  );
}

function ChevronIcon(): ReactElement {
  return (
    <svg aria-hidden="true" style={chevronStyle} viewBox="0 0 16 16">
      <path
        d="m4 6 4 4 4-4"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.6"
      />
    </svg>
  );
}

// The shared content rail. `TcChat` caps the transcript, the ghost banner and
// the composer at `--chat-content-width` and centres them; this panel is
// pinned directly above the composer, so it has to sit on the same rail or it
// overhangs the box it is pinned to — visibly, on any window wide enough for
// the cap to bite. It is declared here rather than wrapped around the panel in
// `TcChat` because `panel.nextElementSibling === tc-chat-composer-slot` is a
// pinned contract: nothing may come between the checklist and the composer.
// The width itself is the CSS var, so the two cannot drift to different values.
const railStyle: CSSProperties = {
  boxSizing: "border-box",
  marginLeft: "auto",
  marginRight: "auto",
  maxWidth: "var(--chat-content-width, 68rem)",
  width: "100%",
};

const rootStyle: CSSProperties = {
  ...railStyle,
  display: "flex",
  flexDirection: "column",
  // No `margin` shorthand here — it would reset `railStyle`'s auto side
  // margins and un-centre the panel. Vertical spacing is the canvas stack's
  // single `CANVAS_STACK_GAP`, not this component's business.
  padding: "10px 12px 11px",
  background: "var(--color-surface)",
  border: "1px solid var(--color-border)",
  borderRadius: "var(--radius-lg, 12px)",
};

// Terminal + folded: no progress bar follows, so the header owes it no bottom
// gutter, and the whole card tightens to a single quiet row.
const rootStyleFoldedComplete: CSSProperties = {
  ...rootStyle,
  padding: "7px 12px 8px",
};

const headerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "0 2px 8px",
};

const headerStyleFoldedComplete: CSSProperties = {
  ...headerStyle,
  padding: "0 2px 0",
};

const titleStyle: CSSProperties = {
  color: "var(--color-text-subtle)",
  fontFamily: "var(--font-mono)",
  fontSize: 9.5,
  fontWeight: 400,
  letterSpacing: "0.14em",
  textTransform: "uppercase",
};

const generationStyle: CSSProperties = {
  padding: "1px 6px",
  border: "1px solid var(--color-border-strong)",
  borderRadius: "var(--radius-full, 999px)",
  color: "var(--color-text-subtle)",
  fontFamily: "var(--font-mono)",
  fontSize: 9.5,
  letterSpacing: "0.06em",
};

const countStyle: CSSProperties = {
  marginLeft: "auto",
  color: "var(--color-text-subtle)",
  fontFamily: "var(--font-mono)",
  fontSize: 10.5,
  fontVariantNumeric: "tabular-nums",
};

const toggleStyle = (collapsed: boolean): CSSProperties => ({
  display: "grid",
  placeItems: "center",
  width: 18,
  height: 18,
  padding: 0,
  border: 0,
  background: "none",
  borderRadius: "var(--radius-sm, 6px)",
  color: "var(--color-text-subtle)",
  cursor: "pointer",
  transform: collapsed ? "rotate(-90deg)" : "none",
});

const chevronStyle: CSSProperties = {
  width: 13,
  height: 13,
};

const progressTrackStyle: CSSProperties = {
  height: 1,
  margin: "0 2px 9px",
  background: "var(--color-border)",
  borderRadius: 1,
  overflow: "hidden",
};

const progressFillStyle = (
  done: number,
  total: number,
  complete: boolean,
): CSSProperties => ({
  display: "block",
  width: total === 0 ? "0%" : `${(done / total) * 100}%`,
  height: "100%",
  background: complete
    ? "var(--color-success, #57c785)"
    : "var(--color-accent)",
  transition: "width 380ms cubic-bezier(0.4, 0, 0.2, 1)",
});

const listStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 1,
  listStyle: "none",
  margin: 0,
  padding: 0,
};

const rowStyle = (status: RunTodoStatus): CSSProperties => ({
  display: "flex",
  alignItems: "flex-start",
  gap: 9,
  padding: "4px 2px",
  color:
    status === "completed"
      ? "var(--color-text-subtle)"
      : status === "in_progress"
        ? "var(--color-text)"
        : "var(--color-text-muted)",
  fontSize: 12.5,
  lineHeight: 1.45,
});

const rowTextStyle: CSSProperties = {
  minWidth: 0,
};

const glyphSlotStyle: CSSProperties = {
  flex: "0 0 auto",
  display: "grid",
  placeItems: "center",
  width: 14,
  height: 14,
  marginTop: 2,
};

const checkStyle = (tone: "row" | "done"): CSSProperties => ({
  width: 14,
  height: 14,
  color:
    tone === "done"
      ? "var(--color-success, #57c785)"
      : "var(--color-text-subtle)",
});

const spinnerStyle: CSSProperties = {
  width: 12,
  height: 12,
  borderRadius: "50%",
  border: "1.5px solid var(--color-accent-soft, rgba(95, 178, 236, 0.22))",
  borderTopColor: "var(--color-accent)",
};

/** The parked counterpart of the spinner: a still, amber-rimmed ring. */
const waitingGlyphStyle: CSSProperties = {
  width: 12,
  height: 12,
  borderRadius: "50%",
  border: "1.5px solid var(--color-warning, #e8b45e)",
  borderTopColor: "transparent",
};

const waitingLabelStyle: CSSProperties = {
  flex: "0 0 auto",
  marginLeft: "auto",
  paddingLeft: 8,
  color: "var(--color-warning, #e8b45e)",
  fontFamily: "var(--font-mono)",
  fontSize: 10,
  letterSpacing: "0.04em",
  whiteSpace: "nowrap",
};

const ringStyle: CSSProperties = {
  width: 11,
  height: 11,
  border: "1.4px solid currentColor",
  borderRadius: "50%",
  opacity: 0.45,
};

const summaryStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  margin: 0,
  padding: "1px 2px",
  color: "var(--color-text-subtle)",
  fontSize: 12.5,
};

// Shipped with the component rather than a host stylesheet: a package-owned
// class re-declared in an app's sheet wins the cascade, which is how desktop
// has silently lost package styling before.
//
// The tick draws itself on when a row completes, and nothing else does. That
// falls out of the row keys: a status flip swaps the glyph inside a surviving
// <li>, so the check element MOUNTS exactly at the moment of completion and its
// one-shot animation plays once. Rows that were already done keep their glyph
// across re-renders and stay still.
const TODO_LIST_CSS = `
@keyframes tc-todo-spin { to { transform: rotate(360deg); } }
@keyframes tc-todo-check-draw { from { stroke-dashoffset: 14; } to { stroke-dashoffset: 0; } }
.tc-todo__spinner { animation: tc-todo-spin 720ms linear infinite; }
.tc-todo__check { stroke-dasharray: 14; animation: tc-todo-check-draw 260ms cubic-bezier(0.3, 0, 0.2, 1); }
[data-reduce-motion="1"] .tc-todo__spinner,
[data-reduce-motion="always"] .tc-todo__spinner,
[data-reduce-motion="1"] .tc-todo__check,
[data-reduce-motion="always"] .tc-todo__check { animation: none; }
@media (prefers-reduced-motion: reduce) {
  .tc-todo__spinner, .tc-todo__check { animation: none; }
}
`;
