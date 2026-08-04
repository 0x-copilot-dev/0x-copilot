// ThreadSwitcherHost — the cockpit's ONE binding of the shared chats archive.
//
// The host is two lines of JSX, so the assertions worth writing are about the
// two properties the file exists to hold rather than about rendering:
//
//   * the scope reaches the CONTROLLER (it lands on the wire as
//     `filter[project_id]` on every bucket request), not just the panel — a
//     panel-level filter over a keyset page silently drops rows;
//   * the hook stays mounted across open/close, so toggling the panel neither
//     refetches nor re-subscribes (NFR-1.1).
//
// The transport fake follows `useChatsArchive.test.tsx` rather than the heavier
// cockpit `FakeTransport`: this component's only server conversation is the
// three-bucket list plus its stream.

import type { ConversationId, ProjectId } from "@0x-copilot/api-types";
import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";

import { TransportProvider } from "../../providers/TransportProvider";
import type { ThreadScopeOption } from "../../shell/ThreadSwitcher";

import {
  ThreadSwitcherHost,
  type ThreadSwitcherHostProps,
} from "./ThreadSwitcherHost";

const CONVERSATIONS_PATH = "/v1/agent/conversations";
const PROJECT_FILTER_KEY = "filter[project_id]";

const ACME = "p-acme" as ProjectId;
const ATLAS = "p-atlas" as ProjectId;

const SCOPES: ReadonlyArray<ThreadScopeOption> = [
  { id: ACME, name: "Acme renewal", colorHue: 210 },
  { id: ATLAS, name: "Atlas launch", colorHue: 140 },
];

const CAPABILITIES: TransportCapabilities = {
  substrate: "web",
  nativeSecretStorage: false,
  fileSystemAccess: false,
  clipboardWrite: false,
  openExternal: false,
};

interface CapturedSub {
  readonly path: string;
  closed: boolean;
}

class ArchiveTransport implements Transport {
  readonly requests: TypedRequest[] = [];
  readonly subs: CapturedSub[] = [];

  async request<TRes>(request: TypedRequest): Promise<TRes> {
    this.requests.push(request);
    return {
      conversations: [],
      next_cursor: null,
      has_more: false,
    } as TRes;
  }

  subscribeServerSentEvents(options: SseSubscribeOptions): SseSubscription {
    const sub: CapturedSub = { path: options.path, closed: false };
    this.subs.push(sub);
    return {
      close: () => {
        sub.closed = true;
      },
    };
  }

  getSession(): Session {
    return { bearer: null };
  }

  capabilities(): TransportCapabilities {
    return CAPABILITIES;
  }

  /** Every bucket list request, in order. */
  listCalls(): TypedRequest[] {
    return this.requests.filter(
      (request) =>
        request.path === CONVERSATIONS_PATH && request.method === "GET",
    );
  }

  /** The project each bucket request carried (`undefined` = unscoped). */
  scopes(): unknown[] {
    return this.listCalls().map(
      (request) => request.query?.[PROJECT_FILTER_KEY],
    );
  }

  openSubs(): CapturedSub[] {
    return this.subs.filter((sub) => !sub.closed);
  }
}

function host(
  transport: Transport,
  props: Partial<ThreadSwitcherHostProps> = {},
): ReactElement {
  return (
    <TransportProvider transport={transport}>
      <ThreadSwitcherHost
        open
        variant="docked"
        activeConversationId={"conv-1" as ConversationId}
        onOpenConversation={vi.fn()}
        {...props}
      />
    </TransportProvider>
  );
}

describe("ThreadSwitcherHost", () => {
  it("passes the scope into the controller, so every bucket request is filtered", async () => {
    const transport = new ArchiveTransport();
    render(host(transport, { scope: ACME, scopeOptions: SCOPES }));

    await waitFor(() => expect(transport.listCalls()).toHaveLength(3));
    // All three buckets, not just the visible one: the scope belongs to the
    // fetch, so a bucket the user has not scrolled to is already narrowed.
    expect(transport.scopes()).toEqual([ACME, ACME, ACME]);
  });

  it("sends no project filter when unscoped (undefined and null both mean All)", async () => {
    for (const scope of [undefined, null] as const) {
      const transport = new ArchiveTransport();
      const view = render(host(transport, { scope }));
      await waitFor(() => expect(transport.listCalls()).toHaveLength(3));
      // ABSENT, not present-and-undefined: "no filter" is how the endpoint
      // spells "everything", and `filter[project_id]=` matches nothing.
      for (const request of transport.listCalls()) {
        expect(Object.keys(request.query ?? {})).not.toContain(
          PROJECT_FILTER_KEY,
        );
      }
      view.unmount();
    }
  });

  it("re-fetches under the new scope when the host changes it", async () => {
    const transport = new ArchiveTransport();
    const view = render(host(transport, { scope: null, scopeOptions: SCOPES }));
    await waitFor(() => expect(transport.listCalls()).toHaveLength(3));

    view.rerender(host(transport, { scope: ATLAS, scopeOptions: SCOPES }));

    await waitFor(() => expect(transport.listCalls()).toHaveLength(6));
    expect(transport.scopes().slice(3)).toEqual([ATLAS, ATLAS, ATLAS]);
  });

  it("forwards the scope props to the panel", async () => {
    const onScopeChange = vi.fn();
    const transport = new ArchiveTransport();
    render(
      host(transport, { scope: ACME, scopeOptions: SCOPES, onScopeChange }),
    );

    // The trigger names the active scope…
    const trigger = await screen.findByTestId("thread-switcher-scope-trigger");
    expect(trigger.textContent).toContain("Acme renewal");

    // …the menu carries every option the host supplied…
    fireEvent.click(trigger);
    expect(
      screen
        .getAllByTestId("thread-switcher-scope-option")
        .map((option) => option.getAttribute("data-project-id")),
    ).toEqual([ACME, ATLAS]);

    // …and picking one reports to the HOST. The host owns the scope; the
    // cockpit must not quietly hold a second copy of it.
    fireEvent.click(screen.getByTestId("thread-switcher-scope-all"));
    expect(onScopeChange).toHaveBeenCalledWith(null);
  });

  it("renders no scope control when the host supplies no options", async () => {
    const transport = new ArchiveTransport();
    render(host(transport));
    await screen.findByTestId("thread-switcher-title");
    expect(screen.queryByTestId("thread-switcher-scope")).toBeNull();
  });

  it("keeps ONE subscription and one fetch across open/close (NFR-1.1)", async () => {
    const transport = new ArchiveTransport();
    const view = render(host(transport, { scope: ACME }));
    await waitFor(() => expect(transport.openSubs()).toHaveLength(1));

    // Closing drops the PANEL, never the hook: the host stays mounted, so the
    // subscription and the loaded buckets survive.
    view.rerender(host(transport, { open: false, scope: ACME }));
    expect(screen.queryByTestId("thread-switcher-title")).toBeNull();
    expect(transport.openSubs()).toHaveLength(1);

    view.rerender(host(transport, { open: true, scope: ACME }));
    await screen.findByTestId("thread-switcher-title");
    // Re-opening costs nothing — no second stream, no second three-bucket load.
    expect(transport.openSubs()).toHaveLength(1);
    expect(transport.listCalls()).toHaveLength(3);
  });
});
