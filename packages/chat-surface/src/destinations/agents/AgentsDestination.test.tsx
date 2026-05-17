import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@enterprise-search/chat-transport";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../../providers/RouterProvider";
import { TransportProvider } from "../../providers/TransportProvider";
import type { ArtifactRoute, Router } from "../../routing/router";

import { AgentsDestination, type AgentRunRow } from "./AgentsDestination";

type RequestHandler = (req: TypedRequest) => Promise<unknown>;

function makeTransport(handler: RequestHandler): Transport {
  return {
    async request<TRes>(req: TypedRequest): Promise<TRes> {
      return (await handler(req)) as TRes;
    },
    subscribeServerSentEvents(_opts: SseSubscribeOptions): SseSubscription {
      return { close: () => undefined };
    },
    getSession(): Session {
      return { bearer: null };
    },
    capabilities(): TransportCapabilities {
      return {
        substrate: "web",
        nativeSecretStorage: false,
        fileSystemAccess: false,
        clipboardWrite: false,
        openExternal: false,
      };
    },
  };
}

function makeRouter(): Router<ArtifactRoute> {
  let current: ArtifactRoute | null = null;
  const subs = new Set<(r: ArtifactRoute) => void>();
  return {
    current(): ArtifactRoute {
      if (current === null) throw new Error("no route");
      return current;
    },
    navigate: vi.fn((r: ArtifactRoute) => {
      current = r;
      for (const s of subs) s(r);
    }),
    subscribe(handler) {
      subs.add(handler);
      return () => subs.delete(handler);
    },
  };
}

const SAMPLE_RUNS: readonly AgentRunRow[] = [
  {
    run_id: "run-1",
    agent_name: "atlas-default",
    status: "completed",
    model: "gpt-5",
    tokens: 12_345,
    latency_ms: 4_200,
    started_at: "2026-05-10T10:00:00Z",
  },
  {
    run_id: "run-2",
    agent_name: "atlas-research",
    status: "running",
    model: "claude-opus-4-7",
    tokens: 2_100,
    latency_ms: 800,
    started_at: "2026-05-11T11:00:00Z",
  },
  {
    run_id: "run-3",
    agent_name: "atlas-default",
    status: "failed",
    model: "gpt-5",
    tokens: 500,
    latency_ms: 200,
    started_at: "2026-05-09T09:00:00Z",
  },
];

function renderWith(handler: RequestHandler): {
  router: Router<ArtifactRoute>;
} {
  const router = makeRouter();
  render(
    <TransportProvider transport={makeTransport(handler)}>
      <RouterProvider router={router}>
        <AgentsDestination />
      </RouterProvider>
    </TransportProvider>,
  );
  return { router };
}

describe("AgentsDestination", () => {
  it("renders the skeleton while the initial request is in flight", async () => {
    let resolve!: (v: { runs: readonly AgentRunRow[] }) => void;
    const pending = new Promise<{ runs: readonly AgentRunRow[] }>((r) => {
      resolve = r;
    });
    renderWith(() => pending);
    expect(screen.getAllByTestId("agents-skeleton-row").length).toBeGreaterThan(
      0,
    );
    await act(async () => {
      resolve({ runs: [] });
      await pending;
    });
  });

  it("renders rows once the request resolves", async () => {
    renderWith(async () => ({ runs: SAMPLE_RUNS }));
    await waitFor(() => {
      expect(screen.getAllByTestId("agents-row")).toHaveLength(3);
    });
    expect(screen.getByText("atlas-research")).toBeInTheDocument();
    expect(screen.getAllByText("gpt-5").length).toBeGreaterThan(0);
  });

  it("renders the empty state when the runs list is empty", async () => {
    renderWith(async () => ({ runs: [] }));
    await waitFor(() => {
      expect(screen.getByTestId("agents-empty")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("agents-row")).toBeNull();
  });

  it("renders the error state and recovers on retry", async () => {
    let calls = 0;
    renderWith(async () => {
      calls += 1;
      if (calls === 1) throw new Error("network down");
      return { runs: SAMPLE_RUNS };
    });
    await waitFor(() => {
      expect(screen.getByTestId("agents-error")).toBeInTheDocument();
    });
    expect(screen.getByText("network down")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("agents-retry"));
    await waitFor(() => {
      expect(screen.getAllByTestId("agents-row")).toHaveLength(3);
    });
    expect(calls).toBe(2);
  });

  it("clicking a row navigates with {kind:'run', runId}", async () => {
    const { router } = renderWith(async () => ({ runs: SAMPLE_RUNS }));
    await waitFor(() => {
      expect(screen.getAllByTestId("agents-row")).toHaveLength(3);
    });
    const row = screen.getAllByTestId("agents-row")[0];
    fireEvent.click(row);
    expect(router.navigate).toHaveBeenCalledWith({
      kind: "run",
      runId: expect.any(String),
    });
  });

  it("applies the status filter through the request query and the row view", async () => {
    const requests: TypedRequest[] = [];
    renderWith(async (req) => {
      requests.push(req);
      const status = req.query?.status;
      if (status === "running") {
        return { runs: SAMPLE_RUNS.filter((r) => r.status === "running") };
      }
      return { runs: SAMPLE_RUNS };
    });
    await waitFor(() => {
      expect(screen.getAllByTestId("agents-row")).toHaveLength(3);
    });
    fireEvent.change(screen.getByTestId("agents-status-filter"), {
      target: { value: "running" },
    });
    await waitFor(() => {
      const rows = screen.getAllByTestId("agents-row");
      expect(rows).toHaveLength(1);
      expect(rows[0]).toHaveAttribute("data-run-id", "run-2");
    });
    expect(requests.at(-1)?.query?.status).toBe("running");
  });

  it("clicking a column header toggles sort and re-orders rows", async () => {
    renderWith(async () => ({ runs: SAMPLE_RUNS }));
    await waitFor(() => {
      expect(screen.getAllByTestId("agents-row")).toHaveLength(3);
    });
    fireEvent.click(screen.getByTestId("agents-sort-tokens"));
    let rows = screen.getAllByTestId("agents-row");
    expect(rows.map((r) => r.getAttribute("data-run-id"))).toEqual([
      "run-3",
      "run-2",
      "run-1",
    ]);
    fireEvent.click(screen.getByTestId("agents-sort-tokens"));
    rows = screen.getAllByTestId("agents-row");
    expect(rows.map((r) => r.getAttribute("data-run-id"))).toEqual([
      "run-1",
      "run-2",
      "run-3",
    ]);
  });
});
