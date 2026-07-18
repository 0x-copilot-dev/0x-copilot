// ActivityRoute — host-binder integration tests (PR-4.6).
//
// Covers PRD §8: composed rows render grouped by the component
// (FR-4.14/4.19), a running row → onOpenRun (FR-4.16), the retention link
// → onOpenRetentionSettings (FR-4.17), and the error / empty states
// (FR-4.2), plus the pure composition (`projectActivityRows`) + status
// fold (`mapRunStatus`).
//
// The "transport" is mocked at the two composed endpoints
// (`listConversations` + `listAuditEvents`); the REAL `activityApi`
// composition and the REAL `<ActivityDestination>` run, so the test
// exercises projection AND in-shell day grouping end-to-end. Fixtures use
// the local `Date` constructor + a fixed `now` (Date.now spy) so the
// Today / Yesterday derivation is timezone-robust.

import type {
  AuditEvent,
  Conversation,
  ListAuditEventsResponse,
  ConversationListResponse,
  RunId,
} from "@0x-copilot/api-types";
import {
  RouterProvider,
  registerItemRefResolver,
  __resetItemRefRegistryForTests,
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

// --- Mock the two composed endpoints (the "transport") --------------------
// The real `activityApi` composition + the real `<ActivityDestination>`
// run on top of these; only the network reads are stubbed. Mirrors the
// InboxRoute.test importActual+override pattern.
const apiMocks = vi.hoisted(() => ({
  listConversations: vi.fn(),
  listAuditEvents: vi.fn(),
}));
vi.mock("../../api/agentApi", async () => {
  const actual =
    await vi.importActual<typeof import("../../api/agentApi")>(
      "../../api/agentApi",
    );
  return { ...actual, listConversations: apiMocks.listConversations };
});
vi.mock("../../api/auditApi", async () => {
  const actual =
    await vi.importActual<typeof import("../../api/auditApi")>(
      "../../api/auditApi",
    );
  return { ...actual, listAuditEvents: apiMocks.listAuditEvents };
});

// Imports below resolve through the mocks above.
import { ActivityRoute } from "./ActivityRoute";
import { mapRunStatus, projectActivityRows } from "./api/activityApi";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

// --- Timezone-robust fixtures ---------------------------------------------
// Local noon on Jul 18 2026. Rows below are built with the same local
// `Date` constructor so their calendar day round-trips regardless of the
// runner's timezone.
const NOW = new Date(2026, 6, 18, 12, 0, 0).getTime();
const TODAY_ISO = new Date(2026, 6, 18, 9, 0, 0).toISOString();
const TODAY_LATER_ISO = new Date(2026, 6, 18, 10, 30, 0).toISOString();
const YESTERDAY_ISO = new Date(2026, 6, 17, 15, 0, 0).toISOString();

function conv(over: Partial<Conversation> = {}): Conversation {
  return {
    conversation_id: "conv_default",
    org_id: "org_test",
    user_id: "user_test",
    assistant_id: "asst_1",
    title: "Sync my inbox",
    status: "active",
    created_at: TODAY_ISO,
    updated_at: TODAY_ISO,
    archived_at: null,
    metadata: {},
    schema_version: 1,
    latest_run_id: "run_default",
    latest_run_status: "completed",
    ...over,
  };
}

function audit(over: Partial<AuditEvent> = {}): AuditEvent {
  return {
    stream: "mcp_audit_events",
    seq: 1,
    audit_id: "aud_1",
    org_id: "org_test",
    actor_user_id: "user_test",
    actor_kind: "user",
    subject_user_id: null,
    action: "mcp.tool.invoke",
    resource_type: "run",
    resource_id: "run_default",
    outcome: "success",
    metadata: { connector_id: "gmail" },
    chain: { seq: 1, prev_hash: null, signature: null, key_version: null },
    created_at: TODAY_ISO,
    ...over,
  };
}

function convList(
  conversations: readonly Conversation[],
): ConversationListResponse {
  return {
    conversations: [...conversations],
    next_cursor: null,
    has_more: false,
  };
}

function auditList(rows: readonly AuditEvent[]): ListAuditEventsResponse {
  return {
    rows: [...rows],
    next_cursor: null,
    has_more: false,
    degraded_streams: [],
  };
}

// --- Router + ItemLink resolver scaffolding -------------------------------
// Non-running rows navigate through the `"run"` ItemLink resolver; the
// host (Run destination) owns it, so tests register a stand-in and wrap
// the route in a RouterProvider the way the App shell does.
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
  apiMocks.listConversations.mockReset();
  apiMocks.listAuditEvents.mockReset();
  navigate.mockClear();
  vi.spyOn(Date, "now").mockReturnValue(NOW);
  registerItemRefResolver("run", async (id) => ({
    label: `Run ${id}`,
    icon: null,
    route: { kind: "run", runId: id },
  }));
});

afterEach(() => {
  __resetItemRefRegistryForTests();
  vi.restoreAllMocks();
});

// ===========================================================================
// Pure composition — projectActivityRows + mapRunStatus (FR-4.15/4.18/4.19)
// ===========================================================================

describe("mapRunStatus", () => {
  it("folds waiting_for_approval into needs_input (Inbox fold, FR-4.18)", () => {
    expect(mapRunStatus("waiting_for_approval")).toBe("needs_input");
  });

  it("maps in-flight statuses to running", () => {
    expect(mapRunStatus("running")).toBe("running");
    expect(mapRunStatus("queued")).toBe("running");
    expect(mapRunStatus("cancelling")).toBe("running");
  });

  it("maps completed to done and terminal-non-clean to stopped", () => {
    expect(mapRunStatus("completed")).toBe("done");
    expect(mapRunStatus("cancelled")).toBe("stopped");
    expect(mapRunStatus("failed")).toBe("stopped");
    expect(mapRunStatus("timed_out")).toBe("stopped");
  });
});

describe("projectActivityRows", () => {
  it("uses conversations as the run spine and audit for the meta line", () => {
    const rows = projectActivityRows(
      [
        conv({
          conversation_id: "conv_a",
          latest_run_id: "run_a",
          latest_run_status: "completed",
          title: "Weekly digest",
          updated_at: TODAY_ISO,
        }),
      ],
      [
        audit({ resource_id: "run_a", metadata: { connector_id: "gmail" } }),
        audit({
          audit_id: "aud_2",
          resource_id: "run_a",
          metadata: { server_id: "calendar" },
        }),
        // Unrelated run's audit — must not leak into run_a's meta.
        audit({
          audit_id: "aud_3",
          resource_id: "run_other",
          metadata: { connector_id: "slack" },
        }),
      ],
    );

    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      run_id: "run_a",
      title: "Weekly digest",
      status: "done",
      meta: "calendar · gmail", // deduped + sorted
      started_at: TODAY_ISO,
    });
  });

  it("skips conversations that never ran (no latest_run_id)", () => {
    const rows = projectActivityRows(
      [
        conv({ conversation_id: "ran", latest_run_id: "run_x" }),
        conv({
          conversation_id: "never_ran",
          latest_run_id: null,
          latest_run_status: null,
        }),
      ],
      [],
    );
    expect(rows.map((r) => r.run_id)).toEqual(["run_x"]);
  });

  it("orders the flat list newest-first (FR-4.19) and falls back to a default title", () => {
    const rows = projectActivityRows(
      [
        conv({
          conversation_id: "old",
          latest_run_id: "run_old",
          updated_at: YESTERDAY_ISO,
          title: "   ",
        }),
        conv({
          conversation_id: "new",
          latest_run_id: "run_new",
          updated_at: TODAY_LATER_ISO,
        }),
      ],
      [],
    );
    expect(rows.map((r) => r.run_id)).toEqual(["run_new", "run_old"]);
    // Blank title falls back rather than rendering empty.
    expect(rows.find((r) => r.run_id === "run_old")?.title).toBe(
      "Untitled run",
    );
    // No audit → empty (but present) meta string.
    expect(rows[0].meta).toBe("");
  });
});

// ===========================================================================
// Route — states + navigation + in-shell grouping (FR-4.2/4.14/4.16/4.17)
// ===========================================================================

describe("<ActivityRoute>", () => {
  it("composes conversations + audit into day-grouped rows (FR-4.14/4.19)", async () => {
    apiMocks.listConversations.mockResolvedValue(
      convList([
        conv({
          conversation_id: "conv_today",
          latest_run_id: "run_today",
          latest_run_status: "completed",
          title: "Today run",
          updated_at: TODAY_ISO,
        }),
        conv({
          conversation_id: "conv_yday",
          latest_run_id: "run_yday",
          latest_run_status: "completed",
          title: "Yesterday run",
          updated_at: YESTERDAY_ISO,
        }),
      ]),
    );
    apiMocks.listAuditEvents.mockResolvedValue(
      auditList([
        audit({
          resource_id: "run_today",
          metadata: { connector_id: "gmail" },
        }),
      ]),
    );

    renderRoute();

    // Loading first (SectionResult still null → destination skeleton).
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

    // Day dividers, most-recent day first (grouping done by the component).
    const dividers = screen.getAllByTestId("activity-day");
    expect(dividers.map((d) => d.textContent)).toEqual(["Today", "Yesterday"]);

    // The meta line comes from the composed audit rows.
    const todayGroup = screen.getAllByTestId("activity-day-group")[0];
    expect(
      within(todayGroup).getByTestId("activity-row-meta"),
    ).toHaveTextContent("gmail");

    // Non-running rows resolve their title through the "run" ItemLink
    // resolver — await it so the resolver's state update settles inside
    // act rather than after teardown.
    await screen.findAllByTestId("item-link");
  });

  it("a running run row invokes the host onOpenRun with the run id (FR-4.16)", async () => {
    apiMocks.listConversations.mockResolvedValue(
      convList([
        conv({
          conversation_id: "conv_live",
          latest_run_id: "run_live",
          latest_run_status: "running",
          title: "Live sync",
          updated_at: TODAY_ISO,
        }),
      ]),
    );
    apiMocks.listAuditEvents.mockResolvedValue(auditList([]));

    const { onOpenRun } = renderRoute();

    await waitFor(() =>
      expect(screen.getByTestId("activity-route")).toHaveAttribute(
        "data-state",
        "ready",
      ),
    );

    const rowEl = screen.getByTestId("activity-row");
    expect(rowEl.tagName).toBe("BUTTON");
    await userEvent.click(rowEl);
    expect(onOpenRun).toHaveBeenCalledWith("run_live" as RunId);
  });

  it("the retention link invokes the host onOpenRetentionSettings (FR-4.17)", async () => {
    apiMocks.listConversations.mockResolvedValue(convList([conv()]));
    apiMocks.listAuditEvents.mockResolvedValue(auditList([]));

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

  it("renders the empty state when no conversation has ever run (FR-4.2)", async () => {
    apiMocks.listConversations.mockResolvedValue(
      convList([
        conv({
          conversation_id: "never",
          latest_run_id: null,
          latest_run_status: null,
        }),
      ]),
    );
    apiMocks.listAuditEvents.mockResolvedValue(auditList([]));

    renderRoute();

    await waitFor(() =>
      expect(screen.getByTestId("activity-route")).toHaveAttribute(
        "data-state",
        "empty",
      ),
    );
    expect(screen.getByText("No activity yet")).toBeInTheDocument();
  });

  it("renders an error state with a working Retry when the conversation fetch fails (FR-4.2)", async () => {
    apiMocks.listConversations
      .mockRejectedValueOnce(new Error("tenant lookup failed"))
      .mockResolvedValueOnce(convList([conv()]));
    apiMocks.listAuditEvents.mockResolvedValue(auditList([]));

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

    await userEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() =>
      expect(screen.getByTestId("activity-route")).toHaveAttribute(
        "data-state",
        "ready",
      ),
    );
    expect(apiMocks.listConversations).toHaveBeenCalledTimes(2);
  });

  it("degrades to conversations-only rows when the audit read fails (PRD §11)", async () => {
    apiMocks.listConversations.mockResolvedValue(
      convList([
        conv({
          conversation_id: "conv_a",
          latest_run_id: "run_a",
          latest_run_status: "completed",
          updated_at: TODAY_ISO,
        }),
      ]),
    );
    apiMocks.listAuditEvents.mockRejectedValue(new Error("audit store down"));

    renderRoute();

    // Audit failure must NOT sink the feed — rows still render.
    await waitFor(() =>
      expect(screen.getByTestId("activity-route")).toHaveAttribute(
        "data-state",
        "ready",
      ),
    );
    expect(screen.getByTestId("activity-row")).toBeInTheDocument();
  });
});
