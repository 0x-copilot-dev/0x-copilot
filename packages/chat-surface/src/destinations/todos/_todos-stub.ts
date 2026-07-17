// chat-surface Todos adapter shape (transitional; orchestrator rewires
// at merge to `@0x-copilot/api-types/todos`).
//
// Phase 3 has four parallel wave-agents working off slightly different
// shape conventions:
//   - P3-A1 (wire) owns the canonical `packages/api-types/src/todos.ts`.
//   - P3-A2/A3 own the extractor + recurrence materializer.
//   - P3-B1 (this shell), P3-B2 (inline-add + extraction banner), P3-B3
//     (recurrence editor + subtask tree) ship UI in chat-surface.
//   - P3-C wires apps/frontend to the canonical wire.
//
// Until P3-A1 lands, this stub is the local view-model contract every
// UI sub-agent consumes. The shape mirrors todos-prd.md §3 (data shape)
// + §4 (wire) + implementation-plan §11.1 (recurrence) + §11.2 (subtasks)
// + §13 (sections rendering).
//
// Every import of this stub should be marked
// `TODO(merge): rewire to "@0x-copilot/api-types"` so the
// orchestrator's rewrite script can find them.

import type {
  AgentId,
  ItemRef,
  ProjectId,
  RunId,
  TodoExtractionId,
  TodoId,
  TodoSeriesId,
} from "@0x-copilot/api-types";

// Re-exports — let `import { TodoSeriesId } from "../_todos-stub"` keep
// working without an island-wide churn pass. Canonical site is api-types.
export type { TodoExtractionId, TodoSeriesId };

// ---- §4.1 Primitive enums --------------------------------------------------

/** Todo priority. Source: todos-prd §4.1. */
export type TodoPriority = "low" | "med" | "high";

/** Provenance of a todo. Source: todos-prd §4.1. */
export type TodoSource =
  | { readonly kind: "user" }
  | {
      readonly kind: "chat";
      readonly thread_id: string;
      readonly excerpt?: string;
    }
  | {
      readonly kind: "agent";
      readonly agent_id: AgentId;
      readonly run_id?: RunId;
      readonly excerpt?: string;
    };

// ---- §11.1 Recurrence -----------------------------------------------------

/** Recurrence rule attached to a *parent* todo (top-level only — recurring
 *  subtasks are out of scope per implementation-plan §11.2). */
export interface TodoRecurrence {
  readonly rule: "rrule" | "every_N_days" | "every_weekday";
  readonly spec: string; // e.g. "FREQ=WEEKLY;BYDAY=MO,WE,FR" or "every_N_days:3"
  readonly next_materialize_at: string; // ISO instant
  readonly series_id: TodoSeriesId;
}

// ---- §3 Todo row + tree ---------------------------------------------------

/**
 * Single todo row. Section bucketing happens client-side (per
 * implementation-plan §9.6 cross-audit decision + todos-prd §13):
 *   - Overdue   : !done && due < start_of_today
 *   - Today     : !done && due ∈ [start_of_today, end_of_today]
 *   - This week : !done && due ∈ (end_of_today, end_of_week]
 *   - Upcoming  : !done && due > end_of_week (PRD §3.2 calls this "Later")
 *   - No due    : !done && due IS NULL
 *   - Done      : done && completed_at >= now - 14d
 *
 * The destination shell bucket-routes; the row component is bucket-agnostic.
 */
export interface Todo {
  readonly id: TodoId;
  readonly text: string;
  readonly done: boolean;
  readonly completed_at?: string;
  readonly due?: string; // ISO date (no time) — server interprets in user tz.
  readonly priority: TodoPriority;
  readonly source: TodoSource;
  readonly project_id?: ProjectId;
  readonly labels: ReadonlyArray<string>;
  readonly sort_index: number;
  readonly created_at: string;
  readonly updated_at: string;
  /** Recurrence (parent rows only). Subtasks never carry recurrence. */
  readonly recurrence?: TodoRecurrence;
  /** Parent for subtasks (one level only — implementation-plan §11.2). */
  readonly parent_id?: TodoId;
  readonly sort_index_within_parent?: number;
}

// ---- §3.7 Extractions ------------------------------------------------------

export interface TodoExtractionProposal {
  readonly text: string;
  readonly priority: TodoPriority;
  readonly due?: string;
  readonly excerpt?: string;
}

export interface TodoExtraction {
  readonly id: TodoExtractionId;
  readonly source: {
    readonly thread_id: string;
    readonly run_id: RunId;
  };
  readonly proposed_todos: ReadonlyArray<TodoExtractionProposal>;
  readonly status: "pending" | "accepted" | "rejected" | "snoozed";
  readonly snoozed_until?: string;
  readonly created_at: string;
}

// ---- §13 Section keys -----------------------------------------------------

/**
 * Stable bucket keys used for section ordering, render-state tests, and
 * telemetry. Order here is the render order (Overdue first so users
 * can't miss it — todos-prd §3.2).
 */
export type TodoSectionKey =
  | "overdue"
  | "today"
  | "this_week"
  | "upcoming"
  | "no_due"
  | "done";

// ---- §4.2 Top-level UI payload -------------------------------------------

/**
 * The shell's input. Apps/frontend (P3-C) fetches the list + extractions
 * and adapts the wire shape to this stub. When `null`, the shell renders
 * the skeleton.
 */
export interface TodosPayload {
  readonly todos: ReadonlyArray<Todo>;
  readonly extractions?: ReadonlyArray<TodoExtraction>;
  readonly cached_at?: string;
}

// ---- §13.1 Outbound ItemRef helpers --------------------------------------

/**
 * Project a todo into the canonical ItemRef so other destinations can
 * resolve back to it via the registry (todos-prd §13.1 inbound). The
 * destination's `index.ts` registers a resolver for kind `"todo"` so
 * `<ItemLink kind="todo" id=…>` renders from anywhere.
 */
export function todoItemRef(todo: Todo): ItemRef {
  return { kind: "todo", id: todo.id };
}
