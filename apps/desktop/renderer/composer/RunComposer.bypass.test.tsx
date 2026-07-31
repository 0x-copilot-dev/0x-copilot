// @vitest-environment jsdom
//
// The composer bypass pill, driven through the REAL RunComposer (PRD-FS-10
// §4.3). Nothing here injects the hook or the pill: the composer is mounted
// with a transport whose `/v1/agent/workspace/defaults` answer is the only
// difference between the master-off and master-on cases, and the assertions are
// on what reaches `dispatch` — the exact object the run body is built from.
//
// That matters because the failure this file exists to catch is a pill that
// renders and reports nothing. A test that rendered `<BypassPill>` directly and
// asserted `onChange` would stay green through a composer that never mounted it
// or never threaded its value onto the request.

import {
  TransportProvider,
  type RunStartRequest,
} from "@0x-copilot/chat-surface";
import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { type ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RunComposer } from "./RunComposer";

afterEach(() => {
  cleanup();
});

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

function fakeTransport(masterEnabled: boolean): Transport {
  return {
    request: <TRes,>(req: TypedRequest): Promise<TRes> =>
      Promise.resolve(payloadFor(req.path, masterEnabled) as unknown as TRes),
    subscribeServerSentEvents: (
      _opts: SseSubscribeOptions,
    ): SseSubscription => ({ close: () => undefined }),
    getSession: (): Session => ({ bearer: null }),
    capabilities: (): TransportCapabilities => ({
      substrate: "desktop-webview",
      nativeSecretStorage: true,
      fileSystemAccess: false,
      clipboardWrite: false,
      openExternal: false,
    }),
  };
}

function payloadFor(
  path: string,
  masterEnabled: boolean,
): Record<string, unknown> {
  if (path.includes("/v1/skills")) return { skills: [] };
  if (path.includes("/v1/mcp/servers")) return { servers: [] };
  if (path.includes("/v1/settings/provider-keys")) {
    return { keys: [{ provider: "openai" }] };
  }
  if (path.includes("/v1/agent/models")) {
    return {
      default_model_id: "gpt-5.4-mini",
      models: [
        {
          id: "gpt-5.4-mini",
          provider: "openai",
          model_name: "gpt-5.4-mini",
          name: "GPT-5.4 Mini",
          configured: true,
          supports_streaming: true,
        },
      ],
    };
  }
  if (path.includes("/v1/local-models")) return { models: [] };
  if (path.includes("/v1/agent/workspace/defaults")) {
    return {
      default_model: { provider: "openai", model_name: "gpt-5.4-mini" },
      // The ONLY difference between the two cases in this file.
      behavior_overrides: { filesystem_bypass_enabled: masterEnabled },
    };
  }
  return {};
}

function mount(masterEnabled: boolean): {
  container: HTMLElement;
  dispatch: ReturnType<typeof vi.fn>;
} {
  const dispatch = vi.fn(
    async (_request: RunStartRequest): Promise<void> => {},
  );
  const ui: ReactElement = (
    <TransportProvider transport={fakeTransport(masterEnabled)}>
      <RunComposer
        dispatch={dispatch}
        disabled={false}
        placeholder="Send a message…"
      />
    </TransportProvider>
  );
  const { container } = render(ui);
  return { container, dispatch };
}

function pill(): HTMLButtonElement {
  return screen.getByRole("button", {
    name: /Execution mode:/,
  }) as HTMLButtonElement;
}

function typeAndSend(container: HTMLElement, text: string): void {
  const ta = container.querySelector<HTMLTextAreaElement>(
    "[data-testid='composer-textarea']",
  );
  if (ta === null) throw new Error("composer textarea not mounted");
  fireEvent.change(ta, { target: { value: text } });
  const send = container.querySelector<HTMLButtonElement>(
    "button[aria-label='Send message']",
  );
  if (send === null) throw new Error("composer send button not mounted");
  fireEvent.click(send);
}

async function choose(label: RegExp): Promise<void> {
  fireEvent.click(pill());
  fireEvent.click(await screen.findByRole("menuitemradio", { name: label }));
}

describe("RunComposer — filesystem bypass pill", () => {
  it("mounts the pill disabled while the master switch is off", async () => {
    mount(false);
    await waitFor(() => {
      expect(pill()).toBeDisabled();
    });
    expect(pill()).toHaveTextContent("Manual");
    // Not offered means not reachable — clicking must not surface Bypass.
    fireEvent.click(pill());
    expect(screen.queryByRole("menuitemradio", { name: /Bypass/ })).toBeNull();
  });

  it("sends no filesystem_bypass field while the master switch is off", async () => {
    const { container, dispatch } = mount(false);
    await waitFor(() => {
      expect(pill()).toBeDisabled();
    });

    typeAndSend(container, "list the folder");

    await waitFor(() => {
      expect(dispatch).toHaveBeenCalledTimes(1);
    });
    expect(dispatch.mock.calls[0][0].filesystemBypass).toBeUndefined();
  });

  it("threads a message-scoped Bypass onto the request", async () => {
    const { container, dispatch } = mount(true);
    await waitFor(() => {
      expect(pill()).not.toBeDisabled();
    });

    await choose(/^Bypass/);
    expect(pill()).toHaveTextContent("Bypass");

    typeAndSend(container, "tidy the folder");

    await waitFor(() => {
      expect(dispatch).toHaveBeenCalledTimes(1);
    });
    expect(dispatch.mock.calls[0][0].filesystemBypass).toEqual({
      message: "bypass",
    });
  });

  it("spends a message-scoped Bypass after a successful send", async () => {
    // The observable difference between the two scopes. A one-turn choice that
    // silently stayed on would be the worst kind of bug here: the user believes
    // they authorized one message and every later one auto-applies.
    const { container, dispatch } = mount(true);
    await waitFor(() => {
      expect(pill()).not.toBeDisabled();
    });

    await choose(/^Bypass/);
    typeAndSend(container, "first");
    await waitFor(() => {
      expect(dispatch).toHaveBeenCalledTimes(1);
    });

    await waitFor(() => {
      expect(pill()).toHaveTextContent("Manual");
    });
    typeAndSend(container, "second");
    await waitFor(() => {
      expect(dispatch).toHaveBeenCalledTimes(2);
    });
    expect(dispatch.mock.calls[1][0].filesystemBypass).toBeUndefined();
  });

  it("keeps a run-scoped Bypass across sends", async () => {
    const { container, dispatch } = mount(true);
    await waitFor(() => {
      expect(pill()).not.toBeDisabled();
    });

    await choose(/^Bypass/);
    await choose(/This run/);

    typeAndSend(container, "first");
    await waitFor(() => {
      expect(dispatch).toHaveBeenCalledTimes(1);
    });
    typeAndSend(container, "second");
    await waitFor(() => {
      expect(dispatch).toHaveBeenCalledTimes(2);
    });

    expect(dispatch.mock.calls[0][0].filesystemBypass).toEqual({
      run: "bypass",
    });
    expect(dispatch.mock.calls[1][0].filesystemBypass).toEqual({
      run: "bypass",
    });
  });
});
