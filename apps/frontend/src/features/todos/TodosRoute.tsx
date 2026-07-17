// TodosRoute — data binder for the Todos destination.
//
// Mirrors `apps/frontend/src/features/home/HomeRoute.tsx` (P2-C):
//   1. Fetches `GET /v1/todos` (open queue) via `todosApi` and owns
//      loading / error state.
//   2. Computes the overdue count from the loaded payload and pushes
//      it to `BadgePort.setBadge("todos", overdueCount)` per sub-PRD
//      §14.1. Web host's `WebBadgePort` is a no-op; desktop substrate
//      lights up the dock / tray badge with the same call.
//   3. Threads the **context-aware project default** (sub-PRD §16 Q6
//      / cross-audit §9.6) into `<TodosDestination>` — `projectId`
//      prop, present when the host route is a project-detail view,
//      `null` on `/todos`. The inline-add UI uses this to default
//      `project_id` on new todos; the panel filter inherits it.
//   4. Renders `<TodosDestination>` (the chat-surface presentational
//      shell) inside a host-side `<section>` matching the HomeRoute
//      data attributes.
//
// Why a feature-level wrapper, not props on `<TodosDestination>` today:
// the current `<TodosDestination>` in chat-surface is a Wave-1 scaffold
// that does its own fetch and renders a placeholder. Phase 3 Impl-B
// rewrites it into a controlled component that accepts the full Todos
// payload + the project default as props (sub-PRD §15.1). Until then,
// TodosRoute owns the data flow + error state + badge wiring without
// forcing a breaking change on the package boundary. The orchestrator
// rewires the prop hand-off at merge — see `TODO(merge)` markers.

import { useEffect, useMemo, useState, type ReactElement } from "react";

import { TodosDestination } from "@enterprise-search/chat-surface";

import type { RequestIdentity } from "../../api/config";
import { fetchTodos } from "../../api/todosApi";
import type { ListTodosResponse, Todo } from "../../api/_todos-stub";
import { usePort } from "../../ports";
import { errorMessage } from "../../utils/errors";

interface TodosRouteProps {
  readonly identity: RequestIdentity;
  /**
   * Context-aware project default (sub-PRD §16 Q6, cross-audit §9.6).
   *
   * - Pass the current project id when the host's route is a project
   *   detail view; `<TodosDestination>` will default new-todo
   *   `project_id` to this value and pre-filter the panel.
   * - Pass `null` on the top-level `/todos` destination so inline-add
   *   inherits the panel's current filter (or "Unfiled" when none).
   *
   * The destination NEVER reads project state from the router itself
   * — keeping this prop in the host's hands is what makes the same
   * `<TodosDestination>` component reusable inside the projects
   * destination's right-rail without a circular dependency.
   */
  readonly projectId?: string | null;
}

type ViewState =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | { readonly kind: "ready"; readonly payload: ListTodosResponse };

/**
 * Overdue-count compute. Sub-PRD §14.1 dictates the badge surfaces
 * `open && (overdue || due today)` — but the badge value is the
 * OVERDUE count specifically (matches the cross-audit §9.6 binding:
 * "BadgePort wiring per Phase 0.5"). Today's items remain visible in
 * the UI but don't bump the dock badge until they roll into overdue.
 *
 * Pure function over the loaded list so a test can drive it without a
 * mounted component.
 */
export function computeOverdueCount(
  todos: ReadonlyArray<Todo>,
  /** Injectable "now" — defaults to wall clock; tests pin it. */
  now: Date = new Date(),
): number {
  // Build "start of today" in the local tz — `due` is a date with no
  // time component (sub-PRD §5.1), interpreted in the user's tz.
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  let count = 0;
  for (const t of todos) {
    if (t.done) continue;
    // `== null` on purpose: the server emits `"due": null` for undated
    // todos rather than omitting the key, so an `=== undefined` guard
    // lets the null through to `.split()` below and takes down the whole
    // destination via the error boundary. Covers both null and undefined.
    if (t.due == null) continue;
    // Parse the YYYY-MM-DD as a local date (NOT UTC). The server emits
    // a date-only string; `new Date("2026-05-17")` would parse as UTC
    // midnight and shift across the dateline.
    const parts = t.due.split("-").map((n) => Number.parseInt(n, 10));
    if (parts.length !== 3 || parts.some((n) => Number.isNaN(n))) {
      continue;
    }
    const due = new Date(parts[0], parts[1] - 1, parts[2]);
    if (due.getTime() < today.getTime()) {
      count += 1;
    }
  }
  return count;
}

export function TodosRoute({
  identity,
  projectId,
}: TodosRouteProps): ReactElement {
  const badgePort = usePort("badge");
  const [state, setState] = useState<ViewState>({ kind: "loading" });
  const [reloadToken, setReloadToken] = useState(0);

  // ---- HTTP fetch ---------------------------------------------------
  //
  // We fetch ONLY open todos here — the destination's own component
  // handles paginated "Done" via a follow-up call once the user scrolls
  // into it. Server-side bucketing is intentionally avoided per
  // sub-PRD §8 (client buckets the flat list by `due`).
  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    fetchTodos(identity, {
      filters: {
        done: false,
        ...(projectId ? { project_id: [projectId] } : {}),
      },
      sort: "due:asc",
      limit: 200,
    })
      .then((payload) => {
        if (cancelled) return;
        setState({ kind: "ready", payload });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          kind: "error",
          message: errorMessage(error, "Could not load todos."),
        });
      });
    return () => {
      cancelled = true;
    };
  }, [identity, projectId, reloadToken]);

  // ---- BadgePort wiring -------------------------------------------
  //
  // Sub-PRD §14.1: the destination computes the badge count from the
  // loaded todos and pushes it on every list refresh. The web host's
  // implementation is a no-op (cross-audit §1.2); desktop substrates
  // update the OS dock / tray icon with the same call.
  //
  // `useMemo` so the count is stable across renders that don't change
  // the underlying list — avoids spurious `setBadge` calls on the
  // host (the port is allowed to debounce but shouldn't have to).
  const overdueCount = useMemo(() => {
    if (state.kind !== "ready") return 0;
    return computeOverdueCount(state.payload.items);
  }, [state]);

  useEffect(() => {
    badgePort.setBadge("todos", overdueCount);
  }, [badgePort, overdueCount]);

  // ---- Render -------------------------------------------------------
  if (state.kind === "error") {
    return (
      <section
        aria-label="Todos destination"
        data-testid="todos-route"
        data-state="error"
        style={{
          height: "100%",
          width: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 24,
          boxSizing: "border-box",
          backgroundColor: "var(--color-bg)",
          color: "var(--color-text)",
        }}
      >
        <div
          role="alert"
          data-testid="todos-route-error"
          style={{
            border: "1px solid var(--color-border)",
            borderRadius: 12,
            backgroundColor: "var(--color-surface)",
            padding: 32,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 12,
            maxWidth: 480,
          }}
        >
          <div style={{ fontSize: 14, fontWeight: 600 }}>
            Could not load todos
          </div>
          <div
            style={{ fontSize: 13, color: "var(--color-text-muted)" }}
            data-testid="todos-route-error-message"
          >
            {state.message}
          </div>
          <button
            type="button"
            data-testid="todos-route-retry"
            onClick={() => setReloadToken((t) => t + 1)}
            style={{
              height: 32,
              padding: "0 14px",
              borderRadius: 8,
              border: "1px solid var(--color-border-strong)",
              backgroundColor: "transparent",
              color: "var(--color-accent)",
              fontSize: 13,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Retry
          </button>
        </div>
      </section>
    );
  }

  // Loading + ready both render <TodosDestination />. Today (Wave 1
  // seed) `<TodosDestination>` runs its own fetch + renders a
  // placeholder, so passing the payload through is a no-op. The
  // wrapper is here so Phase 3 Impl-B's controlled rewrite of
  // TodosDestination can accept `payload` (+ `projectId` for the
  // context-aware default + `onMutate` callback) without any
  // App.tsx-level rewiring.
  //
  // TODO(merge): once Impl-B's TodosDestination accepts props, pass:
  //   <TodosDestination
  //     payload={state.kind === "ready" ? state.payload : null}
  //     defaultProjectId={projectId ?? null}
  //     identity={identity}
  //     onRetry={() => setReloadToken((t) => t + 1)}
  //   />
  return (
    <section
      aria-label="Todos destination"
      data-testid="todos-route"
      data-state={state.kind}
      data-overdue-count={overdueCount}
      data-default-project-id={projectId ?? ""}
      style={{ height: "100%", width: "100%", overflow: "auto" }}
    >
      <TodosDestination />
    </section>
  );
}
