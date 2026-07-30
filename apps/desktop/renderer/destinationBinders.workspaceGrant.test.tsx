// @vitest-environment jsdom
//
// The desktop host's half of the folder-grant reconciliation, end to end through
// the real cockpit: a run pauses on a path it has no grant for, the user is ASKED,
// and the run resumes only after a grant actually exists.
//
// This is the wire that was missing. `capability.request-folder-grant` has been
// implemented in main since AC5 slice 1 with zero callers, so an ungranted read
// fell through to the agent's virtual memory filesystem and reported a real
// folder as empty. The assertions here are therefore about the SEQUENCE, not just
// the render: no grant → no decision POST; grant → exactly one approve.
import {
  KeyValueStoreProvider,
  RouterProvider,
  TransportProvider,
  type ArtifactRoute,
  type ConversationId,
  type KeyValueStore,
  type Router,
} from "@0x-copilot/chat-surface";
import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";
import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { type ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CAPABILITY_CHANNELS } from "../main/capabilities/channels";
import type { WindowBridge } from "../preload/window-bridge-types";
import { RunBinder } from "./destinationBinders";

// globals: false in the desktop vitest config → register cleanup explicitly.
afterEach(() => {
  cleanup();
  delete (globalThis.window as unknown as { bridge?: WindowBridge }).bridge;
});

// jsdom ships no IntersectionObserver; the composer's caret path wants one.
class NoopIntersectionObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
  takeRecords(): unknown[] {
    return [];
  }
}
if (typeof globalThis.IntersectionObserver === "undefined") {
  (
    globalThis as unknown as { IntersectionObserver: unknown }
  ).IntersectionObserver = NoopIntersectionObserver;
}

const CONVERSATION_ID = "conv-grant" as ConversationId;
const RUN_ID = "run-grant";
const APPROVAL_ID = "appr-downloads";
// The folder the model asked for, host-absolute and verbatim — the one string on
// this card the user has to be able to recognise before saying yes.
const ASKED_PATH = "/Users/ada/Downloads";

interface Recorder {
  readonly calls: TypedRequest[];
}

function payloadFor(path: string): Record<string, unknown> {
  if (path.includes("/v1/skills")) return { skills: [] };
  if (path.includes("/v1/mcp/servers")) return { servers: [] };
  if (path.includes("/v1/settings/provider-keys")) {
    return { keys: [{ provider: "openai" }] };
  }
  if (path.includes("/v1/local-models")) return { models: [] };
  if (path.includes("/v1/agent/workspace/defaults")) {
    return { default_model: { provider: "openai", model_name: "gpt-4o" } };
  }
  if (path.includes("/messages")) return { messages: [] };
  if (path.endsWith(`/conversations/${CONVERSATION_ID}/runs`)) {
    return { runs: [{ run_id: RUN_ID, status: "running" }] };
  }
  // The conversation head is what binds the cockpit to the live run.
  if (path.endsWith(`/conversations/${CONVERSATION_ID}`)) {
    return { latest_run_id: RUN_ID };
  }
  if (path.includes("/v1/agent/conversations")) return { conversations: [] };
  return {};
}

/** The run's SSE tail, held so the test can push the interrupt frame. */
interface Stream {
  push(envelope: RuntimeEventEnvelope): Promise<void>;
}

function cockpitTransport(recorder: Recorder): {
  readonly transport: Transport;
  readonly stream: Stream;
} {
  let opts: SseSubscribeOptions | null = null;
  const transport: Transport = {
    request: <TRes,>(req: TypedRequest): Promise<TRes> => {
      recorder.calls.push(req);
      return Promise.resolve(payloadFor(req.path) as unknown as TRes);
    },
    subscribeServerSentEvents: (o: SseSubscribeOptions): SseSubscription => {
      opts = o;
      return { close: () => undefined };
    },
    getSession: (): Session => ({ bearer: null }),
    capabilities: (): TransportCapabilities => ({
      substrate: "desktop-webview",
      nativeSecretStorage: true,
      fileSystemAccess: false,
      clipboardWrite: false,
      openExternal: false,
    }),
  };
  const stream: Stream = {
    async push(envelope) {
      await waitFor(() => {
        expect(opts).not.toBeNull();
      });
      await act(async () => {
        opts?.onMessage?.(JSON.stringify(envelope));
      });
    },
  };
  return { transport, stream };
}

/**
 * The interrupt that carries a folder ask: any approval kind plus the
 * `workspace_grant` payload block (see `approvals/presentation.ts` — the card is
 * keyed on the block, not on a kind).
 */
function folderAsk(): RuntimeEventEnvelope {
  return {
    event_id: "e-1",
    run_id: RUN_ID,
    conversation_id: CONVERSATION_ID,
    sequence_no: 1,
    event_type: "approval_requested",
    activity_kind: "approval",
    payload: {
      approval_id: APPROVAL_ID,
      approval_kind: "tool_call",
      display_name: "Read Downloads",
      workspace_grant: {
        path: ASKED_PATH,
        mode: "read_only",
        reason: "to list the files you asked about",
      },
    },
    created_at: new Date(1716000000000).toISOString(),
  } as RuntimeEventEnvelope;
}

function fakeRouter(): Router<ArtifactRoute | null> {
  return {
    current: () => null,
    navigate: () => undefined,
    subscribe: () => () => undefined,
  };
}

function fakeKeyValueStore(): KeyValueStore {
  const map = new Map<string, string>();
  return {
    get: (key) => map.get(key) ?? null,
    set: (key, value) => {
      if (value === null) map.delete(key);
      else map.set(key, value);
    },
    keys: (prefix) =>
      [...map.keys()].filter(
        (key) => prefix === undefined || key.startsWith(prefix),
      ),
  };
}

/** The Electron bridge, answering the capability channels however the test says. */
function installBridge(answers: Record<string, unknown>): {
  readonly invoke: ReturnType<typeof vi.fn>;
} {
  const invoke = vi.fn(async (channel: string, _payload: unknown) => {
    const answer = answers[channel];
    if (typeof answer === "function") return (answer as () => unknown)();
    return answer;
  });
  (globalThis.window as unknown as { bridge?: WindowBridge }).bridge = {
    ipc: {
      invoke: invoke as unknown as WindowBridge["ipc"]["invoke"],
      on: () => () => {},
    },
  };
  return { invoke };
}

function renderCockpit(transport: Transport): HTMLElement {
  const ui: ReactElement = (
    <TransportProvider transport={transport}>
      <KeyValueStoreProvider store={fakeKeyValueStore()}>
        <RouterProvider router={fakeRouter()}>
          <RunBinder conversationId={CONVERSATION_ID} />
        </RouterProvider>
      </KeyValueStoreProvider>
    </TransportProvider>
  );
  return render(ui).container;
}

function decisionPosts(recorder: Recorder): readonly TypedRequest[] {
  return recorder.calls.filter(
    (c) =>
      c.method === "POST" &&
      c.path === `/v1/agent/approvals/${APPROVAL_ID}/decision`,
  );
}

function card(container: HTMLElement): HTMLElement | null {
  return container.querySelector<HTMLElement>(
    `[data-testid='tc-chat-grant-${APPROVAL_ID}']`,
  );
}

function grantButton(container: HTMLElement): HTMLButtonElement | null {
  return container.querySelector<HTMLButtonElement>(
    `[data-testid='tc-chat-workspace-grant-approve-${APPROVAL_ID}']`,
  );
}

describe("desktop Run cockpit — the mid-run folder ask", () => {
  it("asks for the folder, grants it through the native picker, THEN resumes the run", async () => {
    const recorder: Recorder = { calls: [] };
    const { invoke } = installBridge({
      [CAPABILITY_CHANNELS.requestFolderGrant]: {
        grantId: "22222222-2222-4222-8222-222222222222",
        mode: "read_only",
        label: "Downloads",
        status: "active",
      },
      [CAPABILITY_CHANNELS.listGrants]: [],
    });
    const { transport, stream } = cockpitTransport(recorder);
    const container = renderCockpit(transport);

    await stream.push(folderAsk());

    // The ask is a QUESTION, and it names the folder in full.
    await waitFor(() => expect(card(container)).not.toBeNull());
    expect(
      container.querySelector("[data-testid='wg-path']")?.textContent,
    ).toBe(ASKED_PATH);
    // Answerable, because the desktop host wired the port. (Without it the same
    // card renders inert — the web contract; see the next test for the desktop
    // failure mode.)
    const grant = grantButton(container);
    expect(grant).not.toBeNull();
    expect(grant?.disabled).toBe(false);

    // Nothing has been approved yet — the run is still paused on the ask.
    expect(decisionPosts(recorder)).toHaveLength(0);

    await act(async () => {
      grant?.click();
    });

    // The picker was opened over the capability channel, carrying the requested
    // mode and NOT the path (main owns the selection; the dialog is the consent).
    await waitFor(() => {
      expect(invoke).toHaveBeenCalledWith(
        CAPABILITY_CHANNELS.requestFolderGrant,
        { mode: "read_only" },
      );
    });
    const grantPayloads = invoke.mock.calls
      .filter(([channel]) => channel === CAPABILITY_CHANNELS.requestFolderGrant)
      .map(([, payload]) => JSON.stringify(payload));
    for (const payload of grantPayloads) {
      expect(payload).not.toContain("Downloads");
      expect(payload).not.toContain("/Users");
    }

    // …and ONLY now does the run resume, with a single approve.
    await waitFor(() => {
      expect(decisionPosts(recorder)).toHaveLength(1);
    });
    expect(decisionPosts(recorder)[0]?.body).toEqual({ decision: "approved" });
    // The ask is settled, not left hanging. It used to be observed via an
    // approved RECEIPT, but a resolved approval is no longer pinned above the
    // composer: by then the run has continued and its result is in the
    // transcript, so the receipt carried no information and only cost height.
    // The invariant it was really protecting — the card gives way and nothing
    // is left dangling — is what is asserted now.
    await waitFor(() => {
      expect(card(container)).toBeNull();
    });
    expect(
      container.querySelector(
        `[data-testid='tc-chat-approval-receipt-${APPROVAL_ID}']`,
      ),
    ).toBeNull();
  });

  it("shows the failure and leaves the run paused when the capability is opted out", async () => {
    // The honoured opt-out (`RUNTIME_ENABLE_DESKTOP_FILESYSTEM=0`): main leaves
    // `capabilityService` null, so the channel has no handler and Electron
    // rejects the invoke. The user must see WHY; the run must not proceed as if
    // the folder were readable.
    const recorder: Recorder = { calls: [] };
    installBridge({
      [CAPABILITY_CHANNELS.requestFolderGrant]: () => {
        throw new Error(
          "No handler registered for 'capability.request-folder-grant'",
        );
      },
      [CAPABILITY_CHANNELS.listGrants]: [],
    });
    const { transport, stream } = cockpitTransport(recorder);
    const container = renderCockpit(transport);

    await stream.push(folderAsk());
    await waitFor(() => expect(grantButton(container)).not.toBeNull());
    await act(async () => {
      grantButton(container)?.click();
    });

    await waitFor(() => {
      expect(card(container)?.dataset.state).toBe("failed");
    });
    // The host's own message, verbatim — not a shrug.
    expect(card(container)?.textContent).toContain("No handler registered");
    // And no approve: a failed grant is not consent to read.
    expect(decisionPosts(recorder)).toHaveLength(0);
  });

  it("denying the folder resolves the interrupt without granting anything", async () => {
    const recorder: Recorder = { calls: [] };
    const { invoke } = installBridge({
      [CAPABILITY_CHANNELS.listGrants]: [],
    });
    const { transport, stream } = cockpitTransport(recorder);
    const container = renderCockpit(transport);

    await stream.push(folderAsk());
    const deny = await waitFor(() => {
      const el = container.querySelector<HTMLButtonElement>(
        `[data-testid='tc-chat-workspace-grant-deny-${APPROVAL_ID}']`,
      );
      expect(el).not.toBeNull();
      return el as HTMLButtonElement;
    });
    await act(async () => {
      deny.click();
    });

    // The run continues without the folder: a reject, and the picker never opened.
    await waitFor(() => {
      expect(decisionPosts(recorder)).toHaveLength(1);
    });
    expect(decisionPosts(recorder)[0]?.body).toEqual({ decision: "rejected" });
    expect(invoke).not.toHaveBeenCalledWith(
      CAPABILITY_CHANNELS.requestFolderGrant,
      expect.anything(),
    );
  });
});
