import {
  act,
  render,
  screen,
  waitFor,
  fireEvent,
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
  Person,
  TeamListResponse,
  TeamStreamEnvelope,
  TenantId,
  UserId,
} from "@0x-copilot/api-types";

// Hoisted mocks for teamApi — route tests stay off real fetch/transport.
const teamApiMocks = vi.hoisted(() => ({
  fetchTeam: vi.fn(),
  fetchPerson: vi.fn(),
  streamTeamEvents: vi.fn(),
}));
vi.mock("../../../api/teamApi", async () => {
  const actual = await vi.importActual<typeof import("../../../api/teamApi")>(
    "../../../api/teamApi",
  );
  return {
    ...actual,
    fetchTeam: teamApiMocks.fetchTeam,
    fetchPerson: teamApiMocks.fetchPerson,
    streamTeamEvents: teamApiMocks.streamTeamEvents,
  };
});

import { TeamGateway } from "../TeamGateway";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

function person(overrides: Partial<Person> = {}): Person {
  return {
    id: "user_alice" as UserId,
    tenant_id: "tenant_1" as TenantId,
    display_name: "Alice",
    email: "alice@example.com",
    role: "member",
    presence: "active",
    last_seen_at: "2026-05-18T08:00:00Z",
    joined_at: "2026-01-01T00:00:00Z",
    agents_count: 0,
    projects_count: 0,
    is_self: false,
    ...overrides,
  };
}

function listResponse(items: ReadonlyArray<Person>): TeamListResponse {
  return { people: items, next_cursor: null };
}

function envelope(
  type: TeamStreamEnvelope["event_type"],
  p: Person | undefined,
  sequenceNo = 1,
): TeamStreamEnvelope {
  return {
    event_id: `evt_${sequenceNo}`,
    sequence_no: sequenceNo,
    event_type: type,
    person: p,
    created_at: "2026-05-18T09:00:00Z",
  };
}

function captureStreamCallbacks(closeMock = vi.fn()): {
  readonly close: Mock;
  readonly lastCall: () => {
    onEvent: (e: TeamStreamEnvelope) => void;
    onError: (err: Event) => void;
    onOpen?: () => void;
  };
} {
  return {
    close: closeMock,
    lastCall: () => {
      const calls = teamApiMocks.streamTeamEvents.mock.calls;
      const opts = calls[calls.length - 1][0] as {
        onEvent: (e: TeamStreamEnvelope) => void;
        onError: (err: Event) => void;
        onOpen?: () => void;
      };
      return opts;
    },
  };
}

describe("TeamGateway", () => {
  beforeEach(() => {
    teamApiMocks.fetchTeam.mockReset();
    teamApiMocks.fetchPerson.mockReset();
    teamApiMocks.streamTeamEvents.mockReset();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the list happy path and shows fetched people", async () => {
    teamApiMocks.fetchTeam.mockResolvedValueOnce(
      listResponse([person({ display_name: "Alice" })]),
    );
    teamApiMocks.streamTeamEvents.mockReturnValueOnce({
      close: vi.fn(),
    });

    render(<TeamGateway identity={IDENTITY} />);

    expect(await screen.findByTestId("team-route")).toBeDefined();
    await waitFor(() => {
      expect(screen.queryByTestId("team-route-loading")).toBeNull();
    });
    expect(screen.getAllByTestId("team-route-row")).toHaveLength(1);
    expect(screen.getByText("Alice")).toBeDefined();
  });

  it("merges a presence-changed SSE envelope into the list", async () => {
    teamApiMocks.fetchTeam.mockResolvedValueOnce(
      listResponse([person({ display_name: "Alice", presence: "active" })]),
    );
    const harness = captureStreamCallbacks();
    teamApiMocks.streamTeamEvents.mockReturnValueOnce({
      close: harness.close,
    });

    render(<TeamGateway identity={IDENTITY} />);
    await waitFor(() => {
      expect(screen.queryByTestId("team-route-loading")).toBeNull();
    });

    // Initial state — active.
    expect(
      screen.getByTestId("team-route-row").getAttribute("data-presence"),
    ).toBe("active");

    // SSE pushes a presence change → row updates without a refetch.
    act(() => {
      harness
        .lastCall()
        .onEvent(
          envelope(
            "team.presence_changed",
            person({ display_name: "Alice", presence: "away" }),
            7,
          ),
        );
    });

    await waitFor(() => {
      expect(
        screen.getByTestId("team-route-row").getAttribute("data-presence"),
      ).toBe("away");
    });
  });

  it("opens detail when a row is clicked and notifies onSubPathChange", async () => {
    teamApiMocks.fetchTeam.mockResolvedValueOnce(
      listResponse([person({ id: "user_alice" as UserId })]),
    );
    teamApiMocks.streamTeamEvents.mockReturnValueOnce({
      close: vi.fn(),
    });
    teamApiMocks.fetchPerson.mockResolvedValueOnce({
      person: person({ id: "user_alice" as UserId }),
      agents: [],
      projects: [],
      recent_activity: [],
    });

    const onSubPathChange = vi.fn();
    render(
      <TeamGateway identity={IDENTITY} onSubPathChange={onSubPathChange} />,
    );
    await waitFor(() => {
      expect(screen.queryByTestId("team-route-loading")).toBeNull();
    });

    fireEvent.click(screen.getByTestId("team-route-select"));

    await waitFor(() => {
      expect(screen.queryByTestId("team-detail-route")).toBeDefined();
    });
    expect(onSubPathChange).toHaveBeenCalledWith("user_alice");
  });
});
