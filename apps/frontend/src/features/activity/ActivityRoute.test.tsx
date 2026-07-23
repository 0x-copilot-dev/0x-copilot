// ActivityRoute — host-binder integration tests (PR-4.6 · PRD-08 D1/D1c).
//
// Covers PRD §8: run-history rows render grouped by the component
// (FR-4.14/4.19), a running row → onOpenRun (FR-4.16), the retention link
// → onOpenRetentionSettings (FR-4.17), and the error / empty states (FR-4.2),
// plus the shared projection (`projectActivityRows`) + status fold
// (`mapRunStatus`) and the meta composer.
//
// PRD-08: the "transport" is mocked at the ONE run-history endpoint
// (`listRunHistory` → GET /v1/agent/runs). The REAL `activityApi` composition
// and the REAL `<ActivityDestination>` run, so the test exercises projection +
// meta + in-shell day grouping end-to-end. The audit endpoint is mocked ONLY to
// assert it is never called (DoD 12 — the swallowed-403 regression guard).

import type {
  ListAuditEventsResponse,
  RunHistoryEntry,
  RunHistoryResponse,
  RunId,
} from "@0x-copilot/api-types";
import {
  RouterProvider,
  type ArtifactRoute,
  type Router,
} from "@0x-copilot/chat-surface";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type Mock,
} from "vitest";

// --- Mock the ONE run-history endpoint (the "transport") ------------------
const apiMocks = vi.hoisted(() => ({
  listRunHistory: vi.fn(),
  listAuditEvents: vi.fn(),
}));
vi.mock("../../api/agentApi", async () => {
  const actual =
    await vi.importActual<typeof import("../../api/agentApi")>(
      "../../api/agentApi",
    );
  return { ...actual, listRunHistory: apiMocks.listRunHistory };
});
// Mocked only so DoD 12 can prove it is NEVER called — Activity no longer reads
// the audit stream.
vi.mock("../../api/auditApi", async () => {
  const actual =
    await vi.importActual<typeof import("../../api/auditApi")>(
      "../../api/auditApi",
    );
  return { ...actual, listAuditEvents: apiMocks.listAuditEvents };
});

// Imports below resolve through the mocks above.
import {
  ACTIVITY_RUN_STATUSES,
  AGENT_RUN_STATUSES,
} from "@0x-copilot/api-types";
import { ActivityRoute } from "./ActivityRoute";
import { mapRunStatus, projectActivityRows } from "./api/activityApi";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

// --- Timezone-robust fixtures ---------------------------------------------
const NOW = new Date(2026, 6, 18, 12, 0, 0).getTime();
const TODAY_ISO = new Date(2026, 6, 18, 9, 0, 0).toISOString();
const TODAY_LATER_ISO = new Date(2026, 6, 18, 10, 30, 0).toISOString();
const YESTERDAY_ISO = new Date(2026, 6, 17, 15, 0, 0).toISOString();

function entry(over: Partial<RunHistoryEntry> = {}): RunHistoryEntry {
  return {
    run_id: "run_default",
    conversation_id: "conv_default",
    conversation_title: "Sync my inbox",
    status: "running",
    model_name: "gpt-4o",
    created_at: TODAY_ISO,
    started_at: TODAY_ISO,
    completed_at: null,
    cancelled_at: null,
    connector_count: null,
    step_count: null,
    pending_approval_count: 0,
    ...over,
  };
}

function runList(entries: readonly RunHistoryEntry[]): RunHistoryResponse {
  return { runs: [...entries], next_cursor: null, has_more: false };
}

function auditList(): ListAuditEventsResponse {
  return { rows: [], next_cursor: null, has_more: false, degraded_streams: [] };
}

// --- Router scaffolding ---------------------------------------------------
const navigate = vi.fn();
const testRouter: Router<ArtifactRoute> = {
  current: () => ({ kind: "chat", conversationId: "x" }) as ArtifactRoute,
  navigate,
  subscribe: () => () => undefined,
};

function renderRoute(
  props: Partial<Parameters<typeof ActivityRoute>[0]> = {},
): {
  readonly onOpenRun: Mock;
  readonly onOpenRetentionSettings: Mock;
} {
  const onOpenRun = vi.fn();
  const onOpenRetentionSettings = vi.fn();
  render(
    <RouterProvider router={testRouter}>
      <ActivityRoute
        identity={IDENTITY}
        onOpenRun={onOpenRun}
        onOpenRetentionSettings={onOpenRetentionSettings}
        {...props}
      />
    </RouterProvider>,
  );
  return { onOpenRun, onOpenRetentionSettings };
}

beforeEach(() => {
  apiMocks.listRunHistory.mockReset();
  apiMocks.listAuditEvents.mockReset();
  apiMocks.listAuditEvents.mockResolvedValue(auditList());
  navigate.mockClear();
  vi.spyOn(Date, "now").mockReturnValue(NOW);
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ===========================================================================
// Pure composition — projectActivityRows + mapRunStatus (FR-4.15/4.18/4.19)
// ===========================================================================

describe("mapRunStatus", () => {
  it("folds waiting_for_approval into needs_input (Inbox fold, FR-4.18)", () => {
    expect(mapRunStatus("waiting_for_approval")).toBe("needs_input");
  });

  it("is total over AGENT_RUN_STATUSES (PRD-05 DoD 16)", () => {
    for (const status of AGENT_RUN_STATUSES) {
      const mapped = mapRunStatus(status);
      expect(mapped).not.toBeUndefined();
      expect(ACTIVITY_RUN_STATUSES.includes(mapped)).toBe(true);
    }
  });
});

describe("projectActivityRows", () => {
  it("projects the run-history entry, composing the meta line from the counters", () => {
    const rows = projectActivityRows([
      entry({
        run_id: "run_a",
        conversation_id: "conv_a",
        conversation_title: "Weekly digest",
        status: "waiting_for_approval",
        connector_count: 4,
        step_count: 7,
        pending_approval_count: 1,
        started_at: TODAY_ISO,
      }),
    ]);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      run_id: "run_a",
      title: "Weekly digest",
      status: "needs_input",
      meta: "4 apps · 7 steps · awaiting 1 approval",
      started_at: TODAY_ISO,
    });
  });

  it("falls back to a default title and an empty meta when counters are unknown", () => {
    const rows = projectActivityRows([
      entry({ run_id: "run_x", conversation_title: "   " }),
    ]);
    expect(rows[0]!.title).toBe("Untitled run");
    expect(rows[0]!.meta).toBe("");
  });
});

// ===========================================================================
// Route — states + navigation + in-shell grouping (FR-4.2/4.14/4.16/4.17)
// ===========================================================================

describe("<ActivityRoute>", () => {
  it("reads GET /v1/agent/runs, groups by day, and issues ZERO /v1/audit requests (DoD 12)", async () => {
    apiMocks.listRunHistory.mockResolvedValue(
      runList([
        entry({
          run_id: "run_today",
          conversation_id: "conv_today",
          conversation_title: "Today run",
          status: "completed",
          started_at: TODAY_ISO,
        }),
        entry({
          run_id: "run_yday",
          conversation_id: "conv_yday",
          conversation_title: "Yesterday run",
          status: "completed",
          started_at: YESTERDAY_ISO,
        }),
      ]),
    );

    renderRoute();

    expect(screen.getByTestId("activity-route")).toHaveAttribute(
      "data-state",
      "loading",
    );
    await waitFor(() =>
      expect(screen.getByTestId("activity-route")).toHaveAttribute(
        "data-state",
        "ready",
      ),
    );

    const dividers = screen.getAllByTestId("activity-day");
    expect(dividers.map((d) => d.textContent)).toEqual(["Today", "Yesterday"]);
    expect(screen.getByText("Today run")).toBeInTheDocument();
    expect(screen.queryAllByTestId("item-link")).toHaveLength(0);

    // The swallowed-403 regression guard: the audit endpoint is never touched.
    expect(apiMocks.listAuditEvents).not.toHaveBeenCalled();
  });

  // DoD 13 — one composer, two hosts: the identical fixture renders the identical
  // meta sub-line on the web route and the desktop binder.
  it("renders the meta sub-line '4 apps · 7 steps · awaiting 1 approval' from the counters (DoD 13)", async () => {
    apiMocks.listRunHistory.mockResolvedValue(
      runList([
        entry({
          run_id: "run_meta",
          conversation_id: "conv_meta",
          conversation_title: "Launch Week ops",
          status: "running",
          connector_count: 4,
          step_count: 7,
          pending_approval_count: 1,
          started_at: TODAY_ISO,
        }),
      ]),
    );

    renderRoute();
    await waitFor(() =>
      expect(screen.getByTestId("activity-route")).toHaveAttribute(
        "data-state",
        "ready",
      ),
    );
    const rowEl = screen.getByTestId("activity-row");
    expect(within(rowEl).getByTestId("activity-row-meta")).toHaveTextContent(
      "4 apps · 7 steps · awaiting 1 approval",
    );
  });

  it("a row invokes the host onOpenRun with { conversationId, runId } (FR-4.16, Seam C)", async () => {
    apiMocks.listRunHistory.mockResolvedValue(
      runList([
        entry({
          run_id: "run_live",
          conversation_id: "conv_live",
          status: "running",
          conversation_title: "Live sync",
          started_at: TODAY_ISO,
        }),
      ]),
    );

    const { onOpenRun } = renderRoute();
    await waitFor(() =>
      expect(screen.getByTestId("activity-route")).toHaveAttribute(
        "data-state",
        "ready",
      ),
    );

    const rowEl = screen.getByTestId("activity-row");
    expect(rowEl).toHaveAttribute("role", "button");
    await userEvent.click(rowEl);
    expect(onOpenRun).toHaveBeenCalledWith({
      conversationId: "conv_live",
      runId: "run_live" as RunId,
    });
  });

  it("the retention link invokes the host onOpenRetentionSettings (FR-4.17)", async () => {
    apiMocks.listRunHistory.mockResolvedValue(runList([entry()]));
    const { onOpenRetentionSettings } = renderRoute();
    await waitFor(() =>
      expect(screen.getByTestId("activity-route")).toHaveAttribute(
        "data-state",
        "ready",
      ),
    );
    await userEvent.click(screen.getByTestId("activity-retention-link"));
    expect(onOpenRetentionSettings).toHaveBeenCalledTimes(1);
  });

  it("renders the empty state when there are no runs (FR-4.2)", async () => {
    apiMocks.listRunHistory.mockResolvedValue(runList([]));
    renderRoute();
    await waitFor(() =>
      expect(screen.getByTestId("activity-route")).toHaveAttribute(
        "data-state",
        "empty",
      ),
    );
    expect(screen.getByText("Nothing here yet")).toBeInTheDocument();
  });

  it("renders an error state with a working Retry when the run-list fetch fails (FR-4.2)", async () => {
    apiMocks.listRunHistory
      .mockRejectedValueOnce(new Error("tenant lookup failed"))
      .mockResolvedValueOnce(runList([entry()]));

    renderRoute();
    await waitFor(() =>
      expect(screen.getByTestId("activity-route")).toHaveAttribute(
        "data-state",
        "error",
      ),
    );
    expect(screen.getByTestId("activity-error")).toHaveAttribute(
      "role",
      "alert",
    );

    await userEvent.click(screen.getByTestId("activity-retry"));
    await waitFor(() =>
      expect(screen.getByTestId("activity-route")).toHaveAttribute(
        "data-state",
        "ready",
      ),
    );
    expect(apiMocks.listRunHistory).toHaveBeenCalledTimes(2);
    // Never reached for the audit stream, even across a retry.
    expect(apiMocks.listAuditEvents).not.toHaveBeenCalled();
  });

  it("a run-list read failure surfaces as an error — never swallowed to an empty feed (DoD 12)", async () => {
    // The old code swallowed the audit half into []; there is no such half now,
    // so a failure must reach the error state, not degrade silently.
    apiMocks.listRunHistory.mockRejectedValue(new Error("403 forbidden"));
    renderRoute();
    await waitFor(() =>
      expect(screen.getByTestId("activity-route")).toHaveAttribute(
        "data-state",
        "error",
      ),
    );
    expect(screen.queryByTestId("activity-row")).toBeNull();
  });
});
