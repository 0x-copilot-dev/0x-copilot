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
  ConversationId,
  MemoryItem,
  MemoryItemId,
  MemoryListResponse,
  MemoryStreamEnvelope,
  TenantId,
  UserId,
} from "@0x-copilot/api-types";

const memoryApiMocks = vi.hoisted(() => ({
  fetchMemory: vi.fn(),
  fetchMemoryItem: vi.fn(),
  deleteMemory: vi.fn(),
  fetchMemoryProposals: vi.fn(),
  acceptMemoryProposal: vi.fn(),
  rejectMemoryProposal: vi.fn(),
  streamMemoryEvents: vi.fn(),
}));
vi.mock("../../../api/memoryApi", async () => {
  const actual = await vi.importActual<typeof import("../../../api/memoryApi")>(
    "../../../api/memoryApi",
  );
  return {
    ...actual,
    fetchMemory: memoryApiMocks.fetchMemory,
    fetchMemoryItem: memoryApiMocks.fetchMemoryItem,
    deleteMemory: memoryApiMocks.deleteMemory,
    fetchMemoryProposals: memoryApiMocks.fetchMemoryProposals,
    acceptMemoryProposal: memoryApiMocks.acceptMemoryProposal,
    rejectMemoryProposal: memoryApiMocks.rejectMemoryProposal,
    streamMemoryEvents: memoryApiMocks.streamMemoryEvents,
  };
});

import { MemoryGateway } from "../MemoryGateway";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

function item(overrides: Partial<MemoryItem> = {}): MemoryItem {
  return {
    id: "mem_1" as MemoryItemId,
    tenant_id: "tenant_1" as TenantId,
    scope: "user",
    kind: "skill",
    title: "Speaks Python",
    body: "Long-time Python developer.",
    tags: ["python"],
    created_by: { kind: "user", id: "user_test" },
    last_used_at: null,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
    project_id: null,
    ...overrides,
  };
}

function listResponse(items: ReadonlyArray<MemoryItem>): MemoryListResponse {
  return { items, next_cursor: null };
}

function envelope(
  type: MemoryStreamEnvelope["event_type"],
  payload: { item?: MemoryItem; deleted_id?: MemoryItemId } = {},
  sequenceNo = 1,
): MemoryStreamEnvelope {
  return {
    event_id: `evt_${sequenceNo}`,
    sequence_no: sequenceNo,
    event_type: type,
    item: payload.item,
    deleted_id: payload.deleted_id,
    created_at: "2026-05-18T09:00:00Z",
  };
}

function captureStreamCallbacks(): {
  readonly lastCall: () => {
    onEvent: (e: MemoryStreamEnvelope) => void;
    onError: (err: Event) => void;
    onOpen?: () => void;
  };
} {
  return {
    lastCall: () => {
      const calls = memoryApiMocks.streamMemoryEvents.mock.calls;
      const opts = calls[calls.length - 1][0] as {
        onEvent: (e: MemoryStreamEnvelope) => void;
        onError: (err: Event) => void;
        onOpen?: () => void;
      };
      return opts;
    },
  };
}

describe("MemoryGateway", () => {
  beforeEach(() => {
    Object.values(memoryApiMocks).forEach((m: Mock) => m.mockReset());
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the list happy path", async () => {
    memoryApiMocks.fetchMemory.mockResolvedValueOnce(
      listResponse([item({ title: "Speaks Python" })]),
    );
    memoryApiMocks.streamMemoryEvents.mockReturnValueOnce({
      close: vi.fn(),
    });

    render(<MemoryGateway identity={IDENTITY} />);
    expect(await screen.findByTestId("memory-route")).toBeDefined();
    await waitFor(() => {
      expect(screen.queryByTestId("memory-route-loading")).toBeNull();
    });
    expect(screen.getAllByTestId("memory-route-row")).toHaveLength(1);
    expect(screen.getByText("Speaks Python")).toBeDefined();
  });

  it("merges a memory.created envelope into the list", async () => {
    memoryApiMocks.fetchMemory.mockResolvedValueOnce(listResponse([item()]));
    const harness = captureStreamCallbacks();
    memoryApiMocks.streamMemoryEvents.mockReturnValueOnce({
      close: vi.fn(),
    });

    render(<MemoryGateway identity={IDENTITY} />);
    await waitFor(() => {
      expect(screen.queryByTestId("memory-route-loading")).toBeNull();
    });
    expect(screen.getAllByTestId("memory-route-row")).toHaveLength(1);

    act(() => {
      harness
        .lastCall()
        .onEvent(
          envelope(
            "memory.created",
            { item: item({ id: "mem_2" as MemoryItemId, title: "New row" }) },
            7,
          ),
        );
    });

    await waitFor(() => {
      expect(screen.getAllByTestId("memory-route-row")).toHaveLength(2);
    });
    expect(screen.getByText("New row")).toBeDefined();
  });

  it("removes a row on memory.deleted envelope", async () => {
    memoryApiMocks.fetchMemory.mockResolvedValueOnce(
      listResponse([item({ id: "mem_1" as MemoryItemId })]),
    );
    const harness = captureStreamCallbacks();
    memoryApiMocks.streamMemoryEvents.mockReturnValueOnce({
      close: vi.fn(),
    });

    render(<MemoryGateway identity={IDENTITY} />);
    await waitFor(() => {
      expect(screen.queryByTestId("memory-route-loading")).toBeNull();
    });

    act(() => {
      harness
        .lastCall()
        .onEvent(
          envelope(
            "memory.deleted",
            { deleted_id: "mem_1" as MemoryItemId },
            8,
          ),
        );
    });

    await waitFor(() => {
      expect(screen.queryAllByTestId("memory-route-row")).toHaveLength(0);
    });
  });

  it("opens proposals queue when Proposals is clicked", async () => {
    memoryApiMocks.fetchMemory.mockResolvedValueOnce(listResponse([item()]));
    memoryApiMocks.streamMemoryEvents.mockReturnValueOnce({
      close: vi.fn(),
    });
    memoryApiMocks.fetchMemoryProposals.mockResolvedValueOnce({
      proposals: [
        {
          id: "mp_1",
          tenant_id: "tenant_1" as TenantId,
          user_id: "user_test" as UserId,
          proposed_at: "2026-05-18T09:00:00Z",
          proposed_kind: "fact",
          proposed_title: "Q1 launch",
          proposed_body: "…",
          source: { kind: "chat", id: "conv_42" as ConversationId },
          status: "pending",
          decided_at: null,
        },
      ],
      next_cursor: null,
    });

    const onSubPathChange = vi.fn();
    render(
      <MemoryGateway identity={IDENTITY} onSubPathChange={onSubPathChange} />,
    );
    await waitFor(() => {
      expect(screen.queryByTestId("memory-route-loading")).toBeNull();
    });

    fireEvent.click(screen.getByTestId("memory-route-open-proposals"));

    await waitFor(() => {
      expect(screen.queryByTestId("memory-proposals-route")).toBeDefined();
    });
    expect(onSubPathChange).toHaveBeenCalledWith("proposals");
  });
});
