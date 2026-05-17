// Local stub for the Phase 3 Todos wire contract.
//
// The canonical types live in `@enterprise-search/api-types`
// (`packages/api-types/src/todos.ts`), authored by the parallel
// Phase 3 Impl-A backend-types agent. This frontend wave (P3-C) runs
// in parallel against the same sub-PRD spec and cannot import a type
// that is not yet on `main`, so this stub mirrors the shapes in
// `docs/atlas-new-design/destinations/todos-prd.md` §4.1 (`Todo`) +
// §4.1 (`TodoExtraction`) and §4.3 (mutation requests / responses).
//
// `TodoId`, `TodoExtractionId`, and the cross-destination `ItemKind`
// branding already live in api-types — re-export from there so the
// `<ItemLink kind="todo" id={...} />` registry stays a single source
// of truth even before the rest of the Todos contract merges.
//
// TODO(merge): delete this file. Replace every `_todos-stub` import
// with `@enterprise-search/api-types` once Impl-A's
// `packages/api-types/src/todos.ts` lands on main.

import type {
  ConversationId,
  RunId,
  TodoId,
} from "@enterprise-search/api-types";

export type { TodoId };

// `TodoExtractionId` is not yet branded in api-types (Impl-A adds it).
// Until then, model it as a branded alias here so the destination /
// route can carry the same shape post-merge by a single import swap.
export type TodoExtractionId = string & {
  readonly __brand: "TodoExtractionId";
};

export type TodoPriority = "low" | "med" | "high";

/**
 * `source.kind` discriminator. Mirrors sub-PRD §4.1.
 *
 * - `user`     — typed directly by the owner (`POST /v1/todos` happy path).
 * - `chat`     — extracted from a chat transcript; `thread_id` retained.
 * - `agent`    — extracted from an agent run; `agent_id` + optional `run_id`.
 *
 * The public `POST /v1/todos` REJECTS non-`user` source per sub-PRD §4.3;
 * the extractor sets `chat` / `agent` via the internal accept path.
 */
export type TodoSource =
  | { readonly kind: "user" }
  | {
      readonly kind: "chat";
      readonly thread_id: ConversationId;
      readonly excerpt?: string;
    }
  | {
      readonly kind: "agent";
      readonly agent_id: string;
      readonly run_id?: RunId;
      readonly excerpt?: string;
    };

/** Canonical Todo record returned by `GET /v1/todos` and the CRUD mutations. */
export interface Todo {
  readonly id: TodoId;
  readonly tenant_id: string;
  readonly owner_user_id: string;
  readonly text: string;
  readonly done: boolean;
  /** Set iff `done` flipped true. */
  readonly completed_at?: string;
  /** ISO date (no time component); user-tz interpreted server-side. */
  readonly due?: string;
  readonly priority: TodoPriority;
  readonly source: TodoSource;
  readonly project_id?: string;
  readonly labels: ReadonlyArray<string>;
  /** Float between bucket neighbours; server-managed. */
  readonly sort_index: number;
  readonly created_at: string;
  readonly updated_at: string;
}

/** Proposed-todo candidate inside a `TodoExtraction`. Sub-PRD §4.1. */
export interface ProposedTodo {
  readonly text: string;
  readonly priority: TodoPriority;
  readonly due?: string;
  readonly excerpt?: string;
}

/** Pending / resolved auto-extraction surfaced via the banner. */
export interface TodoExtraction {
  readonly id: TodoExtractionId;
  readonly tenant_id: string;
  readonly owner_user_id: string;
  readonly source: {
    readonly thread_id: ConversationId;
    readonly run_id: RunId;
  };
  readonly proposed_todos: ReadonlyArray<ProposedTodo>;
  readonly status: "pending" | "accepted" | "rejected" | "snoozed";
  readonly snoozed_until?: string;
  readonly created_at: string;
}

// ===========================================================================
// List + filter (sub-PRD §4.2)
// ===========================================================================

export type TodoSortKey =
  | "due:asc"
  | "due:desc"
  | "priority:desc"
  | "priority:asc"
  | "created_at:desc"
  | "created_at:asc"
  | "updated_at:desc";

export interface ListTodosFilters {
  readonly done?: boolean;
  readonly priority?: ReadonlyArray<TodoPriority>;
  /** "unfiled" matches NULL `project_id`. Multiple values OR'd. */
  readonly project_id?: ReadonlyArray<string>;
  readonly source?: ReadonlyArray<TodoSource["kind"]>;
}

export interface ListTodosResponse {
  readonly items: ReadonlyArray<Todo>;
  readonly next_cursor?: string;
  /** Present only when no filter narrows the result (sub-PRD §4.2). */
  readonly total?: number;
}

export interface ListExtractionsResponse {
  readonly items: ReadonlyArray<TodoExtraction>;
  readonly next_cursor?: string;
}

// ===========================================================================
// Mutations (sub-PRD §4.3)
// ===========================================================================

/**
 * Create payload. Server defaults `priority: "med"` and `source: { kind: "user" }`.
 * Public surface REJECTS non-user source per sub-PRD §4.3 — the extraction
 * accept path uses the internal `/internal/v1/...` endpoint instead.
 */
export interface CreateTodoRequest {
  readonly text: string;
  readonly priority?: TodoPriority;
  readonly due?: string;
  readonly project_id?: string;
  readonly labels?: ReadonlyArray<string>;
}

export interface UpdateTodoRequest {
  readonly text?: string;
  readonly done?: boolean;
  readonly priority?: TodoPriority;
  readonly due?: string | null;
  readonly labels?: ReadonlyArray<string>;
  readonly project_id?: string | null;
  readonly sort_index?: number;
}

export type BulkTodoAction =
  | { readonly action: "mark_done"; readonly ids: ReadonlyArray<TodoId> }
  | { readonly action: "mark_undone"; readonly ids: ReadonlyArray<TodoId> }
  | { readonly action: "delete"; readonly ids: ReadonlyArray<TodoId> }
  | {
      readonly action: "set_priority";
      readonly ids: ReadonlyArray<TodoId>;
      readonly payload: { readonly priority: TodoPriority };
    }
  | {
      readonly action: "set_project";
      readonly ids: ReadonlyArray<TodoId>;
      readonly payload: { readonly project_id: string | null };
    };

export interface BulkTodoResponse {
  readonly affected: number;
  readonly correlation_id: string;
}

export interface AcceptExtractionRequest {
  readonly accepted_indices: ReadonlyArray<number>;
}

export interface AcceptExtractionResponse {
  readonly todos: ReadonlyArray<Todo>;
}

export interface SnoozeExtractionRequest {
  /** ISO timestamp. */
  readonly until: string;
}
