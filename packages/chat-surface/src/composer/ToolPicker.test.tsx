import { afterEach, describe, expect, it, vi } from "vitest";
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
import { ToolPicker, type ToolDescriptor } from "./ToolPicker";

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

const SAMPLE_TOOLS: ReadonlyArray<ToolDescriptor> = [
  { name: "gmail.draft.create", label: "Gmail draft" },
  { name: "sheets.cell.set", label: "Set cell", description: "Mutate a cell" },
];

describe("ToolPicker", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders nothing when closed", () => {
    const { transport } = makeTransport(() => Promise.resolve({ tools: [] }));
    render(
      withTransport(
        transport,
        <ToolPicker
          open={false}
          selectedTools={[]}
          onToggle={() => {}}
          onClose={() => {}}
        />,
      ),
    );
    expect(screen.queryByTestId("tool-picker")).not.toBeInTheDocument();
  });

  it("fetches /v1/mcp/tools on first open and renders the list", async () => {
    const { transport, record } = makeTransport(() =>
      Promise.resolve({ tools: SAMPLE_TOOLS }),
    );
    render(
      withTransport(
        transport,
        <ToolPicker
          open={true}
          selectedTools={[]}
          onToggle={() => {}}
          onClose={() => {}}
        />,
      ),
    );
    await waitFor(() => {
      expect(screen.getByText("Gmail draft")).toBeInTheDocument();
    });
    expect(screen.getByText("Set cell")).toBeInTheDocument();
    expect(record.calls).toHaveLength(1);
    expect(record.calls[0]).toMatchObject({
      method: "GET",
      path: "/v1/mcp/tools",
    });
  });

  it("renders an empty-state when the catalog is empty", async () => {
    const { transport } = makeTransport(() => Promise.resolve({ tools: [] }));
    render(
      withTransport(
        transport,
        <ToolPicker
          open={true}
          selectedTools={[]}
          onToggle={() => {}}
          onClose={() => {}}
        />,
      ),
    );
    await waitFor(() => {
      expect(screen.getByTestId("tool-picker-empty")).toBeInTheDocument();
    });
  });

  it("renders an error state when the request rejects", async () => {
    const { transport } = makeTransport(() =>
      Promise.reject(new Error("network down")),
    );
    render(
      withTransport(
        transport,
        <ToolPicker
          open={true}
          selectedTools={[]}
          onToggle={() => {}}
          onClose={() => {}}
        />,
      ),
    );
    await waitFor(() => {
      expect(screen.getByTestId("tool-picker-error")).toBeInTheDocument();
    });
  });

  it("fires onToggle with the tool name when a row is clicked", async () => {
    const { transport } = makeTransport(() =>
      Promise.resolve({ tools: SAMPLE_TOOLS }),
    );
    const onToggle = vi.fn();
    render(
      withTransport(
        transport,
        <ToolPicker
          open={true}
          selectedTools={[]}
          onToggle={onToggle}
          onClose={() => {}}
        />,
      ),
    );
    const row = await screen.findByTestId("tool-picker-row-gmail.draft.create");
    fireEvent.click(row);
    expect(onToggle).toHaveBeenCalledWith("gmail.draft.create");
  });

  it("marks selected tools with aria-selected=true", async () => {
    const { transport } = makeTransport(() =>
      Promise.resolve({ tools: SAMPLE_TOOLS }),
    );
    render(
      withTransport(
        transport,
        <ToolPicker
          open={true}
          selectedTools={["gmail.draft.create"]}
          onToggle={() => {}}
          onClose={() => {}}
        />,
      ),
    );
    const row = await screen.findByTestId("tool-picker-row-gmail.draft.create");
    expect(row).toHaveAttribute("aria-selected", "true");
    const otherRow = screen.getByTestId("tool-picker-row-sheets.cell.set");
    expect(otherRow).toHaveAttribute("aria-selected", "false");
  });

  it("fires onClose when the close button is clicked", async () => {
    const { transport } = makeTransport(() =>
      Promise.resolve({ tools: SAMPLE_TOOLS }),
    );
    const onClose = vi.fn();
    render(
      withTransport(
        transport,
        <ToolPicker
          open={true}
          selectedTools={[]}
          onToggle={() => {}}
          onClose={onClose}
        />,
      ),
    );
    await screen.findByText("Gmail draft");
    fireEvent.click(screen.getByTestId("tool-picker-close"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("does not refetch when re-opened after a successful first load", async () => {
    const { transport, record } = makeTransport(() =>
      Promise.resolve({ tools: SAMPLE_TOOLS }),
    );
    const { rerender } = render(
      withTransport(
        transport,
        <ToolPicker
          open={true}
          selectedTools={[]}
          onToggle={() => {}}
          onClose={() => {}}
        />,
      ),
    );
    await screen.findByText("Gmail draft");
    rerender(
      withTransport(
        transport,
        <ToolPicker
          open={false}
          selectedTools={[]}
          onToggle={() => {}}
          onClose={() => {}}
        />,
      ),
    );
    rerender(
      withTransport(
        transport,
        <ToolPicker
          open={true}
          selectedTools={[]}
          onToggle={() => {}}
          onClose={() => {}}
        />,
      ),
    );
    expect(record.calls).toHaveLength(1);
  });
});
