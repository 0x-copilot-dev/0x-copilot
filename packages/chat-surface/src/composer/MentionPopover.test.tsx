import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";

import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@enterprise-search/chat-transport";

import { TransportProvider } from "../providers/TransportProvider";
import { MentionPopover, type MentionCandidate } from "./MentionPopover";

interface StubRecord {
  readonly calls: TypedRequest[];
}

function makeTransport(resolver: (req: TypedRequest) => Promise<unknown>): {
  transport: Transport;
  record: StubRecord;
} {
  const record: StubRecord = { calls: [] };
  const transport: Transport = {
    request: <TRes,>(req: TypedRequest): Promise<TRes> => {
      record.calls.push(req);
      return resolver(req) as Promise<TRes>;
    },
    subscribeServerSentEvents: (
      _opts: SseSubscribeOptions,
    ): SseSubscription => ({
      close: () => {},
    }),
    getSession: (): Session => ({ bearer: null }),
    capabilities: (): TransportCapabilities => ({
      substrate: "web",
      nativeSecretStorage: false,
      fileSystemAccess: false,
      clipboardWrite: false,
      openExternal: false,
    }),
  };
  return { transport, record };
}

function withTransport(transport: Transport, children: ReactNode): ReactNode {
  return (
    <TransportProvider transport={transport}>{children}</TransportProvider>
  );
}

const SAMPLE: ReadonlyArray<MentionCandidate> = [
  { slug: "tim.research", label: "Tim Research", kind: "skill" },
  { slug: "sarah", label: "Sarah Acme", kind: "user" },
];

describe("MentionPopover", () => {
  it("renders nothing when closed", () => {
    const { transport } = makeTransport(() =>
      Promise.resolve({ candidates: SAMPLE }),
    );
    render(
      withTransport(
        transport,
        <MentionPopover
          open={false}
          query=""
          onSelect={() => {}}
          onClose={() => {}}
        />,
      ),
    );
    expect(screen.queryByTestId("mention-popover")).not.toBeInTheDocument();
  });

  it("issues a GET /v1/mentions request with the query when opened", async () => {
    const { transport, record } = makeTransport(() =>
      Promise.resolve({ candidates: SAMPLE }),
    );
    render(
      withTransport(
        transport,
        <MentionPopover
          open={true}
          query="ti"
          onSelect={() => {}}
          onClose={() => {}}
        />,
      ),
    );
    await waitFor(() => {
      expect(screen.getByText("@Tim Research")).toBeInTheDocument();
    });
    expect(record.calls).toHaveLength(1);
    expect(record.calls[0]).toMatchObject({
      method: "GET",
      path: "/v1/mentions",
      query: { q: "ti" },
    });
  });

  it("refetches when the query changes", async () => {
    let returned: ReadonlyArray<MentionCandidate> = SAMPLE;
    const { transport, record } = makeTransport(() =>
      Promise.resolve({ candidates: returned }),
    );
    const { rerender } = render(
      withTransport(
        transport,
        <MentionPopover
          open={true}
          query="ti"
          onSelect={() => {}}
          onClose={() => {}}
        />,
      ),
    );
    await screen.findByText("@Tim Research");
    returned = [{ slug: "sarah", label: "Sarah Acme", kind: "user" }];
    rerender(
      withTransport(
        transport,
        <MentionPopover
          open={true}
          query="sa"
          onSelect={() => {}}
          onClose={() => {}}
        />,
      ),
    );
    await waitFor(() => {
      expect(record.calls.length).toBe(2);
    });
    expect(record.calls[1]?.query).toEqual({ q: "sa" });
  });

  it("renders the empty state when there are no matches", async () => {
    const { transport } = makeTransport(() =>
      Promise.resolve({ candidates: [] }),
    );
    render(
      withTransport(
        transport,
        <MentionPopover
          open={true}
          query="zzz"
          onSelect={() => {}}
          onClose={() => {}}
        />,
      ),
    );
    await waitFor(() => {
      expect(screen.getByTestId("mention-empty")).toBeInTheDocument();
    });
  });

  it("renders an error state when the request rejects", async () => {
    const { transport } = makeTransport(() =>
      Promise.reject(new Error("nope")),
    );
    render(
      withTransport(
        transport,
        <MentionPopover
          open={true}
          query="x"
          onSelect={() => {}}
          onClose={() => {}}
        />,
      ),
    );
    await waitFor(() => {
      expect(screen.getByTestId("mention-error")).toBeInTheDocument();
    });
  });

  it("fires onSelect with the candidate and onClose when a row is clicked", async () => {
    const { transport } = makeTransport(() =>
      Promise.resolve({ candidates: SAMPLE }),
    );
    const onSelect = vi.fn();
    const onClose = vi.fn();
    render(
      withTransport(
        transport,
        <MentionPopover
          open={true}
          query="ti"
          onSelect={onSelect}
          onClose={onClose}
        />,
      ),
    );
    const row = await screen.findByTestId("mention-row-tim.research");
    fireEvent.click(row);
    expect(onSelect).toHaveBeenCalledWith(SAMPLE[0]);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("positions the panel using anchorRect when provided", async () => {
    const { transport } = makeTransport(() =>
      Promise.resolve({ candidates: SAMPLE }),
    );
    render(
      withTransport(
        transport,
        <MentionPopover
          open={true}
          query="ti"
          onSelect={() => {}}
          onClose={() => {}}
          anchorRect={{ top: 100, left: 50 }}
        />,
      ),
    );
    const panel = await screen.findByTestId("mention-popover");
    expect(panel).toHaveStyle({
      position: "absolute",
      top: "100px",
      left: "50px",
    });
  });
});
