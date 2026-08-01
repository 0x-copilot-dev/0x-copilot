import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type {
  RuntimeApiEventType,
  RuntimeEventEnvelope,
} from "@0x-copilot/api-types";

import { projectRunTodos, type RunTodosProjection } from "./eventProjector";
import { TcTodoList } from "./TcTodoList";

let nextSeq = 0;

function todoEvent(
  todos: readonly { content: string; status: string }[],
  overrides: Partial<RuntimeEventEnvelope> & {
    readonly listId?: string;
    readonly generation?: number;
  } = {},
): RuntimeEventEnvelope {
  const { listId, generation, ...envelope } = overrides;
  const seq = nextSeq;
  nextSeq += 1;
  return {
    event_id: `evt-${seq}`,
    run_id: "run-1",
    conversation_id: "conv-1",
    sequence_no: seq,
    event_type: "todo_list_updated" as RuntimeApiEventType,
    activity_kind: "event",
    created_at: "2023-11-14T22:13:20.000Z",
    payload: {
      list_id: listId ?? "run-1:todos:1",
      generation: generation ?? 1,
      todos,
    },
    ...envelope,
  } as RuntimeEventEnvelope;
}

describe("projectRunTodos", () => {
  it("returns null for a run that never opened a checklist", () => {
    nextSeq = 0;
    expect(projectRunTodos([])).toBeNull();
  });

  it("reads the newest snapshot rather than folding a sequence of diffs", () => {
    // `write_todos` replaces the whole list, so the last snapshot IS the state.
    nextSeq = 0;
    const projection = projectRunTodos([
      todoEvent([
        { content: "pull the export", status: "in_progress" },
        { content: "reconcile", status: "pending" },
      ]),
      todoEvent([
        { content: "pull the export", status: "completed" },
        { content: "reconcile", status: "in_progress" },
        { content: "resolve 14 orphan ids", status: "pending" },
      ]),
    ]);

    expect(projection).not.toBeNull();
    expect(projection?.todos).toHaveLength(3);
    expect(projection?.completedCount).toBe(1);
    expect(projection?.isComplete).toBe(false);
  });

  it("never rolls backwards when a stale snapshot arrives late", () => {
    // Replay and the live tail interleave; ordering by sequence is what stops a
    // late-delivered earlier snapshot from un-completing finished rows.
    nextSeq = 0;
    const newer = todoEvent([{ content: "step", status: "completed" }], {
      sequence_no: 9,
    });
    const older = todoEvent([{ content: "step", status: "in_progress" }], {
      sequence_no: 4,
    });

    expect(projectRunTodos([newer, older])?.isComplete).toBe(true);
  });

  it("ignores subagent checklists (they belong to the subagent views)", () => {
    nextSeq = 0;
    const projection = projectRunTodos([
      todoEvent([{ content: "child work", status: "in_progress" }], {
        subagent_id: "task-1",
      }),
    ]);

    expect(projection).toBeNull();
  });

  it("drops a snapshot whole when a row is unreadable", () => {
    // A row the client cannot place would render as pending and read as work
    // still to come — worse than showing no panel.
    nextSeq = 0;
    const unknownStatus = todoEvent([{ content: "step", status: "blocked" }]);
    const missingContent = todoEvent([{ status: "pending" } as never]);

    expect(projectRunTodos([unknownStatus])).toBeNull();
    expect(projectRunTodos([missingContent])).toBeNull();
  });

  it("ignores a snapshot with no list identity", () => {
    nextSeq = 0;
    const anonymous = todoEvent([{ content: "step", status: "pending" }]);
    delete (anonymous.payload as Record<string, unknown>).list_id;

    expect(projectRunTodos([anonymous])).toBeNull();
  });

  it("marks a list complete only when it has rows and all are done", () => {
    nextSeq = 0;
    expect(projectRunTodos([todoEvent([])])?.isComplete).toBe(false);
    expect(
      projectRunTodos([todoEvent([{ content: "a", status: "completed" }])])
        ?.isComplete,
    ).toBe(true);
  });
});

function projection(
  overrides: Partial<RunTodosProjection> = {},
): RunTodosProjection {
  const todos = overrides.todos ?? [
    { content: "Pull the Q3 pipeline export", status: "completed" as const },
    { content: "Reconcile opportunity ids", status: "in_progress" as const },
    { content: "Flag accounts that moved >20%", status: "pending" as const },
  ];
  const completedCount = todos.filter((t) => t.status === "completed").length;
  return {
    listId: "run-1:todos:1",
    generation: 1,
    todos,
    completedCount,
    isComplete: todos.length > 0 && completedCount === todos.length,
    sequenceNo: 1,
    ...overrides,
  };
}

describe("TcTodoList", () => {
  it("renders one row per todo with its status and a progress count", () => {
    render(<TcTodoList projection={projection()} />);

    const rows = screen.getAllByTestId("tc-todo-row");
    expect(rows.map((row) => row.getAttribute("data-status"))).toEqual([
      "completed",
      "in_progress",
      "pending",
    ]);
    expect(screen.getByTestId("tc-todo-list-count")).toHaveTextContent("1/3");
    expect(rows[1]).toHaveTextContent("Reconcile opportunity ids");
  });

  it("shows a spinner on the in-progress row and a tick on the completed one", () => {
    render(<TcTodoList projection={projection()} />);

    const rows = screen.getAllByTestId("tc-todo-row");
    expect(within(rows[1]).getByTestId("tc-todo-spinner")).toBeInTheDocument();
    expect(within(rows[0]).queryByTestId("tc-todo-spinner")).toBeNull();
    expect(rows[0].querySelector("svg")).not.toBeNull();
  });

  it("swaps the spinner for a tick when a row completes", () => {
    // The transition the design is about: the SAME row keeps its place and its
    // glyph changes, rather than the list re-flowing underneath the reader.
    const { rerender } = render(<TcTodoList projection={projection()} />);
    expect(
      within(screen.getAllByTestId("tc-todo-row")[1]).getByTestId(
        "tc-todo-spinner",
      ),
    ).toBeInTheDocument();

    rerender(
      <TcTodoList
        projection={projection({
          todos: [
            { content: "Pull the Q3 pipeline export", status: "completed" },
            { content: "Reconcile opportunity ids", status: "completed" },
            { content: "Flag accounts that moved >20%", status: "in_progress" },
          ],
        })}
      />,
    );

    const rows = screen.getAllByTestId("tc-todo-row");
    expect(rows[1]).toHaveAttribute("data-status", "completed");
    expect(within(rows[1]).queryByTestId("tc-todo-spinner")).toBeNull();
    expect(screen.getByTestId("tc-todo-list-count")).toHaveTextContent("2/3");
  });

  it("folds a finished list to a one-line summary", () => {
    render(
      <TcTodoList
        projection={projection({
          todos: [
            { content: "a", status: "completed" },
            { content: "b", status: "completed" },
          ],
        })}
      />,
    );

    expect(screen.getByTestId("tc-todo-list-summary")).toHaveTextContent(
      "All 2 todos complete",
    );
    expect(screen.queryAllByTestId("tc-todo-row")).toHaveLength(0);
    expect(screen.getByTestId("tc-todo-list")).toHaveAttribute(
      "data-complete",
      "true",
    );
  });

  it("keeps a finished list reachable behind the toggle", () => {
    render(
      <TcTodoList
        projection={projection({
          todos: [{ content: "a", status: "completed" }],
        })}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-todo-list-toggle"));

    expect(screen.getAllByTestId("tc-todo-row")).toHaveLength(1);
  });

  it("names the list only from the second generation on", () => {
    const { rerender } = render(<TcTodoList projection={projection()} />);
    expect(screen.queryByTestId("tc-todo-list-generation")).toBeNull();

    rerender(
      <TcTodoList
        projection={projection({ generation: 2, listId: "run-1:todos:2" })}
      />,
    );
    expect(screen.getByTestId("tc-todo-list-generation")).toHaveTextContent(
      "List 2",
    );
  });

  it("re-expands when the agent opens a new list after the user collapsed one", () => {
    // Collapsing list 1 is not a statement about list 2.
    const { rerender } = render(<TcTodoList projection={projection()} />);
    fireEvent.click(screen.getByTestId("tc-todo-list-toggle"));
    expect(screen.queryAllByTestId("tc-todo-row")).toHaveLength(0);

    rerender(
      <TcTodoList
        projection={projection({
          listId: "run-1:todos:2",
          generation: 2,
          todos: [{ content: "fresh work", status: "in_progress" }],
        })}
      />,
    );
    expect(screen.getAllByTestId("tc-todo-row")).toHaveLength(1);
  });

  it("renders nothing when the agent cleared its list", () => {
    render(<TcTodoList projection={projection({ todos: [] })} />);

    expect(screen.queryByTestId("tc-todo-list")).toBeNull();
  });
});
