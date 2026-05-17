// TodosDestination shell tests (P3-B1).
//
// Covers: loading skeleton, error/unavailable empty states, section
// bucketing (overdue / today / this_week / upcoming / no_due / done),
// done-section collapse, recurrence chip render, subtask nest
// collapse/expand, bulk-select toolbar.

import type {
  AgentId,
  ConversationId,
  ProjectId,
  RunId,
  SectionResult,
  TodoId,
} from "@enterprise-search/api-types";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../../providers/RouterProvider";
import type { ArtifactRoute, Router } from "../../routing/router";

// Import the destination's index for ItemRef resolver side effects —
// registers kind `"todo"`. The shell also renders <ItemLink> chips for
// `project`, `chat`, `run`, and `agent` sources; home owns chat/run
// resolvers (imported below). Project + agent resolvers may be
// unregistered in this test context — ItemLink falls back to the
// deleted-chip path which still carries `data-item-kind` for the
// wiring assertion below.
import "./index";
import "../home/index";

// TODO(merge): rewire to "@enterprise-search/api-types"
import type {
  Todo,
  TodoExtraction,
  TodoSeriesId,
  TodosPayload,
} from "./_todos-stub";

// Helper to mint branded ids inside fixtures.
const asTodoId = (s: string): TodoId => s as unknown as TodoId;
const asSeriesId = (s: string): TodoSeriesId => s as unknown as TodoSeriesId;
import {
  TodosDestination,
  type TodosDestinationProps,
} from "./TodosDestination";

// ===========================================================================
// Test scaffolding
// ===========================================================================

function makeRouter(): Router<ArtifactRoute> & {
  navigate: ReturnType<typeof vi.fn>;
} {
  let current: ArtifactRoute | null = null;
  const subscribers = new Set<(r: ArtifactRoute) => void>();
  const navigate = vi.fn((r: ArtifactRoute) => {
    current = r;
    for (const s of subscribers) s(r);
  });
  return {
    current(): ArtifactRoute {
      if (current === null) throw new Error("no route");
      return current;
    },
    navigate,
    subscribe(handler) {
      subscribers.add(handler);
      return () => subscribers.delete(handler);
    },
  };
}

function renderTodos(props: TodosDestinationProps = {}): void {
  const router = makeRouter();
  render(
    <RouterProvider router={router}>
      <TodosDestination {...props} />
    </RouterProvider>,
  );
}

// ===========================================================================
// Fixtures
// ===========================================================================

// "Now" is pinned to 2026-05-17 12:00 UTC. start_of_today_utc = 2026-05-17.
const NOW = Date.parse("2026-05-17T12:00:00.000Z");

function ok<T>(data: T): SectionResult<T> {
  return { status: "ok", data };
}

type TodoInit = Omit<Partial<Todo>, "id"> & { readonly id: string };

function makeTodo(over: TodoInit): Todo {
  return {
    id: over.id as unknown as TodoId,
    text: over.text ?? "Some todo",
    done: over.done ?? false,
    completed_at: over.completed_at,
    due: over.due,
    priority: over.priority ?? "med",
    source: over.source ?? { kind: "user" },
    project_id: over.project_id,
    labels: over.labels ?? [],
    sort_index: over.sort_index ?? 0,
    created_at: over.created_at ?? "2026-05-10T00:00:00.000Z",
    updated_at: over.updated_at ?? "2026-05-10T00:00:00.000Z",
    recurrence: over.recurrence,
    parent_id: over.parent_id,
    sort_index_within_parent: over.sort_index_within_parent,
  };
}

const TODO_OVERDUE = makeTodo({
  id: "todo_overdue",
  text: "File Q1 taxes",
  due: "2026-05-15T00:00:00.000Z",
  priority: "high",
});

const TODO_TODAY = makeTodo({
  id: "todo_today",
  text: "Daily standup notes",
  due: "2026-05-17T00:00:00.000Z",
  priority: "med",
});

const TODO_WEEK = makeTodo({
  id: "todo_week",
  text: "Renewal narrative for Globex",
  due: "2026-05-20T00:00:00.000Z",
  priority: "med",
  source: {
    kind: "chat",
    thread_id: "conv_abc",
  },
});

const TODO_UPCOMING = makeTodo({
  id: "todo_upcoming",
  text: "Quarterly review prep",
  due: "2026-06-15T00:00:00.000Z",
  priority: "low",
});

const TODO_NO_DUE = makeTodo({
  id: "todo_nodue",
  text: "Read the runbook",
  priority: "low",
});

const TODO_DONE = makeTodo({
  id: "todo_done",
  text: "Coffee with intern",
  done: true,
  completed_at: "2026-05-15T15:00:00.000Z",
});

const TODO_RECURRING = makeTodo({
  id: "todo_recurring",
  text: "Weekly stakeholder sync",
  due: "2026-05-17T00:00:00.000Z",
  priority: "med",
  recurrence: {
    rule: "rrule",
    spec: "FREQ=WEEKLY;BYDAY=MO",
    next_materialize_at: "2026-05-24T00:00:00.000Z",
    series_id: asSeriesId("series_a"),
  },
});

const TODO_PARENT = makeTodo({
  id: "todo_parent",
  text: "Plan offsite",
  due: "2026-05-17T00:00:00.000Z",
  priority: "high",
});

const TODO_SUBTASK_A = makeTodo({
  id: "todo_sub_a",
  text: "Book the venue",
  due: "2026-05-17T00:00:00.000Z",
  parent_id: asTodoId("todo_parent"),
  sort_index_within_parent: 0,
});

const TODO_SUBTASK_B = makeTodo({
  id: "todo_sub_b",
  text: "Send invites",
  due: "2026-05-17T00:00:00.000Z",
  parent_id: asTodoId("todo_parent"),
  sort_index_within_parent: 1,
});

const FULL_PAYLOAD_TODOS: ReadonlyArray<Todo> = [
  TODO_OVERDUE,
  TODO_TODAY,
  TODO_WEEK,
  TODO_UPCOMING,
  TODO_NO_DUE,
  TODO_DONE,
];

// ===========================================================================
// Tests
// ===========================================================================

describe("TodosDestination", () => {
  it("renders the skeleton state when todos is null", () => {
    renderTodos({ todos: null });
    const region = screen.getByRole("region", { name: /todos destination/i });
    expect(region).toHaveAttribute("data-state", "loading");
    expect(
      screen.getAllByTestId("todos-skeleton-section").length,
    ).toBeGreaterThan(0);
  });

  it("renders the whole-list error state with a retry button when status=error", () => {
    const onRetry = vi.fn();
    renderTodos({
      todos: { status: "error", error: "Network exploded" },
      onRetry,
    });
    const region = screen.getByRole("region", { name: /todos destination/i });
    expect(region).toHaveAttribute("data-state", "error");
    expect(screen.getByTestId("empty-state")).toHaveTextContent(
      /could not load todos/i,
    );
    expect(screen.getByTestId("empty-state")).toHaveTextContent(
      /network exploded/i,
    );
    fireEvent.click(screen.getByTestId("empty-state-action"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("renders the unavailable state when status=unavailable", () => {
    renderTodos({
      todos: { status: "unavailable", error: "Disabled for tenant" },
    });
    const region = screen.getByRole("region", { name: /todos destination/i });
    expect(region).toHaveAttribute("data-state", "unavailable");
    expect(screen.getByTestId("empty-state")).toHaveTextContent(
      /todos unavailable/i,
    );
  });

  it("renders the all-empty state when status=ok with no rows", () => {
    renderTodos({ todos: ok<ReadonlyArray<Todo>>([]) });
    expect(screen.getByTestId("empty-state")).toHaveTextContent(
      /nothing here yet/i,
    );
  });

  it("buckets todos into the six sections (overdue / today / this_week / upcoming / no_due / done)", () => {
    renderTodos({
      todos: ok<ReadonlyArray<Todo>>(FULL_PAYLOAD_TODOS),
      now: NOW,
      initialDoneCollapsed: false,
    });

    expect(
      screen
        .getByTestId("todos-section-overdue")
        .getAttribute("data-row-count"),
    ).toBe("1");
    expect(
      screen.getByTestId("todos-section-today").getAttribute("data-row-count"),
    ).toBe("1");
    expect(
      screen
        .getByTestId("todos-section-this_week")
        .getAttribute("data-row-count"),
    ).toBe("1");
    expect(
      screen
        .getByTestId("todos-section-upcoming")
        .getAttribute("data-row-count"),
    ).toBe("1");
    expect(
      screen.getByTestId("todos-section-no_due").getAttribute("data-row-count"),
    ).toBe("1");
    expect(
      screen.getByTestId("todos-section-done").getAttribute("data-row-count"),
    ).toBe("1");
  });

  it("does NOT render sections with zero rows", () => {
    renderTodos({
      todos: ok<ReadonlyArray<Todo>>([TODO_TODAY]),
      now: NOW,
    });

    expect(screen.queryByTestId("todos-section-overdue")).toBeNull();
    expect(screen.queryByTestId("todos-section-this_week")).toBeNull();
    expect(screen.queryByTestId("todos-section-upcoming")).toBeNull();
    expect(screen.queryByTestId("todos-section-no_due")).toBeNull();
    expect(screen.queryByTestId("todos-section-done")).toBeNull();
    expect(screen.getByTestId("todos-section-today")).toBeInTheDocument();
  });

  it("hides the Done section body by default (collapsed) and shows it on toggle", () => {
    renderTodos({
      todos: ok<ReadonlyArray<Todo>>([TODO_DONE, TODO_TODAY]),
      now: NOW,
    });

    const doneSection = screen.getByTestId("todos-section-done");
    const collapseButton = screen.getByTestId("todos-section-done-collapse");
    expect(collapseButton).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("todos-section-done-body")).toBeNull();

    fireEvent.click(collapseButton);
    expect(collapseButton).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("todos-section-done-body")).toBeInTheDocument();
    expect(doneSection).toBeInTheDocument();
  });

  it("renders the recurrence chip on todos that carry a recurrence spec", () => {
    renderTodos({
      todos: ok<ReadonlyArray<Todo>>([TODO_RECURRING]),
      now: NOW,
    });
    const chip = screen.getByTestId("todo-row-recurrence-chip");
    expect(chip).toHaveAttribute("data-todo-id", "todo_recurring");
    expect(chip).toHaveTextContent(/recurring/i);
  });

  it("invokes the onEditRecurrence callback when the recurrence chip is clicked", () => {
    const onEditRecurrence = vi.fn();
    renderTodos({
      todos: ok<ReadonlyArray<Todo>>([TODO_RECURRING]),
      now: NOW,
      onEditRecurrence,
    });
    fireEvent.click(screen.getByTestId("todo-row-recurrence-chip"));
    expect(onEditRecurrence).toHaveBeenCalledWith("todo_recurring");
  });

  it("nests subtasks under the parent in the same section, collapsed by default", () => {
    renderTodos({
      todos: ok<ReadonlyArray<Todo>>([
        TODO_PARENT,
        TODO_SUBTASK_A,
        TODO_SUBTASK_B,
      ]),
      now: NOW,
    });

    const parentRow = screen.getByTestId("todo-row");
    expect(parentRow).toHaveAttribute("data-todo-id", "todo_parent");
    expect(parentRow).toHaveAttribute("data-has-subtasks", "true");

    const expandButton = screen.getByTestId("todo-row-expand");
    expect(expandButton).toHaveAttribute("aria-expanded", "false");

    // Subtask container is not in the DOM while collapsed.
    expect(screen.queryByTestId("todo-row-todo_parent-subtasks")).toBeNull();

    fireEvent.click(expandButton);
    expect(expandButton).toHaveAttribute("aria-expanded", "true");

    // Now both subtasks render.
    const container = screen.getByTestId("todo-row-todo_parent-subtasks");
    expect(container).toBeInTheDocument();
    expect(screen.getAllByTestId("subtask-row")).toHaveLength(2);
  });

  it("renders the bulk-action toolbar when at least one row is selected", () => {
    renderTodos({
      todos: ok<ReadonlyArray<Todo>>([TODO_TODAY, TODO_WEEK]),
      now: NOW,
      onBulkMarkDone: vi.fn(),
      onBulkDelete: vi.fn(),
    });

    // No selection: no toolbar.
    expect(screen.queryByTestId("todos-bulk-bar")).toBeNull();

    const selectInputs = screen.getAllByTestId("todo-row-select");
    fireEvent.click(selectInputs[0]!);
    expect(screen.getByTestId("todos-bulk-bar")).toBeInTheDocument();
    expect(screen.getByTestId("todos-bulk-bar")).toHaveTextContent(
      /1 selected/,
    );

    fireEvent.click(selectInputs[1]!);
    expect(screen.getByTestId("todos-bulk-bar")).toHaveTextContent(
      /2 selected/,
    );
  });

  it("calls onBulkMarkDone with the selected ids", () => {
    const onBulkMarkDone = vi.fn();
    renderTodos({
      todos: ok<ReadonlyArray<Todo>>([TODO_TODAY, TODO_WEEK]),
      now: NOW,
      onBulkMarkDone,
    });

    fireEvent.click(screen.getAllByTestId("todo-row-select")[0]!);
    fireEvent.click(screen.getAllByTestId("todo-row-select")[1]!);
    fireEvent.click(screen.getByTestId("todos-bulk-mark-done"));

    expect(onBulkMarkDone).toHaveBeenCalledTimes(1);
    const callArg = onBulkMarkDone.mock.calls[0]![0] as ReadonlyArray<TodoId>;
    expect(new Set(callArg)).toEqual(new Set(["todo_today", "todo_week"]));
  });

  it("invokes the inline-add slot for non-empty sections (when supplied)", () => {
    const renderInlineAdd = vi.fn().mockReturnValue("INLINE_ADD_SLOT");
    renderTodos({
      todos: ok<ReadonlyArray<Todo>>([TODO_TODAY]),
      now: NOW,
      renderInlineAdd,
    });

    expect(
      screen.getByTestId("todos-section-today-inline-add-slot"),
    ).toBeInTheDocument();
    // It's called with the section key for context-aware defaults.
    expect(renderInlineAdd).toHaveBeenCalledWith(
      expect.objectContaining({ sectionKey: "today" }),
    );
  });

  it("invokes the extraction-banner slot when extractions are present", () => {
    const renderExtractionBanner = vi.fn().mockReturnValue("EXTRACTION_BANNER");
    const extractions: ReadonlyArray<TodoExtraction> = [
      {
        id: "ext_1" as unknown as TodoExtraction["id"],
        source: { thread_id: "conv_a", run_id: "run_a" as RunId },
        proposed_todos: [{ text: "Do X", priority: "med" }],
        status: "pending",
        created_at: "2026-05-17T10:00:00.000Z",
      },
    ];
    renderTodos({
      todos: ok<ReadonlyArray<Todo>>([TODO_TODAY]),
      now: NOW,
      extractions,
      renderExtractionBanner,
    });

    const slot = screen.getByTestId("todos-extraction-banner-slot");
    expect(slot).toHaveAttribute("data-extraction-count", "1");
    expect(renderExtractionBanner).toHaveBeenCalledWith(
      expect.objectContaining({ extractions }),
    );
  });

  it("invokes onCompleteTodo when the done checkbox is toggled", () => {
    const onCompleteTodo = vi.fn();
    renderTodos({
      todos: ok<ReadonlyArray<Todo>>([TODO_TODAY]),
      now: NOW,
      onCompleteTodo,
    });
    fireEvent.click(screen.getByTestId("todo-row-done"));
    expect(onCompleteTodo).toHaveBeenCalledWith("todo_today", true);
  });

  it("renders ItemLink chips for chat, run, agent, and project sources", () => {
    const TODO_WITH_PROJECT = makeTodo({
      id: "todo_with_project",
      text: "Renewal narrative",
      due: "2026-05-17T00:00:00.000Z",
      project_id: "proj_acme" as ProjectId,
      source: {
        kind: "agent",
        agent_id: "agent_drafter" as AgentId,
        run_id: "run_xyz" as RunId,
      },
    });
    renderTodos({
      todos: ok<ReadonlyArray<Todo>>([TODO_WITH_PROJECT]),
      now: NOW,
    });

    // Both the project chip and the run chip should mount; ItemLink
    // renders either a skeleton (resolver registered) or a deleted
    // chip (resolver missing) — both carry `data-testid` that we can
    // query through RTL.
    const links = [
      ...screen.queryAllByTestId("item-link-skeleton"),
      ...screen.queryAllByTestId("item-link"),
      ...screen.queryAllByTestId("item-link-deleted"),
    ];
    const kinds = links.map((l) => l.getAttribute("data-item-kind"));
    expect(kinds).toContain("project");
    expect(kinds).toContain("run");
  });

  it("exports a bucketTodos helper that buckets a flat list correctly", async () => {
    const { bucketTodos } = await import("./TodosDestination");
    const buckets = bucketTodos(
      ok<ReadonlyArray<Todo>>(FULL_PAYLOAD_TODOS),
      NOW,
    );
    expect(buckets.get("overdue")).toHaveLength(1);
    expect(buckets.get("today")).toHaveLength(1);
    expect(buckets.get("this_week")).toHaveLength(1);
    expect(buckets.get("upcoming")).toHaveLength(1);
    expect(buckets.get("no_due")).toHaveLength(1);
    expect(buckets.get("done")).toHaveLength(1);

    // Done items older than 14 days are dropped from the done bucket.
    const ancientDone = makeTodo({
      id: "todo_ancient",
      done: true,
      completed_at: "2024-01-01T00:00:00.000Z",
    });
    const buckets2 = bucketTodos(ok<ReadonlyArray<Todo>>([ancientDone]), NOW);
    expect(buckets2.get("done")).toHaveLength(0);
  });

  it("returns empty buckets when given null or a non-ok SectionResult", async () => {
    const { bucketTodos } = await import("./TodosDestination");
    const a = bucketTodos(null, NOW);
    expect(a.get("today")).toHaveLength(0);
    const b = bucketTodos(
      { status: "error", error: "x" } as SectionResult<ReadonlyArray<Todo>>,
      NOW,
    );
    expect(b.get("today")).toHaveLength(0);
  });
});
