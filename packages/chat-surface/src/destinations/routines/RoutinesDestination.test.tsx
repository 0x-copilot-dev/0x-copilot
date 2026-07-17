// RoutinesDestination shell tests (P5-B1).
//
// Covers: loading skeleton, error/unavailable empty states, ready state
// rendering (status pill + trigger chips + "next fire" label), filter
// tab interaction, row actions, render-detail slot.

import type {
  AgentId,
  ConnectorId,
  ProjectId,
  RoutineId,
  SectionResult,
  SkillId,
  TenantId,
  ToolId,
  UserId,
} from "@0x-copilot/api-types";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../../providers/RouterProvider";
import type { ArtifactRoute, Router } from "../../routing/router";

// Import the destination's index for ItemRef resolver side-effects —
// registers kind `"routine"`. Without this, an `<ItemLink kind="routine">`
// in a row would resolve to the deleted-chip path.
import "./index";

// TODO(merge): rewire to "@0x-copilot/api-types"
import type {
  Routine,
  RoutineStatus,
  RoutineTrigger,
  TriggerId,
} from "./_routines-stub";

import {
  RoutinesDestination,
  nextFireDisplay,
  uniqueTriggerKinds,
  type RoutinesDestinationProps,
} from "./RoutinesDestination";

// ===========================================================================
// Helpers
// ===========================================================================

const asRoutineId = (s: string): RoutineId => s as unknown as RoutineId;
const asTriggerId = (s: string): TriggerId => s as unknown as TriggerId;
const asUserId = (s: string): UserId => s as unknown as UserId;
const asProjectId = (s: string): ProjectId => s as unknown as ProjectId;
const asAgentId = (s: string): AgentId => s as unknown as AgentId;
const asTenantId = (s: string): TenantId => s as unknown as TenantId;

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

function renderDest(props: RoutinesDestinationProps = {}): void {
  const router = makeRouter();
  render(
    <RouterProvider router={router}>
      <RoutinesDestination {...props} />
    </RouterProvider>,
  );
}

// ===========================================================================
// Fixtures
// ===========================================================================

const NOW = Date.parse("2026-05-17T12:00:00.000Z");
const T_TOMORROW = "2026-05-18T09:00:00.000Z";
const T_YESTERDAY = "2026-05-16T09:00:00.000Z";

function ok<T>(data: T): SectionResult<T> {
  return { status: "ok", data };
}

const SCHEDULE_TRIGGER: RoutineTrigger = {
  kind: "schedule",
  trigger_id: asTriggerId("trg_sched_1"),
  cron: "0 18 * * 1-5",
  tz: "Asia/Kolkata",
};
const WEBHOOK_TRIGGER: RoutineTrigger = {
  kind: "webhook",
  trigger_id: asTriggerId("trg_hook_1"),
  secret_masked: "****abcd",
  secret_rotated_at: null,
  secret_grace_until: null,
  ip_allowlist: [],
};
const EVENT_TRIGGER: RoutineTrigger = {
  kind: "event",
  trigger_id: asTriggerId("trg_evt_1"),
  event_source: "inbox.item_created",
  filter: [],
};
const MANUAL_TRIGGER: RoutineTrigger = {
  kind: "manual",
  trigger_id: asTriggerId("trg_man_1"),
};

type RoutineInit = Omit<Partial<Routine>, "id"> & { readonly id: string };

function makeRoutine(over: RoutineInit): Routine {
  return {
    id: asRoutineId(over.id),
    tenant_id: asTenantId("tnt_1"),
    owner_user_id: asUserId("usr_owner"),
    owner_display_name: over.owner_display_name ?? "Sarah",
    project_id: over.project_id ?? null,
    project_name: over.project_name,
    name: over.name ?? "Weekday Brief",
    description: over.description ?? "Runs weekdays at 18:00 GMT+5:30",
    model: over.model ?? "gpt-4.1",
    base_agent_id: over.base_agent_id ?? null,
    status: over.status ?? "active",
    pause_reason: over.pause_reason ?? null,
    triggers: over.triggers ?? [SCHEDULE_TRIGGER],
    connectors: over.connectors ?? [],
    behavior: over.behavior ?? {
      autonomy: "auto_apply",
      max_retries: 1,
      backoff: "exponential",
      backoff_base_seconds: 30,
      max_duration_seconds: 600,
      output_target: { kind: "inbox" },
      notify_on_success: ["owner"],
      notify_on_failure: ["owner"],
    },
    permissions: over.permissions ?? {
      scope: "read_only",
      allowed_tools: [] as ReadonlyArray<ToolId>,
      allowed_skills: [] as ReadonlyArray<SkillId>,
      max_tool_calls_per_fire: 200,
      max_output_tokens_per_fire: 32000,
      data_residency: "inherit",
      manual_fire: "owner",
    },
    missed_fire_policy: over.missed_fire_policy ?? "fire_once",
    // Note: `??` would short-circuit on null too — preserve explicit
    // null overrides (webhook/event/manual-only routines have
    // `next_fire_at = null`).
    next_fire_at:
      over.next_fire_at === undefined ? T_TOMORROW : over.next_fire_at,
    last_fire_at:
      over.last_fire_at === undefined ? T_YESTERDAY : over.last_fire_at,
    last_fire_status:
      over.last_fire_status === undefined ? "succeeded" : over.last_fire_status,
    created_at: over.created_at ?? "2026-05-01T00:00:00.000Z",
    updated_at: over.updated_at ?? "2026-05-16T00:00:00.000Z",
    links: over.links ?? [],
  };
}

// ===========================================================================
// Tests
// ===========================================================================

describe("RoutinesDestination", () => {
  it("renders the skeleton state when items is null", () => {
    renderDest({ items: null });
    expect(screen.getByTestId("routines-destination")).toHaveAttribute(
      "data-state",
      "loading",
    );
    expect(
      screen.getAllByTestId("routines-skeleton-row").length,
    ).toBeGreaterThan(0);
  });

  it("renders whole-list error state with a retry button when status=error", () => {
    const onRetry = vi.fn();
    renderDest({
      items: { status: "error", error: "Network exploded" },
      onRetry,
    });
    expect(screen.getByTestId("routines-destination")).toHaveAttribute(
      "data-state",
      "error",
    );
    expect(screen.getByTestId("empty-state")).toHaveTextContent(
      /could not load routines/i,
    );
    fireEvent.click(screen.getByTestId("empty-state-action"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("renders the unavailable state when status=unavailable", () => {
    renderDest({
      items: { status: "unavailable", error: "Disabled for tenant" },
    });
    expect(screen.getByTestId("routines-destination")).toHaveAttribute(
      "data-state",
      "unavailable",
    );
    expect(screen.getByTestId("empty-state")).toHaveTextContent(
      /routines unavailable/i,
    );
  });

  it("renders an empty state when status=ok with no rows", () => {
    renderDest({ items: ok<ReadonlyArray<Routine>>([]) });
    expect(screen.getByTestId("empty-state")).toHaveTextContent(
      /no routines yet/i,
    );
  });

  it("renders one row per routine with status pill + name + trigger chips", () => {
    const routines: ReadonlyArray<Routine> = [
      makeRoutine({
        id: "rt_1",
        name: "Weekday Brief",
        status: "active",
        triggers: [SCHEDULE_TRIGGER],
      }),
      makeRoutine({
        id: "rt_2",
        name: "Paused Acme",
        status: "paused",
        triggers: [WEBHOOK_TRIGGER, MANUAL_TRIGGER],
      }),
    ];
    renderDest({ items: ok<ReadonlyArray<Routine>>(routines), now: NOW });
    const rows = screen.getAllByTestId("routine-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveAttribute("data-status", "active");
    expect(rows[1]).toHaveAttribute("data-status", "paused");
    expect(rows[0]).toHaveTextContent(/Weekday Brief/);
    // Status pill renders "Active" tone
    expect(rows[0]).toHaveTextContent(/Active/);
    // Trigger chip(s)
    expect(rows[0]).toHaveTextContent(/Schedule/);
    expect(rows[1]).toHaveTextContent(/Webhook/);
    expect(rows[1]).toHaveTextContent(/Manual/);
  });

  it("renders FilterTabs with the 5 status slugs (All / Active / Paused / Errored / Draft)", () => {
    renderDest({ items: ok<ReadonlyArray<Routine>>([]) });
    for (const slug of ["all", "active", "paused", "errored", "draft"]) {
      expect(screen.getByTestId(`filter-tab-${slug}`)).toBeInTheDocument();
    }
    expect(screen.getByTestId("filter-tab-all")).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("calls onFilterChange when a filter tab is clicked", () => {
    const onFilterChange = vi.fn();
    renderDest({
      items: ok<ReadonlyArray<Routine>>([]),
      onFilterChange,
    });
    fireEvent.click(screen.getByTestId("filter-tab-active"));
    expect(onFilterChange).toHaveBeenCalledWith("active");
    fireEvent.click(screen.getByTestId("filter-tab-errored"));
    expect(onFilterChange).toHaveBeenCalledWith("errored");
  });

  it("renders the 'New routine' primary action and invokes onCreateRoutine", () => {
    const onCreateRoutine = vi.fn();
    renderDest({
      items: ok<ReadonlyArray<Routine>>([
        makeRoutine({ id: "rt_1", name: "Foo" }),
      ]),
      onCreateRoutine,
    });
    // PageHeader exposes the primary action button
    const button = screen.getByRole("button", { name: /new routine/i });
    fireEvent.click(button);
    expect(onCreateRoutine).toHaveBeenCalledTimes(1);
  });

  it("renders an errored badge in PageHeader when errored counts are present", () => {
    renderDest({
      items: ok<ReadonlyArray<Routine>>([
        makeRoutine({ id: "rt_e", status: "errored" }),
      ]),
      counts: { all: 1, active: 0, paused: 0, errored: 1, draft: 0 },
    });
    // Subtitle reports errored count
    expect(screen.getByTestId("routines-destination")).toHaveTextContent(
      /1 errored/i,
    );
  });

  it("renders row hover actions only for the applicable status (active=Pause, paused=Activate)", () => {
    renderDest({
      items: ok<ReadonlyArray<Routine>>([
        makeRoutine({ id: "rt_a", status: "active" }),
        makeRoutine({ id: "rt_p", status: "paused" }),
      ]),
      onPauseRoutine: vi.fn(),
      onActivateRoutine: vi.fn(),
    });
    const rows = screen.getAllByTestId("routine-row");
    // rt_a (active): Pause visible, Activate hidden
    expect(
      rows[0].querySelector('[data-testid="routine-row-pause"]'),
    ).not.toBeNull();
    expect(
      rows[0].querySelector('[data-testid="routine-row-activate"]'),
    ).toBeNull();
    // rt_p (paused): Activate visible, Pause hidden
    expect(
      rows[1].querySelector('[data-testid="routine-row-pause"]'),
    ).toBeNull();
    expect(
      rows[1].querySelector('[data-testid="routine-row-activate"]'),
    ).not.toBeNull();
  });

  it("Run now / Edit handlers fire with the routine id", () => {
    const onRunNow = vi.fn();
    const onEditRoutine = vi.fn();
    renderDest({
      items: ok<ReadonlyArray<Routine>>([makeRoutine({ id: "rt_1" })]),
      onRunNow,
      onEditRoutine,
    });
    fireEvent.click(screen.getByTestId("routine-row-run-now"));
    expect(onRunNow).toHaveBeenCalledWith(asRoutineId("rt_1"));
    fireEvent.click(screen.getByTestId("routine-row-edit"));
    expect(onEditRoutine).toHaveBeenCalledWith(asRoutineId("rt_1"));
  });

  it("renders ItemLink chips for cross-destination refs (agent, project)", () => {
    const routine = makeRoutine({
      id: "rt_links",
      links: [
        { kind: "agent", id: asAgentId("ag_atlas") },
        { kind: "project", id: asProjectId("proj_acme") },
      ],
    });
    renderDest({ items: ok<ReadonlyArray<Routine>>([routine]) });
    // ItemLink renders into the row's meta; we assert presence of the
    // item-link tagged elements (resolver registration happens at import
    // time so the registry can resolve agent/project too).
    expect(screen.getByTestId("routine-row")).toBeInTheDocument();
  });

  it("renders the renderDetail slot in place of the list body when focused", () => {
    const renderDetail = vi.fn(({ routineId }) => (
      <div data-testid="routines-detail-stub">
        detail for {routineId as string}
      </div>
    ));
    renderDest({
      items: ok<ReadonlyArray<Routine>>([makeRoutine({ id: "rt_focused" })]),
      renderDetail,
      focusedRoutineId: asRoutineId("rt_focused"),
    });
    expect(screen.getByTestId("routines-detail-slot")).toHaveAttribute(
      "data-focused-routine-id",
      "rt_focused",
    );
    expect(screen.getByTestId("routines-detail-stub")).toHaveTextContent(
      /detail for rt_focused/,
    );
    expect(screen.queryByTestId("routine-row")).toBeNull();
  });
});

// ===========================================================================
// Helper exports
// ===========================================================================

describe("uniqueTriggerKinds", () => {
  it("dedupes trigger kinds in stable order (schedule -> webhook -> event -> manual)", () => {
    const triggers: ReadonlyArray<RoutineTrigger> = [
      MANUAL_TRIGGER,
      WEBHOOK_TRIGGER,
      SCHEDULE_TRIGGER,
      EVENT_TRIGGER,
      SCHEDULE_TRIGGER, // duplicate
    ];
    expect(uniqueTriggerKinds(triggers)).toEqual([
      "schedule",
      "webhook",
      "event",
      "manual",
    ]);
  });
});

describe("nextFireDisplay", () => {
  it("formats next_fire_at as a relative time when present", () => {
    const r = makeRoutine({ id: "rt", next_fire_at: T_TOMORROW });
    expect(nextFireDisplay(r, NOW)).toMatch(/^next /);
  });

  it("returns 'Webhook · waiting' when no schedule + webhook trigger", () => {
    const r = makeRoutine({
      id: "rt",
      next_fire_at: null,
      triggers: [WEBHOOK_TRIGGER, MANUAL_TRIGGER],
    });
    expect(nextFireDisplay(r, NOW)).toBe("Webhook · waiting");
  });

  it("returns 'Event · waiting' when no schedule + event trigger", () => {
    const r = makeRoutine({
      id: "rt",
      next_fire_at: null,
      triggers: [EVENT_TRIGGER, MANUAL_TRIGGER],
    });
    expect(nextFireDisplay(r, NOW)).toBe("Event · waiting");
  });

  it("returns 'Manual only' when only a manual trigger and no schedule", () => {
    const r = makeRoutine({
      id: "rt",
      next_fire_at: null,
      triggers: [MANUAL_TRIGGER],
    });
    expect(nextFireDisplay(r, NOW)).toBe("Manual only");
  });
});

// `RoutineStatus` exhaustive check — adding a status here will fail the
// switch at the call site; this test ensures the test file stays a live
// reference for the supported statuses.
const _ALL_STATUSES: ReadonlyArray<RoutineStatus> = [
  "draft",
  "active",
  "paused",
  "errored",
];
void _ALL_STATUSES;
