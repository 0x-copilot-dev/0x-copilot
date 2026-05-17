import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@enterprise-search/chat-transport";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { TransportProvider } from "../../providers/TransportProvider";
import {
  MemoryDestination,
  type Memory,
  type MemoryType,
} from "./MemoryDestination";

const CAPS: TransportCapabilities = {
  substrate: "web",
  nativeSecretStorage: false,
  fileSystemAccess: false,
  clipboardWrite: false,
  openExternal: false,
};

const SESSION: Session = { bearer: null };

interface CallLog {
  readonly path: string;
  readonly type: MemoryType | undefined;
}

function makeTransport(
  handler: (type: MemoryType | undefined) => Promise<unknown>,
): { readonly transport: Transport; readonly calls: CallLog[] } {
  const calls: CallLog[] = [];
  const transport: Transport = {
    request: <TRes,>(req: TypedRequest): Promise<TRes> => {
      const type =
        typeof req.query?.type === "string"
          ? (req.query.type as MemoryType)
          : undefined;
      calls.push({ path: req.path, type });
      return handler(type).then((v) => v as TRes);
    },
    subscribeServerSentEvents: (
      _opts: SseSubscribeOptions,
    ): SseSubscription => ({
      close: () => undefined,
    }),
    getSession: () => SESSION,
    capabilities: () => CAPS,
  };
  return { transport, calls };
}

function memoryFixture(over: Partial<Memory> = {}): Memory {
  return {
    id: "mem_1",
    type: "user",
    title: "Prefers TypeScript over Python",
    description: "Sarah codes mostly in TypeScript on the desktop app.",
    lastUpdatedIso: new Date(Date.now() - 1000 * 60 * 60).toISOString(),
    pinned: false,
    ...over,
  };
}

describe("MemoryDestination", () => {
  it("renders a loading skeleton before the user tab resolves", () => {
    const { transport } = makeTransport(() => new Promise(() => undefined));
    render(
      <TransportProvider transport={transport}>
        <MemoryDestination />
      </TransportProvider>,
    );
    expect(screen.getByTestId("memory-loading")).toBeInTheDocument();
  });

  it("renders memory cards sorted with pinned first", async () => {
    const { transport } = makeTransport(async (type) => {
      if (type !== "user") return { memories: [] };
      return {
        memories: [
          memoryFixture({ id: "a", title: "Alpha", pinned: false }),
          memoryFixture({ id: "b", title: "Bravo", pinned: true }),
          memoryFixture({ id: "c", title: "Charlie", pinned: false }),
        ],
      };
    });
    render(
      <TransportProvider transport={transport}>
        <MemoryDestination />
      </TransportProvider>,
    );
    const list = await screen.findByTestId("memory-list");
    const titles = within(list)
      .getAllByRole("heading", { level: 2 })
      .map((h) => h.textContent);
    expect(titles).toEqual(["Bravo", "Alpha", "Charlie"]);
  });

  it("renders the empty-state card when no memories exist", async () => {
    const { transport } = makeTransport(async () => ({ memories: [] }));
    render(
      <TransportProvider transport={transport}>
        <MemoryDestination />
      </TransportProvider>,
    );
    expect(await screen.findByTestId("memory-empty")).toBeInTheDocument();
  });

  it("renders an error sentinel when the transport rejects", async () => {
    const { transport } = makeTransport(async () => {
      throw new Error("memory service down");
    });
    render(
      <TransportProvider transport={transport}>
        <MemoryDestination />
      </TransportProvider>,
    );
    const errorCard = await screen.findByTestId("memory-error");
    expect(errorCard).toHaveTextContent("memory service down");
  });

  it("filters memories by the search query", async () => {
    const user = userEvent.setup();
    const { transport } = makeTransport(async () => ({
      memories: [
        memoryFixture({
          id: "1",
          title: "TypeScript preferred",
          description: "",
        }),
        memoryFixture({ id: "2", title: "Python only", description: "" }),
      ],
    }));
    render(
      <TransportProvider transport={transport}>
        <MemoryDestination />
      </TransportProvider>,
    );
    await screen.findByTestId("memory-list");
    const search = screen.getByLabelText("Search memories");
    await user.type(search, "python");
    const list = screen.getByTestId("memory-list");
    const titles = within(list)
      .getAllByRole("heading", { level: 2 })
      .map((h) => h.textContent);
    expect(titles).toEqual(["Python only"]);
  });

  it("shows a no-match card when the search excludes every memory", async () => {
    const user = userEvent.setup();
    const { transport } = makeTransport(async () => ({
      memories: [memoryFixture({ title: "TypeScript", description: "lang" })],
    }));
    render(
      <TransportProvider transport={transport}>
        <MemoryDestination />
      </TransportProvider>,
    );
    await screen.findByTestId("memory-list");
    await user.type(screen.getByLabelText("Search memories"), "rust");
    expect(
      await screen.findByTestId("memory-empty-search"),
    ).toBeInTheDocument();
  });

  it("fetches a different type when a different tab is selected", async () => {
    const user = userEvent.setup();
    const { transport, calls } = makeTransport(async (type) => ({
      memories: [
        memoryFixture({
          id: `id-${type}`,
          type: type ?? "user",
          title: `t-${type}`,
        }),
      ],
    }));
    render(
      <TransportProvider transport={transport}>
        <MemoryDestination />
      </TransportProvider>,
    );
    await screen.findByTestId("memory-list");
    expect(calls.map((c) => c.type)).toEqual(["user"]);

    await user.click(screen.getByRole("tab", { name: /project memories/i }));
    await waitFor(() => {
      expect(calls.map((c) => c.type)).toEqual(["user", "project"]);
    });
    expect(screen.getByText("t-project")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /reference memories/i }));
    await waitFor(() => {
      expect(calls.map((c) => c.type)).toEqual([
        "user",
        "project",
        "reference",
      ]);
    });
  });

  it("does not refetch when switching back to a cached tab", async () => {
    const user = userEvent.setup();
    const { transport, calls } = makeTransport(async (type) => ({
      memories: [
        memoryFixture({
          id: `id-${type}`,
          type: type ?? "user",
          title: `t-${type}`,
        }),
      ],
    }));
    render(
      <TransportProvider transport={transport}>
        <MemoryDestination />
      </TransportProvider>,
    );
    await screen.findByTestId("memory-list");

    await user.click(screen.getByRole("tab", { name: /project memories/i }));
    await waitFor(() => expect(calls.length).toBe(2));

    await user.click(screen.getByRole("tab", { name: /user memories/i }));
    expect(calls.length).toBe(2);
  });

  it("invokes onTogglePin and onDelete when the row actions are clicked", async () => {
    const user = userEvent.setup();
    const onTogglePin = vi.fn();
    const onDelete = vi.fn();
    const { transport } = makeTransport(async () => ({
      memories: [memoryFixture({ pinned: false })],
    }));
    render(
      <TransportProvider transport={transport}>
        <MemoryDestination onTogglePin={onTogglePin} onDelete={onDelete} />
      </TransportProvider>,
    );
    await screen.findByTestId("memory-list");
    await user.click(screen.getByRole("button", { name: /pin memory/i }));
    expect(onTogglePin).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: /delete memory/i }));
    expect(onDelete).toHaveBeenCalledTimes(1);
  });
});
