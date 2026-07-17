import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Todo, ListTodosResponse, TodoId } from "../../api/_todos-stub";

// Mock chat-surface BEFORE TodosRoute pulls it in — <TodosDestination />
// would otherwise need full Transport / Router provider scaffolding to
// mount; its own rendering is exercised in the chat-surface package
// test suite.
vi.mock("@0x-copilot/chat-surface", () => ({
  TodosDestination: () => <div data-testid="todos-destination-stub">stub</div>,
}));

// Mock the host-side ports module so `usePort("badge")` returns a stub
// whose `setBadge` calls we assert against. `usePort` itself lives in
// `apps/frontend/src/ports/PortProvider.tsx`; the production
// implementation throws when no <PortProvider> is mounted, so the
// route test substitutes the mock here rather than wrapping every
// `render()` call in a provider.
const badgeSetBadge = vi.fn();
vi.mock("../../ports", () => ({
  usePort: (_name: string) => ({ setBadge: badgeSetBadge }),
}));

// Mock the todosApi module so the tests don't have to drive the real
// fetch — that surface is covered in todosApi.test.ts.
const todosApiMocks = vi.hoisted(() => ({
  fetchTodos: vi.fn(),
}));
vi.mock("../../api/todosApi", async () => {
  const actual =
    await vi.importActual<typeof import("../../api/todosApi")>(
      "../../api/todosApi",
    );
  return {
    ...actual,
    fetchTodos: todosApiMocks.fetchTodos,
  };
});

// Imports below this line resolve through the mocks above.
import { TodosRoute, computeOverdueCount } from "./TodosRoute";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

function todo(overrides: Partial<Todo> = {}): Todo {
  return {
    id: "todo_1" as TodoId,
    tenant_id: "tenant_1",
    owner_user_id: "user_test",
    text: "Ship the brief",
    done: false,
    priority: "med",
    source: { kind: "user" },
    labels: [],
    sort_index: 0,
    created_at: "2026-05-18T09:00:00Z",
    updated_at: "2026-05-18T09:00:00Z",
    ...overrides,
  };
}

function payload(items: ReadonlyArray<Todo>): ListTodosResponse {
  return { items };
}

describe("computeOverdueCount", () => {
  it("counts only open todos whose due date is strictly before today", () => {
    const now = new Date(2026, 4, 18); // 2026-05-18 local
    const items = [
      todo({ id: "a" as TodoId, due: "2026-05-17" }), // overdue
      todo({ id: "b" as TodoId, due: "2026-05-18" }), // today — not overdue
      todo({ id: "c" as TodoId, due: "2026-05-19" }), // future
      todo({ id: "d" as TodoId, due: "2026-05-01", done: true }), // done; excluded
      todo({ id: "e" as TodoId }), // no due
    ];
    expect(computeOverdueCount(items, now)).toBe(1);
  });

  it("ignores malformed `due` strings rather than crashing", () => {
    const now = new Date(2026, 4, 18);
    expect(
      computeOverdueCount(
        [
          todo({ id: "x" as TodoId, due: "not-a-date" }),
          todo({ id: "y" as TodoId, due: "" }),
        ],
        now,
      ),
    ).toBe(0);
  });

  // Regression: the server emits `"due": null` for an undated todo — it
  // sends the key with a null value rather than omitting it. The original
  // guard only checked `=== undefined`, so a null fell through to
  // `due.split("-")` and threw, and the error boundary tore down the whole
  // app shell. The case above (`todo({ id: "e" })`) only ever exercised
  // *absent*, which is why this shipped. Pin the real wire shape.
  it("treats a null `due` as undated instead of throwing", () => {
    const now = new Date(2026, 4, 18);
    const items = [
      todo({ id: "n1" as TodoId, due: null }),
      todo({ id: "n2" as TodoId, due: null, done: true }),
      todo({ id: "n3" as TodoId, due: "2026-05-17" }), // still counted
    ];
    expect(() => computeOverdueCount(items, now)).not.toThrow();
    expect(computeOverdueCount(items, now)).toBe(1);
  });
});

describe("TodosRoute", () => {
  beforeEach(() => {
    todosApiMocks.fetchTodos.mockReset();
    badgeSetBadge.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the destination after the todos payload loads", async () => {
    todosApiMocks.fetchTodos.mockResolvedValue(payload([todo()]));

    render(<TodosRoute identity={IDENTITY} />);

    expect(screen.getByTestId("todos-route")).toHaveAttribute(
      "data-state",
      "loading",
    );

    await waitFor(() =>
      expect(screen.getByTestId("todos-route")).toHaveAttribute(
        "data-state",
        "ready",
      ),
    );
    expect(screen.getByTestId("todos-destination-stub")).toBeInTheDocument();
  });

  it("fetches open todos via /v1/todos with the correct filter shape", async () => {
    todosApiMocks.fetchTodos.mockResolvedValue(payload([]));

    render(<TodosRoute identity={IDENTITY} />);

    await waitFor(() => expect(todosApiMocks.fetchTodos).toHaveBeenCalled());
    const args = todosApiMocks.fetchTodos.mock.calls[0];
    expect(args[0]).toEqual(IDENTITY);
    expect(args[1]).toMatchObject({
      filters: { done: false },
      sort: "due:asc",
      limit: 200,
    });
    // No project filter on the top-level destination — sub-PRD §16 Q6
    // says project default is `null` on /todos.
    expect(args[1].filters.project_id).toBeUndefined();
  });

  it("filters by project when projectId is passed (context-aware default)", async () => {
    todosApiMocks.fetchTodos.mockResolvedValue(payload([]));

    render(<TodosRoute identity={IDENTITY} projectId="proj_42" />);

    await waitFor(() => expect(todosApiMocks.fetchTodos).toHaveBeenCalled());
    expect(
      todosApiMocks.fetchTodos.mock.calls[0][1].filters.project_id,
    ).toEqual(["proj_42"]);
    // Project id surfaced for the destination's inline-add default.
    await waitFor(() =>
      expect(screen.getByTestId("todos-route")).toHaveAttribute(
        "data-default-project-id",
        "proj_42",
      ),
    );
  });

  it("pushes the overdue count to BadgePort on every list refresh", async () => {
    // Day in the future so the fixture overdue dates are unambiguously past.
    vi.setSystemTime(new Date(2099, 0, 1));
    todosApiMocks.fetchTodos.mockResolvedValue(
      payload([
        todo({ id: "a" as TodoId, due: "2026-05-17" }),
        todo({ id: "b" as TodoId, due: "2026-05-17" }),
        todo({ id: "c" as TodoId }), // no due — ignored
      ]),
    );

    render(<TodosRoute identity={IDENTITY} />);

    await waitFor(() =>
      expect(screen.getByTestId("todos-route")).toHaveAttribute(
        "data-state",
        "ready",
      ),
    );

    // setBadge fires at least once with the right slug + count.
    // Loading -> ready transitions both push (the loading push is 0).
    expect(badgeSetBadge).toHaveBeenCalledWith("todos", 0);
    expect(badgeSetBadge).toHaveBeenLastCalledWith("todos", 2);
    expect(screen.getByTestId("todos-route")).toHaveAttribute(
      "data-overdue-count",
      "2",
    );

    vi.useRealTimers();
  });

  it("renders an error state with a working retry when the fetch fails", async () => {
    todosApiMocks.fetchTodos.mockRejectedValueOnce(
      new Error("tenant lookup failed"),
    );
    todosApiMocks.fetchTodos.mockResolvedValueOnce(payload([]));

    render(<TodosRoute identity={IDENTITY} />);

    await waitFor(() =>
      expect(screen.getByTestId("todos-route-error")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("todos-route-error-message")).toHaveTextContent(
      /tenant lookup failed/,
    );

    fireEvent.click(screen.getByTestId("todos-route-retry"));

    await waitFor(() =>
      expect(screen.getByTestId("todos-route")).toHaveAttribute(
        "data-state",
        "ready",
      ),
    );
    expect(todosApiMocks.fetchTodos).toHaveBeenCalledTimes(2);
  });

  it("does NOT push a badge count from the error state (count is undefined)", async () => {
    todosApiMocks.fetchTodos.mockRejectedValue(new Error("boom"));

    render(<TodosRoute identity={IDENTITY} />);

    await waitFor(() =>
      expect(screen.getByTestId("todos-route-error")).toBeInTheDocument(),
    );

    // Error state's overdue count is 0 — the badge must not show a stale
    // value, but it also must not be left at the prior render's count.
    // Loading -> error: badge stays at 0.
    for (const call of badgeSetBadge.mock.calls) {
      expect(call[1]).toBe(0);
    }
  });
});
