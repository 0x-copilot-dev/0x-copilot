import {
  useCallback,
  useEffect,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import { useRouter } from "../../providers/RouterProvider";
import { useTransport } from "../../providers/TransportProvider";
import type { ArtifactRoute } from "../../routing/router";

export type TodoStatusFilter = "open" | "done" | "all";

export type TodoId = string & { readonly __brand: "TodoId" };

export interface Todo {
  readonly id: TodoId;
  readonly title: string;
  readonly completed: boolean;
  readonly dueAt?: string;
  readonly source?: string;
  readonly route?: ArtifactRoute;
}

export interface TodosPayload {
  readonly todos: ReadonlyArray<Todo>;
}

type ViewState =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | { readonly kind: "ready"; readonly todos: ReadonlyArray<Todo> };

interface FilterDescriptor {
  readonly slug: TodoStatusFilter;
  readonly label: string;
  readonly emptyTitle: string;
  readonly emptyHint: string;
}

const FILTERS: ReadonlyArray<FilterDescriptor> = [
  {
    slug: "open",
    label: "Open",
    emptyTitle: "Nothing open",
    emptyHint: "Todos created by you or the agent will show up here.",
  },
  {
    slug: "done",
    label: "Done",
    emptyTitle: "No completed todos yet",
    emptyHint: "Finished items live here so you can find them later.",
  },
  {
    slug: "all",
    label: "All",
    emptyTitle: "No todos yet",
    emptyHint: "Open and completed items will be listed here.",
  },
];

const APP_BACKGROUND = "#0F1218";
const PANEL_BACKGROUND = "#131722";
const PANEL_BORDER = "#22252E";
const PANEL_BORDER_STRONG = "#2C3140";
const TEXT_PRIMARY = "#E4E5E9";
const TEXT_SECONDARY = "#7E8492";
const TEXT_FAINT = "#5A606E";
const ACCENT = "#7B9BFF";
const DANGER = "#E26A6A";

const SKELETON_ROW_COUNT = 4;

function formatDueDate(iso: string, now: number = Date.now()): string {
  const parsed = Date.parse(iso);
  if (Number.isNaN(parsed)) return "—";
  const diff = parsed - now;
  const day = 86_400_000;
  if (diff < -day) {
    const days = Math.floor(-diff / day);
    return `${days}d overdue`;
  }
  if (diff < 0) return "Due today";
  if (diff < day) return "Due today";
  if (diff < 2 * day) return "Due tomorrow";
  const days = Math.floor(diff / day);
  if (days < 30) return `Due in ${days}d`;
  const months = Math.floor(days / 30);
  if (months < 12) return `Due in ${months}mo`;
  const years = Math.floor(months / 12);
  return `Due in ${years}y`;
}

function dueDateColor(iso: string, now: number = Date.now()): string {
  const parsed = Date.parse(iso);
  if (Number.isNaN(parsed)) return TEXT_SECONDARY;
  if (parsed < now) return DANGER;
  return TEXT_SECONDARY;
}

function SkeletonRow({ index }: { index: number }): ReactElement {
  const style: CSSProperties = {
    height: 56,
    borderRadius: 10,
    border: `1px solid ${PANEL_BORDER}`,
    backgroundColor: PANEL_BACKGROUND,
    padding: 14,
    display: "flex",
    alignItems: "center",
    gap: 12,
    opacity: 0.7,
  };
  const bar: CSSProperties = {
    backgroundColor: "#1A1E2A",
    borderRadius: 4,
    height: 12,
  };
  return (
    <div
      style={style}
      data-testid="todos-skeleton-row"
      data-skeleton-index={index}
      aria-hidden="true"
    >
      <div
        style={{
          width: 18,
          height: 18,
          borderRadius: 4,
          backgroundColor: "#1A1E2A",
        }}
      />
      <div
        style={{ flex: 1, display: "flex", flexDirection: "column", gap: 6 }}
      >
        <div style={{ ...bar, width: "55%", height: 14 }} />
        <div style={{ ...bar, width: "30%" }} />
      </div>
    </div>
  );
}

function TodoRow({
  todo,
  pending,
  rowError,
  optimisticCompleted,
  onOpen,
  onToggle,
}: {
  todo: Todo;
  pending: boolean;
  rowError: string | null;
  optimisticCompleted: boolean;
  onOpen: (todo: Todo) => void;
  onToggle: (todo: Todo, nextCompleted: boolean) => void;
}): ReactElement {
  const wrapper: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 4,
  };
  const rowStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 12,
    padding: "12px 14px",
    borderRadius: 10,
    border: `1px solid ${PANEL_BORDER}`,
    backgroundColor: PANEL_BACKGROUND,
    color: TEXT_PRIMARY,
    boxSizing: "border-box",
  };
  const checkboxStyle: CSSProperties = {
    width: 18,
    height: 18,
    accentColor: ACCENT,
    cursor: pending ? "default" : "pointer",
    flexShrink: 0,
  };
  const titleButtonStyle: CSSProperties = {
    flex: 1,
    minWidth: 0,
    background: "transparent",
    border: "none",
    color: optimisticCompleted ? TEXT_SECONDARY : TEXT_PRIMARY,
    padding: 0,
    margin: 0,
    cursor: "pointer",
    textAlign: "left",
    display: "flex",
    flexDirection: "column",
    gap: 4,
    textDecoration: optimisticCompleted ? "line-through" : "none",
  };
  const titleStyle: CSSProperties = {
    fontSize: 14,
    fontWeight: 600,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const metaWrapStyle: CSSProperties = {
    display: "flex",
    gap: 8,
    fontSize: 12,
    color: TEXT_SECONDARY,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const errorStyle: CSSProperties = {
    color: DANGER,
    fontSize: 12,
    paddingLeft: 44,
  };
  const checkboxAriaLabel = optimisticCompleted
    ? `Mark ${todo.title} as open`
    : `Mark ${todo.title} as done`;
  return (
    <div
      style={wrapper}
      data-testid="todo-row"
      data-todo-id={todo.id}
      data-completed={optimisticCompleted ? "true" : "false"}
    >
      <div style={rowStyle}>
        <input
          type="checkbox"
          checked={optimisticCompleted}
          disabled={pending}
          onChange={(event) => onToggle(todo, event.target.checked)}
          style={checkboxStyle}
          aria-label={checkboxAriaLabel}
          data-testid="todo-row-toggle"
        />
        <button
          type="button"
          onClick={() => onOpen(todo)}
          style={titleButtonStyle}
          aria-label={`Open todo ${todo.title}`}
          data-testid="todo-row-open"
        >
          <span style={titleStyle}>{todo.title}</span>
          {todo.dueAt !== undefined || todo.source !== undefined ? (
            <span style={metaWrapStyle}>
              {todo.dueAt !== undefined ? (
                <span style={{ color: dueDateColor(todo.dueAt) }}>
                  {formatDueDate(todo.dueAt)}
                </span>
              ) : null}
              {todo.dueAt !== undefined && todo.source !== undefined ? (
                <span>·</span>
              ) : null}
              {todo.source !== undefined ? <span>{todo.source}</span> : null}
            </span>
          ) : null}
        </button>
      </div>
      {rowError !== null ? (
        <div
          role="alert"
          style={errorStyle}
          data-testid="todo-row-error"
          data-todo-id={todo.id}
        >
          {rowError}
        </div>
      ) : null}
    </div>
  );
}

function ErrorPanel({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}): ReactElement {
  const wrapper: CSSProperties = {
    border: `1px solid ${PANEL_BORDER}`,
    borderRadius: 12,
    backgroundColor: PANEL_BACKGROUND,
    padding: 32,
    textAlign: "center",
    color: TEXT_PRIMARY,
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: 12,
  };
  const subStyle: CSSProperties = { color: TEXT_SECONDARY, fontSize: 13 };
  const retryStyle: CSSProperties = {
    height: 32,
    padding: "0 14px",
    borderRadius: 8,
    border: `1px solid ${PANEL_BORDER_STRONG}`,
    backgroundColor: "transparent",
    color: ACCENT,
    fontSize: 13,
    fontWeight: 600,
    cursor: "pointer",
  };
  return (
    <div
      role="alert"
      style={wrapper}
      data-testid="todos-error"
      data-state="error"
    >
      <div style={{ fontSize: 14, fontWeight: 600 }}>Could not load todos</div>
      <div style={subStyle}>{message}</div>
      <button
        type="button"
        onClick={onRetry}
        style={retryStyle}
        data-testid="todos-retry"
      >
        Retry
      </button>
    </div>
  );
}

function EmptyPanel({
  title,
  hint,
}: {
  title: string;
  hint: string;
}): ReactElement {
  const wrapper: CSSProperties = {
    border: `1px dashed ${PANEL_BORDER_STRONG}`,
    borderRadius: 12,
    padding: 48,
    textAlign: "center",
    color: TEXT_PRIMARY,
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: 8,
  };
  return (
    <div role="status" style={wrapper} data-testid="todos-empty">
      <div style={{ fontSize: 16, fontWeight: 600 }}>{title}</div>
      <div style={{ color: TEXT_FAINT, fontSize: 13, maxWidth: 420 }}>
        {hint}
      </div>
    </div>
  );
}

function TabBar({
  active,
  onSelect,
}: {
  active: TodoStatusFilter;
  onSelect: (filter: TodoStatusFilter) => void;
}): ReactElement {
  const wrapper: CSSProperties = {
    display: "flex",
    gap: 4,
    borderBottom: `1px solid ${PANEL_BORDER}`,
  };
  return (
    <div role="tablist" aria-label="Todo filters" style={wrapper}>
      {FILTERS.map((descriptor) => {
        const isActive = descriptor.slug === active;
        const buttonStyle: CSSProperties = {
          height: 36,
          padding: "0 14px",
          borderRadius: 0,
          border: "none",
          background: "transparent",
          color: isActive ? TEXT_PRIMARY : TEXT_SECONDARY,
          fontSize: 13,
          fontWeight: isActive ? 600 : 500,
          cursor: "pointer",
          borderBottom: `2px solid ${isActive ? ACCENT : "transparent"}`,
          marginBottom: -1,
        };
        return (
          <button
            key={descriptor.slug}
            type="button"
            role="tab"
            aria-selected={isActive}
            aria-controls={`todos-panel-${descriptor.slug}`}
            id={`todos-tab-${descriptor.slug}`}
            data-testid={`todos-tab-${descriptor.slug}`}
            onClick={() => onSelect(descriptor.slug)}
            style={buttonStyle}
          >
            {descriptor.label}
          </button>
        );
      })}
    </div>
  );
}

interface OptimisticState {
  readonly overrides: Readonly<Record<string, boolean>>;
  readonly pending: ReadonlySet<string>;
  readonly errors: Readonly<Record<string, string>>;
}

const EMPTY_OPTIMISTIC: OptimisticState = {
  overrides: {},
  pending: new Set<string>(),
  errors: {},
};

export function TodosDestination(): ReactElement {
  const transport = useTransport();
  const router = useRouter<ArtifactRoute>();
  const [active, setActive] = useState<TodoStatusFilter>("open");
  const [state, setState] = useState<ViewState>({ kind: "loading" });
  const [reloadToken, setReloadToken] = useState(0);
  const [optimistic, setOptimistic] =
    useState<OptimisticState>(EMPTY_OPTIMISTIC);

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    setOptimistic(EMPTY_OPTIMISTIC);
    transport
      .request<TodosPayload>({
        method: "GET",
        path: "/v1/todos",
        query: { status: active },
      })
      .then((response) => {
        if (cancelled) return;
        setState({ kind: "ready", todos: response.todos });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const message =
          error instanceof Error ? error.message : "Network error";
        setState({ kind: "error", message });
      });
    return () => {
      cancelled = true;
    };
  }, [transport, active, reloadToken]);

  const handleSelectTab = useCallback((next: TodoStatusFilter) => {
    setActive(next);
  }, []);

  const handleRetry = useCallback(() => {
    setReloadToken((t) => t + 1);
  }, []);

  const handleOpen = useCallback(
    (todo: Todo) => {
      if (todo.route !== undefined) {
        router.navigate(todo.route);
        return;
      }
      router.navigate({ kind: "workspace", workspaceId: todo.id });
    },
    [router],
  );

  const handleToggle = useCallback(
    (todo: Todo, nextCompleted: boolean) => {
      const previousCompleted = todo.completed;
      setOptimistic((prev) => {
        const overrides = { ...prev.overrides, [todo.id]: nextCompleted };
        const pending = new Set(prev.pending);
        pending.add(todo.id);
        const errors = { ...prev.errors };
        delete errors[todo.id];
        return { overrides, pending, errors };
      });
      transport
        .request<unknown>({
          method: "PATCH",
          path: `/v1/todos/${todo.id}`,
          body: { completed: nextCompleted },
        })
        .then(() => {
          setOptimistic((prev) => {
            const pending = new Set(prev.pending);
            pending.delete(todo.id);
            return {
              overrides: prev.overrides,
              pending,
              errors: prev.errors,
            };
          });
        })
        .catch((error: unknown) => {
          const message =
            error instanceof Error ? error.message : "Could not update todo";
          setOptimistic((prev) => {
            const overrides = {
              ...prev.overrides,
              [todo.id]: previousCompleted,
            };
            const pending = new Set(prev.pending);
            pending.delete(todo.id);
            return {
              overrides,
              pending,
              errors: { ...prev.errors, [todo.id]: message },
            };
          });
        });
    },
    [transport],
  );

  const descriptor =
    FILTERS.find((f) => f.slug === active) ?? (FILTERS[0] as FilterDescriptor);

  const rootStyle: CSSProperties = {
    width: "100%",
    height: "100%",
    minHeight: 0,
    backgroundColor: APP_BACKGROUND,
    color: TEXT_PRIMARY,
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    overflow: "auto",
  };
  const containerStyle: CSSProperties = {
    width: "100%",
    maxWidth: 1000,
    margin: "0 auto",
    padding: "24px 28px 48px",
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    gap: 20,
  };
  const titleStyle: CSSProperties = {
    fontSize: 20,
    fontWeight: 600,
  };
  const listStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 10,
  };

  let body: ReactElement | null = null;
  if (state.kind === "loading") {
    body = (
      <div style={listStyle} data-testid="todos-list" data-state="loading">
        {Array.from({ length: SKELETON_ROW_COUNT }).map((_, i) => (
          <SkeletonRow key={i} index={i} />
        ))}
      </div>
    );
  } else if (state.kind === "error") {
    body = <ErrorPanel message={state.message} onRetry={handleRetry} />;
  } else if (state.todos.length === 0) {
    body = (
      <EmptyPanel title={descriptor.emptyTitle} hint={descriptor.emptyHint} />
    );
  } else {
    body = (
      <div style={listStyle} data-testid="todos-list" data-state="ready">
        {state.todos.map((todo) => {
          const override = optimistic.overrides[todo.id];
          const optimisticCompleted =
            override !== undefined ? override : todo.completed;
          return (
            <TodoRow
              key={todo.id}
              todo={todo}
              pending={optimistic.pending.has(todo.id)}
              rowError={optimistic.errors[todo.id] ?? null}
              optimisticCompleted={optimisticCompleted}
              onOpen={handleOpen}
              onToggle={handleToggle}
            />
          );
        })}
      </div>
    );
  }

  return (
    <section
      aria-label="Todos destination"
      data-testid="todos-destination"
      data-state={state.kind}
      data-active-status={active}
      style={rootStyle}
    >
      <div style={containerStyle}>
        <div style={titleStyle}>Todos</div>
        <TabBar active={active} onSelect={handleSelectTab} />
        <div
          role="tabpanel"
          id={`todos-panel-${active}`}
          aria-labelledby={`todos-tab-${active}`}
        >
          {body}
        </div>
      </div>
    </section>
  );
}
