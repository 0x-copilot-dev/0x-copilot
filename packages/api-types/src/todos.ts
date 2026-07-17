// Todos destination (Phase 3) — CRUD + extraction provenance + recurrence
// + one-level subtasks contract.
//
// Source: docs/atlas-new-design/destinations/todos-prd.md §4 (wire shapes),
// docs/atlas-new-design/cross-audit.md §1.1 (ItemRef link payloads),
// §1.3 (project-scoped ACL — owner writes, project-member reads, tenant
// admin compliance reads, 404-not-403 for non-readers),
// §1.5 (multi-value OR filter axes), §2.1 (branded IDs), and §9.6
// (Phase 3 deviations: recurring + subtasks IN scope, context-aware
// project default, LLM extractor uses Purpose enum).
//
// Wire-only file: no business logic, no HTTP client, no view models.
// Servers own routes; this package mirrors public payloads exactly as
// the facade serves them. Internal `/internal/v1/*` (the extraction
// pipeline) is NOT mirrored here.

import type { ProjectId, TenantId, TodoId, UserId } from "./brands";
import type { ItemRef } from "./refs";

// ---------------------------------------------------------------------------
// Primitive enums
// ---------------------------------------------------------------------------

/** Lifecycle status. Wire enum is two-state (`open`/`done`); soft-delete
 * is a server-side concept and never surfaces here — a deleted todo
 * simply disappears from list responses. */
export type TodoStatus = "open" | "done";

/** User-selected priority bucket. The composite-scoring on Home reads
 * this; the Todos surface renders priority chips off it. */
export type TodoPriority = "low" | "med" | "high";

/** Provenance discriminator on `Todo.source`. Distinct from `ItemKind` —
 * a `chat`-sourced todo provenance carries a `chat` ItemRef in `source.ref`,
 * but the todo itself is still an `ItemKind = "todo"` resource. */
export type TodoSourceKind = "user" | "chat" | "agent";

// ---------------------------------------------------------------------------
// Source provenance (cross-audit §1.1 binding)
// ---------------------------------------------------------------------------

/**
 * What created the todo. `kind: "user"` is the public-POST-only path; the
 * `chat` and `agent` variants are minted by the internal extraction
 * pipeline (`POST /internal/v1/todos/extractions/<id>/accept`) and are
 * REJECTED on the public `POST /v1/todos`.
 *
 * `ref` is the canonical cross-destination link (see refs.ts). The
 * `<ItemLink>` resolver registry handles label/icon/route on render;
 * never trust the wire payload for display state.
 */
export type TodoSource =
  | { readonly kind: "user" }
  | {
      readonly kind: "chat";
      readonly ref: ItemRef;
      readonly excerpt?: string;
    }
  | {
      readonly kind: "agent";
      readonly ref: ItemRef;
      readonly run_ref?: ItemRef;
    };

// ---------------------------------------------------------------------------
// Recurrence (impl-plan §11.1; orchestrator pulled forward to Phase 3)
// ---------------------------------------------------------------------------

/** RRULE-subset recurrence rule kinds. `every_N_days` and
 * `every_weekday` are user-friendly aliases the editor exposes;
 * `rrule` is the escape hatch for power users (RFC 5545 subset). */
export type TodoRecurrenceRule = "rrule" | "every_N_days" | "every_weekday";

/**
 * Recurrence metadata attached to the parent of a recurring series.
 * `series_id` is shared across every materialised concrete todo; the
 * materialiser worker uses `(series_id, due)` as the idempotency key
 * (see schema.sql `todo_series_dedup` unique index).
 *
 * `next_materialize_at` is server-managed — clients never set it. The
 * materialiser advances it after each successful insert.
 */
export interface TodoRecurrence {
  readonly rule: TodoRecurrenceRule;
  /** Free-form spec interpreted by `rule`. RRULE example:
   * `"FREQ=WEEKLY;BYDAY=MO,WE,FR"`. `every_N_days` example: `"3"`. */
  readonly spec: string;
  /** ISO-8601 UTC; the next instant the materialiser may insert. */
  readonly next_materialize_at: string;
  /** UUID shared across every concrete instance of the series. */
  readonly series_id: string;
}

// ---------------------------------------------------------------------------
// Canonical Todo shape
// ---------------------------------------------------------------------------

/**
 * One todo row. `project_id === null` means "Unfiled" — the
 * context-aware default (cross-audit §9.6 Q6) is computed at the
 * inline-add site, not the server.
 *
 * `parent_id` carries the one-level subtask hierarchy (impl-plan §11.2):
 *   - `parent_id` absent / null → top-level todo
 *   - `parent_id` set → subtask of the referenced todo
 *   - a subtask cannot have its own subtask (server enforces with 400).
 *
 * `sort_index_within_parent` is set only for subtasks; top-level
 * ordering uses `created_at` + future drag-reorder index (Wave 2).
 *
 * `completed_at` is set iff `status === "done"`. Flipping back to
 * `"open"` clears it.
 */
export interface Todo {
  readonly id: TodoId;
  readonly tenant_id: TenantId;
  readonly owner_user_id: UserId;
  readonly project_id: ProjectId | null;
  readonly text: string;
  readonly status: TodoStatus;
  readonly priority: TodoPriority;
  /** ISO-8601 due date. Server stores as `DATE`; client renders in
   * the user's local timezone.
   *
   * `null` on undated todos — the server always emits the key and sets
   * it to `null` rather than omitting it, so consumers must narrow with
   * `== null` (or an explicit `=== null` arm), not `=== undefined`.
   * Declaring this `string | undefined` is what let a `null` reach
   * `due.split("-")` and crash the whole Todos destination. */
  readonly due?: string | null;
  readonly source: TodoSource;
  readonly parent_id?: TodoId | null;
  readonly sort_index_within_parent?: number | null;
  readonly recurrence?: TodoRecurrence | null;
  readonly created_at: string;
  readonly updated_at: string;
  readonly completed_at?: string | null;
}

// ---------------------------------------------------------------------------
// List / mutation payloads
// ---------------------------------------------------------------------------

/**
 * Cursor-paginated list response. `next_cursor` is opaque (base64 of
 * `(sort_field, id)`); the client passes it back verbatim. Absent means
 * "no more pages".
 */
export interface TodoListResponse {
  readonly items: ReadonlyArray<Todo>;
  readonly next_cursor?: string;
}

/**
 * Public POST body. `source` is rejected on the public path — that
 * field is reserved for the internal extraction-accept pipeline (see
 * `/internal/v1/todos/extractions/<id>/accept`). Public callers create
 * `source: { kind: "user" }` todos only.
 *
 * `parent_id` opts the new row into one-level subtask nesting; the
 * server rejects with 400 if the referenced todo is itself a subtask.
 * Subtask `project_id` is inherited from the parent (server-enforced).
 */
export interface CreateTodoRequest {
  readonly text: string;
  readonly priority?: TodoPriority;
  readonly due?: string;
  readonly project_id?: ProjectId | null;
  readonly parent_id?: TodoId;
  readonly recurrence?: Omit<
    TodoRecurrence,
    "series_id" | "next_materialize_at"
  >;
}

/**
 * PATCH body. Every field is optional; `status` toggles transition the
 * `completed_at` field on the server. `project_id: null` explicitly
 * unfiles (vs omitting the field, which leaves it as-is).
 */
export interface UpdateTodoRequest {
  readonly text?: string;
  readonly status?: TodoStatus;
  readonly priority?: TodoPriority;
  readonly due?: string | null;
  readonly project_id?: ProjectId | null;
  readonly sort_index_within_parent?: number;
  readonly recurrence?: Omit<
    TodoRecurrence,
    "series_id" | "next_materialize_at"
  > | null;
}

/** Permitted bulk-action verbs. Each bulk write produces one audit row
 * per affected todo, all sharing the request's `correlation_id` (PRD §6
 * + cross-audit §1.4). */
export type BulkTodoAction =
  | "mark_done"
  | "mark_open"
  | "delete"
  | "set_priority"
  | "set_project";

/**
 * Bulk-mutation body. The optional `payload` shape depends on `action`:
 *   - `set_priority` → `{ priority: TodoPriority }`
 *   - `set_project`  → `{ project_id: ProjectId | null }`
 *   - others         → omit.
 *
 * `correlation_id` is client-minted (uuid v4 typical); the server
 * stores it on every audit row written by the bulk to make the rows
 * queryable as a unit.
 */
export interface BulkUpdateTodosRequest {
  readonly action: BulkTodoAction;
  readonly ids: ReadonlyArray<TodoId>;
  readonly correlation_id: string;
  readonly payload?: {
    readonly priority?: TodoPriority;
    readonly project_id?: ProjectId | null;
  };
}

/** Bulk response. `affected` counts rows the server actually mutated
 * (ids the caller has no write access to are silently dropped — see
 * cross-audit §1.3 "non-readers get 404"; here non-writers are dropped
 * because the bulk is best-effort). */
export interface BulkUpdateTodosResponse {
  readonly affected: number;
  readonly correlation_id: string;
}
