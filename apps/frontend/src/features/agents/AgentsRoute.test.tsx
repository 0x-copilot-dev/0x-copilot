import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type Mock,
} from "vitest";

import type {
  Agent,
  AgentId,
  AgentListResponse,
  AgentStreamEnvelope,
  AgentUsageResponse,
  AgentVersion,
  AgentVersionId,
  AgentVersionListResponse,
  TenantId,
  UserId,
} from "../../api/_agents-stub";

// Mock the agentsApi module so the tests don't have to drive the real
// fetch / SSE plumbing — that surface is covered in `agentsApi.test.ts`.
const agentsApiMocks = vi.hoisted(() => ({
  fetchAgents: vi.fn(),
  fetchAgent: vi.fn(),
  fetchAgentVersions: vi.fn(),
  fetchAgentUsage: vi.fn(),
  installAgent: vi.fn(),
  uninstallAgent: vi.fn(),
  duplicateAgent: vi.fn(),
  patchAgent: vi.fn(),
  snapshotAgentVersion: vi.fn(),
  streamAgentEvents: vi.fn(),
}));
vi.mock("../../api/agentsApi", async () => {
  const actual = await vi.importActual<typeof import("../../api/agentsApi")>(
    "../../api/agentsApi",
  );
  return {
    ...actual,
    fetchAgents: agentsApiMocks.fetchAgents,
    fetchAgent: agentsApiMocks.fetchAgent,
    fetchAgentVersions: agentsApiMocks.fetchAgentVersions,
    fetchAgentUsage: agentsApiMocks.fetchAgentUsage,
    installAgent: agentsApiMocks.installAgent,
    uninstallAgent: agentsApiMocks.uninstallAgent,
    duplicateAgent: agentsApiMocks.duplicateAgent,
    patchAgent: agentsApiMocks.patchAgent,
    snapshotAgentVersion: agentsApiMocks.snapshotAgentVersion,
    streamAgentEvents: agentsApiMocks.streamAgentEvents,
  };
});

// Imports below this line resolve through the mocks above.
import { AgentsRoute, applyAgentEnvelope } from "./AgentsRoute";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

function agentFixture(overrides: Partial<Agent> = {}): Agent {
  return {
    id: "agent_1" as AgentId,
    tenant_id: "tenant_1" as TenantId,
    name: "Inbox Triage",
    slug: "inbox-triage",
    description: "Triage incoming approvals.",
    icon_emoji: "📥",
    color_hue: 220,
    version: 1,
    status: "available",
    origin: "system",
    owner_user_id: null,
    instructions: "You are a triage assistant.",
    model_default: {
      model_id: "anthropic:claude-sonnet-4-7-1m",
      reasoning_depth: "balanced",
    },
    connectors_default: [],
    skills: [],
    permissions: {
      autonomy: "manual_approval",
      max_tool_calls_per_run: 10,
      max_output_tokens: 4000,
      read_only: false,
    },
    memory_ref: null,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-18T09:00:00Z",
    viewer_install_status: "available",
    viewer_usage_7d: null,
    ...overrides,
  };
}

function versionFixture(overrides: Partial<AgentVersion> = {}): AgentVersion {
  return {
    id: "agentver_1" as AgentVersionId,
    agent_id: "agent_1" as AgentId,
    version: 1,
    instructions_snapshot: "You are a triage assistant.",
    model_default_snapshot: {
      model_id: "anthropic:claude-sonnet-4-7-1m",
      reasoning_depth: "balanced",
    },
    skills_snapshot: [],
    connectors_default_snapshot: [],
    permissions_snapshot: {
      autonomy: "manual_approval",
      max_tool_calls_per_run: 10,
      max_output_tokens: 4000,
      read_only: false,
    },
    created_at: "2026-05-18T09:00:00Z",
    created_by: "user_test" as UserId,
    label: null,
    ...overrides,
  };
}

function listResponse(items: ReadonlyArray<Agent>): AgentListResponse {
  return { items, next_cursor: null };
}

function versionListResponse(
  items: ReadonlyArray<AgentVersion>,
): AgentVersionListResponse {
  return { items, next_cursor: null };
}

function usageResponse(): AgentUsageResponse {
  return {
    agent_id: "agent_1" as AgentId,
    period: "week",
    rollups: [],
    totals: {
      agent_id: "agent_1" as AgentId,
      period: "week",
      run_count: 5,
      token_in: 120,
      token_out: 240,
      cost_usd_micro: 1234,
    },
  };
}

function envelope(
  type: AgentStreamEnvelope["event_type"],
  payload: AgentStreamEnvelope["payload"],
  agentId: AgentId,
  sequenceNo = 1,
): AgentStreamEnvelope {
  return {
    sequence_no: sequenceNo,
    event_type: type,
    agent_id: agentId,
    payload,
    emitted_at: "2026-05-18T09:00:00Z",
  };
}

function captureStreamCallbacks(closeMock = vi.fn()): {
  readonly close: Mock;
  readonly lastCall: () => {
    onEvent: (e: AgentStreamEnvelope) => void;
    onError: (e: Event) => void;
    onOpen?: () => void;
  };
} {
  let lastCallbacks: {
    onEvent: (e: AgentStreamEnvelope) => void;
    onError: (e: Event) => void;
    onOpen?: () => void;
  } = { onEvent: () => undefined, onError: () => undefined };
  agentsApiMocks.streamAgentEvents.mockImplementation(
    ({
      onEvent,
      onError,
      onOpen,
    }: {
      onEvent: (e: AgentStreamEnvelope) => void;
      onError: (e: Event) => void;
      onOpen?: () => void;
    }) => {
      lastCallbacks = { onEvent, onError, onOpen };
      return { close: closeMock };
    },
  );
  return {
    close: closeMock,
    lastCall: () => lastCallbacks,
  };
}

// Default the version-history + usage fetches to resolved-empty so they
// don't reject and surface errors in render tests that don't care about
// the detail pane's data path.
function defaultDetailFetches(): void {
  agentsApiMocks.fetchAgentVersions.mockResolvedValue(versionListResponse([]));
  agentsApiMocks.fetchAgentUsage.mockResolvedValue(usageResponse());
}

// ===========================================================================
// PURE REDUCER — applyAgentEnvelope
// ===========================================================================

describe("applyAgentEnvelope", () => {
  it("flips status in place on agent_status_changed", () => {
    const a = agentFixture({ id: "a" as AgentId, status: "available" });
    const next = applyAgentEnvelope(
      [a],
      envelope(
        "agent_status_changed",
        {
          agent_id: "a" as AgentId,
          status: "disabled",
          prior_status: "available",
        },
        "a" as AgentId,
      ),
    );
    expect(next[0].status).toBe("disabled");
  });

  it("is a no-op for agent_installed / agent_uninstalled / agent_updated at the list layer", () => {
    const a = agentFixture({ id: "a" as AgentId });
    const before = [a];

    const installed = applyAgentEnvelope(
      before,
      envelope(
        "agent_installed",
        { agent_id: "a" as AgentId, user_id: "user_test" as UserId },
        "a" as AgentId,
      ),
    );
    expect(installed).toBe(before);

    const uninstalled = applyAgentEnvelope(
      before,
      envelope(
        "agent_uninstalled",
        { agent_id: "a" as AgentId, user_id: "user_test" as UserId },
        "a" as AgentId,
      ),
    );
    expect(uninstalled).toBe(before);

    const updated = applyAgentEnvelope(
      before,
      envelope(
        "agent_updated",
        { agent_id: "a" as AgentId, version: 2 },
        "a" as AgentId,
      ),
    );
    expect(updated).toBe(before);
  });

  it("returns the same array on agent_status_changed for an unknown id", () => {
    const a = agentFixture({ id: "a" as AgentId, status: "available" });
    const before = [a];
    const after = applyAgentEnvelope(
      before,
      envelope(
        "agent_status_changed",
        {
          agent_id: "b" as AgentId,
          status: "disabled",
          prior_status: "available",
        },
        "b" as AgentId,
      ),
    );
    expect(after).toBe(before);
  });
});

// ===========================================================================
// RENDER — happy + error paths
// ===========================================================================

describe("AgentsRoute render", () => {
  beforeEach(() => {
    agentsApiMocks.fetchAgents.mockReset();
    agentsApiMocks.fetchAgent.mockReset();
    agentsApiMocks.fetchAgentVersions.mockReset();
    agentsApiMocks.fetchAgentUsage.mockReset();
    agentsApiMocks.installAgent.mockReset();
    agentsApiMocks.uninstallAgent.mockReset();
    agentsApiMocks.duplicateAgent.mockReset();
    agentsApiMocks.patchAgent.mockReset();
    agentsApiMocks.snapshotAgentVersion.mockReset();
    agentsApiMocks.streamAgentEvents.mockReset();
    agentsApiMocks.streamAgentEvents.mockReturnValue({ close: vi.fn() });
    defaultDetailFetches();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the loading state, then the ready list", async () => {
    agentsApiMocks.fetchAgents.mockResolvedValueOnce(
      listResponse([agentFixture({ name: "Inbox Triage" })]),
    );

    render(<AgentsRoute identity={IDENTITY} />);

    expect(screen.getByTestId("agents-route")).toHaveAttribute(
      "data-state",
      "loading",
    );

    await waitFor(() => {
      expect(screen.getByTestId("agents-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });
    expect(screen.getByText("Inbox Triage")).toBeInTheDocument();
    expect(screen.getByTestId("agents-route")).toHaveAttribute(
      "data-item-count",
      "1",
    );
  });

  it("renders the empty state when the server returns no items", async () => {
    agentsApiMocks.fetchAgents.mockResolvedValueOnce(listResponse([]));
    render(<AgentsRoute identity={IDENTITY} />);
    await waitFor(() => {
      expect(screen.getByTestId("agents-route-empty")).toBeInTheDocument();
    });
  });

  it("renders the error state on fetch failure and retries on click", async () => {
    agentsApiMocks.fetchAgents.mockRejectedValueOnce(new Error("boom"));
    agentsApiMocks.fetchAgents.mockResolvedValueOnce(
      listResponse([agentFixture()]),
    );

    render(<AgentsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("agents-route-error")).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("agents-route-error-message").textContent,
    ).toContain("boom");

    fireEvent.click(screen.getByTestId("agents-route-retry"));

    await waitFor(() => {
      expect(screen.getByTestId("agents-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });
    expect(agentsApiMocks.fetchAgents).toHaveBeenCalledTimes(2);
  });
});

// ===========================================================================
// SSE — deltas merge + refetch for install/uninstall/update events
// ===========================================================================

describe("AgentsRoute SSE", () => {
  beforeEach(() => {
    agentsApiMocks.fetchAgents.mockReset();
    agentsApiMocks.fetchAgent.mockReset();
    agentsApiMocks.streamAgentEvents.mockReset();
    defaultDetailFetches();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("subscribes after the initial load and flips status on agent_status_changed deltas", async () => {
    agentsApiMocks.fetchAgents.mockResolvedValueOnce(
      listResponse([agentFixture({ id: "a" as AgentId, status: "available" })]),
    );
    const sse = captureStreamCallbacks();

    render(<AgentsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(agentsApiMocks.streamAgentEvents).toHaveBeenCalledTimes(1);
    });

    act(() => {
      sse.lastCall().onEvent(
        envelope(
          "agent_status_changed",
          {
            agent_id: "a" as AgentId,
            status: "disabled",
            prior_status: "available",
          },
          "a" as AgentId,
          1,
        ),
      );
    });

    await waitFor(() => {
      expect(screen.getByTestId("agents-route-row")).toHaveAttribute(
        "data-agent-status",
        "disabled",
      );
    });
  });

  it("refetches the affected row on agent_installed (merged-overrides view, sub-PRD §3.3)", async () => {
    agentsApiMocks.fetchAgents.mockResolvedValueOnce(
      listResponse([
        agentFixture({
          id: "a" as AgentId,
          viewer_install_status: "available",
        }),
      ]),
    );
    agentsApiMocks.fetchAgent.mockResolvedValueOnce(
      agentFixture({
        id: "a" as AgentId,
        viewer_install_status: "installed",
      }),
    );
    const sse = captureStreamCallbacks();

    render(<AgentsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(agentsApiMocks.streamAgentEvents).toHaveBeenCalledTimes(1);
    });

    act(() => {
      sse.lastCall().onEvent(
        envelope(
          "agent_installed",
          {
            agent_id: "a" as AgentId,
            user_id: "user_test" as UserId,
            scope: "user",
          },
          "a" as AgentId,
          5,
        ),
      );
    });

    await waitFor(() => {
      expect(agentsApiMocks.fetchAgent).toHaveBeenCalledWith(IDENTITY, "a");
    });
  });

  it("closes the active stream when the stream errors out (reconnect is then scheduled)", async () => {
    agentsApiMocks.fetchAgents.mockResolvedValueOnce(
      listResponse([agentFixture()]),
    );
    const sse = captureStreamCallbacks();

    render(<AgentsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(agentsApiMocks.streamAgentEvents).toHaveBeenCalledTimes(1);
    });

    // Trigger an error → component closes the active handle and queues
    // an exponential-backoff reconnect via setTimeout. Same pattern as
    // ProjectsRoute / RoutinesRoute — we assert on the observable close
    // side-effect; the reconnect timing is covered structurally by the
    // RECONNECT_BACKOFF_* constants.
    act(() => {
      sse.lastCall().onError(new Event("error"));
    });
    expect(sse.close).toHaveBeenCalled();
  });
});

// ===========================================================================
// MUTATIONS — install / uninstall / duplicate
// ===========================================================================

describe("AgentsRoute mutations", () => {
  beforeEach(() => {
    agentsApiMocks.fetchAgents.mockReset();
    agentsApiMocks.fetchAgent.mockReset();
    agentsApiMocks.installAgent.mockReset();
    agentsApiMocks.uninstallAgent.mockReset();
    agentsApiMocks.duplicateAgent.mockReset();
    agentsApiMocks.streamAgentEvents.mockReset();
    agentsApiMocks.streamAgentEvents.mockReturnValue({ close: vi.fn() });
    defaultDetailFetches();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("calls installAgent and merges the updated row", async () => {
    const a = agentFixture({
      id: "a" as AgentId,
      viewer_install_status: "available",
    });
    agentsApiMocks.fetchAgents.mockResolvedValueOnce(listResponse([a]));
    agentsApiMocks.installAgent.mockResolvedValueOnce(
      agentFixture({
        id: "a" as AgentId,
        viewer_install_status: "installed",
        status: "installed",
      }),
    );

    render(<AgentsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("agents-route-install")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("agents-route-install"));

    await waitFor(() => {
      expect(agentsApiMocks.installAgent).toHaveBeenCalledWith(IDENTITY, "a");
    });
    await waitFor(() => {
      expect(screen.getByTestId("agents-route-uninstall")).toBeInTheDocument();
    });
  });

  it("calls uninstallAgent and merges the updated row", async () => {
    const a = agentFixture({
      id: "a" as AgentId,
      viewer_install_status: "installed",
      status: "installed",
    });
    agentsApiMocks.fetchAgents.mockResolvedValueOnce(listResponse([a]));
    agentsApiMocks.uninstallAgent.mockResolvedValueOnce(
      agentFixture({
        id: "a" as AgentId,
        viewer_install_status: "available",
        status: "available",
      }),
    );

    render(<AgentsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("agents-route-uninstall")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("agents-route-uninstall"));

    await waitFor(() => {
      expect(agentsApiMocks.uninstallAgent).toHaveBeenCalledWith(IDENTITY, "a");
    });
    await waitFor(() => {
      expect(screen.getByTestId("agents-route-install")).toBeInTheDocument();
    });
  });

  it("calls duplicateAgent and prepends the forked row", async () => {
    const a = agentFixture({ id: "a" as AgentId, origin: "system" });
    agentsApiMocks.fetchAgents.mockResolvedValueOnce(listResponse([a]));
    agentsApiMocks.duplicateAgent.mockResolvedValueOnce(
      agentFixture({
        id: "a_custom" as AgentId,
        origin: "custom",
        owner_user_id: "user_test" as UserId,
        name: "Inbox Triage (custom)",
        viewer_install_status: "draft",
        status: "draft",
      }),
    );

    render(<AgentsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("agents-route-duplicate")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("agents-route-duplicate"));

    await waitFor(() => {
      expect(agentsApiMocks.duplicateAgent).toHaveBeenCalledWith(IDENTITY, "a");
    });
    await waitFor(() => {
      expect(screen.getByText("Inbox Triage (custom)")).toBeInTheDocument();
    });
  });

  it("surfaces a pending-error banner when install fails and keeps rendering the list", async () => {
    const a = agentFixture({
      id: "a" as AgentId,
      viewer_install_status: "available",
    });
    agentsApiMocks.fetchAgents.mockResolvedValueOnce(listResponse([a]));
    agentsApiMocks.installAgent.mockRejectedValueOnce(
      new Error("install_forbidden"),
    );

    render(<AgentsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("agents-route-install")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("agents-route-install"));

    await waitFor(() => {
      expect(
        screen.getByTestId("agents-route-pending-error"),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("agents-route-pending-error").textContent,
    ).toContain("install_forbidden");
    // The list itself is still rendered — the user can retry.
    expect(screen.getByTestId("agents-route-row")).toBeInTheDocument();
  });
});

// ===========================================================================
// DETAIL PANEL — editor + version history + usage
// ===========================================================================

describe("AgentsRoute detail panel", () => {
  beforeEach(() => {
    agentsApiMocks.fetchAgents.mockReset();
    agentsApiMocks.fetchAgent.mockReset();
    agentsApiMocks.fetchAgentVersions.mockReset();
    agentsApiMocks.fetchAgentUsage.mockReset();
    agentsApiMocks.patchAgent.mockReset();
    agentsApiMocks.snapshotAgentVersion.mockReset();
    agentsApiMocks.streamAgentEvents.mockReset();
    agentsApiMocks.streamAgentEvents.mockReturnValue({ close: vi.fn() });
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("opens the detail panel on row select and surfaces version history + usage", async () => {
    const a = agentFixture({ id: "a" as AgentId });
    agentsApiMocks.fetchAgents.mockResolvedValueOnce(listResponse([a]));
    agentsApiMocks.fetchAgentVersions.mockResolvedValueOnce(
      versionListResponse([versionFixture({ version: 1, label: "Initial" })]),
    );
    agentsApiMocks.fetchAgentUsage.mockResolvedValueOnce(usageResponse());

    render(<AgentsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("agents-route-select")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("agents-route-select"));

    await waitFor(() => {
      expect(screen.getByTestId("agents-route-detail")).toBeInTheDocument();
    });

    // Version history block fetches against the selected agent id.
    await waitFor(() => {
      expect(agentsApiMocks.fetchAgentVersions).toHaveBeenCalledWith(
        IDENTITY,
        "a",
        { limit: 20 },
      );
    });
    await waitFor(() => {
      expect(screen.getByTestId("agents-route-version-row")).toHaveAttribute(
        "data-version",
        "1",
      );
    });

    // Usage block fetches and renders the totals.
    await waitFor(() => {
      expect(agentsApiMocks.fetchAgentUsage).toHaveBeenCalledWith(
        IDENTITY,
        "a",
        { period: "week" },
      );
    });
    await waitFor(() => {
      expect(screen.getByTestId("agents-route-usage-totals")).toHaveAttribute(
        "data-run-count",
        "5",
      );
    });
  });

  it("saves the edited instructions through patchAgent and merges the updated row", async () => {
    const a = agentFixture({
      id: "a" as AgentId,
      instructions: "old",
      origin: "custom",
      owner_user_id: "user_test" as UserId,
    });
    agentsApiMocks.fetchAgents.mockResolvedValueOnce(listResponse([a]));
    agentsApiMocks.fetchAgentVersions.mockResolvedValueOnce(
      versionListResponse([]),
    );
    agentsApiMocks.fetchAgentUsage.mockResolvedValueOnce(usageResponse());
    agentsApiMocks.patchAgent.mockResolvedValueOnce(
      agentFixture({
        id: "a" as AgentId,
        instructions: "new",
        origin: "custom",
        owner_user_id: "user_test" as UserId,
      }),
    );

    render(<AgentsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("agents-route-select")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("agents-route-select"));

    await waitFor(() => {
      expect(
        screen.getByTestId("agents-route-instructions"),
      ).toBeInTheDocument();
    });
    const ta = screen.getByTestId("agents-route-instructions");
    fireEvent.change(ta, { target: { value: "new" } });
    fireEvent.click(screen.getByTestId("agents-route-save"));

    await waitFor(() => {
      expect(agentsApiMocks.patchAgent).toHaveBeenCalledWith(IDENTITY, "a", {
        instructions: "new",
      });
    });
  });

  it("surfaces a 409 agent_origin_immutable error when patching a system/community agent", async () => {
    const a = agentFixture({ id: "a" as AgentId, origin: "system" });
    agentsApiMocks.fetchAgents.mockResolvedValueOnce(listResponse([a]));
    agentsApiMocks.fetchAgentVersions.mockResolvedValueOnce(
      versionListResponse([]),
    );
    agentsApiMocks.fetchAgentUsage.mockResolvedValueOnce(usageResponse());
    agentsApiMocks.patchAgent.mockRejectedValueOnce(
      new Error("agent_origin_immutable"),
    );

    render(<AgentsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("agents-route-select")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("agents-route-select"));

    await waitFor(() => {
      expect(screen.getByTestId("agents-route-save")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("agents-route-save"));

    await waitFor(() => {
      expect(
        screen.getByTestId("agents-route-pending-error"),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("agents-route-pending-error").textContent,
    ).toContain("agent_origin_immutable");
  });

  it("snapshots a new version via snapshotAgentVersion + refetches the agent", async () => {
    const a = agentFixture({
      id: "a" as AgentId,
      version: 2,
      origin: "custom",
      owner_user_id: "user_test" as UserId,
    });
    agentsApiMocks.fetchAgents.mockResolvedValueOnce(listResponse([a]));
    agentsApiMocks.fetchAgentVersions.mockResolvedValueOnce(
      versionListResponse([]),
    );
    agentsApiMocks.fetchAgentUsage.mockResolvedValueOnce(usageResponse());
    agentsApiMocks.snapshotAgentVersion.mockResolvedValueOnce(
      versionFixture({ version: 3, label: "v3-label" }),
    );
    agentsApiMocks.fetchAgent.mockResolvedValueOnce(
      agentFixture({
        id: "a" as AgentId,
        version: 3,
        origin: "custom",
        owner_user_id: "user_test" as UserId,
      }),
    );

    render(<AgentsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("agents-route-select")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("agents-route-select"));

    await waitFor(() => {
      expect(screen.getByTestId("agents-route-snapshot")).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId("agents-route-version-label"), {
      target: { value: "v3-label" },
    });
    fireEvent.click(screen.getByTestId("agents-route-snapshot"));

    await waitFor(() => {
      expect(agentsApiMocks.snapshotAgentVersion).toHaveBeenCalledWith(
        IDENTITY,
        "a",
        { label: "v3-label" },
      );
    });
    await waitFor(() => {
      expect(agentsApiMocks.fetchAgent).toHaveBeenCalledWith(IDENTITY, "a");
    });
  });
});
