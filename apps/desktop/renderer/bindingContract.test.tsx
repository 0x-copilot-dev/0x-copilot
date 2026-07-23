// @vitest-environment jsdom
//
// Desktop host binding conformance (PRD-03 DoD 6, 7, 9, 10).
//
// The manifest-driven guard that types cannot express: every field the shell
// contract declares is answered by the desktop binding with a non-`undefined`
// value; the declared opt-outs are literally `null` (a diff, not a discovery);
// the rail foot actually renders the signed-in initial (the capability that
// shipped dark on `main`); and Activity's callback is invoked WITH the row's
// run id (a 0-arity discard the type system can't catch).
import {
  ChatShell,
  SHELL_BINDING_FIELDS,
  TransportProvider,
  type ArtifactRoute,
  type KeyValueStore,
  type PresenceSignal,
  type Router,
} from "@0x-copilot/chat-surface";
import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { type ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActivityBinder } from "./destinationBinders";
import {
  DESKTOP_PROJECTS_DETAIL,
  buildDesktopShellBinding,
} from "./shellBinding";

afterEach(() => {
  cleanup();
});

const DESKTOP_CAPS: TransportCapabilities = {
  substrate: "desktop-webview",
  nativeSecretStorage: true,
  fileSystemAccess: false,
  clipboardWrite: false,
  openExternal: false,
};

function stubTransport(): Transport {
  return {
    request: <TRes,>(_req: TypedRequest): Promise<TRes> =>
      Promise.resolve({} as TRes),
    subscribeServerSentEvents: (
      _opts: SseSubscribeOptions,
    ): SseSubscription => ({
      close: () => undefined,
    }),
    getSession: (): Session => ({ bearer: null }),
    capabilities: (): TransportCapabilities => DESKTOP_CAPS,
  };
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

const stubPresence: PresenceSignal = {
  current: () => "visible",
  subscribe: () => () => {},
};

// A conversation whose latest run is live, so its Activity row is a clickable
// "open run" button carrying the run id.
function activityTransport(): Transport {
  return {
    request: <TRes,>(req: TypedRequest): Promise<TRes> => {
      if (req.method === "GET" && req.path === "/v1/agent/conversations") {
        return Promise.resolve({
          conversations: [
            {
              conversation_id: "conv-abc",
              org_id: "org-1",
              user_id: "user-1",
              assistant_id: "asst-1",
              title: "Live sync",
              status: "active",
              created_at: "2026-07-22T00:00:00Z",
              updated_at: "2026-07-22T00:00:00Z",
              archived_at: null,
              metadata: {},
              schema_version: 1,
              latest_run_id: "run_abc",
              latest_run_status: "running",
            },
          ],
          next_cursor: null,
          has_more: false,
        } as unknown as TRes);
      }
      if (req.path.includes("/v1/audit")) {
        return Promise.resolve({ rows: [] } as unknown as TRes);
      }
      return Promise.resolve({} as TRes);
    },
    subscribeServerSentEvents: (
      _opts: SseSubscribeOptions,
    ): SseSubscription => ({
      close: () => undefined,
    }),
    getSession: (): Session => ({ bearer: null }),
    capabilities: (): TransportCapabilities => DESKTOP_CAPS,
  };
}

function mountShell(displayName: string | null): HTMLElement {
  const ui: ReactElement = (
    <ChatShell
      transport={stubTransport()}
      router={fakeRouter()}
      keyValueStore={fakeKeyValueStore()}
      presenceSignal={stubPresence}
      activeDestination="run"
      onNavigate={() => {}}
      // Supplying onOpenSettings is what makes AppRail render its FOOT (the
      // account avatar that carries the identity glyph).
      onOpenSettings={() => {}}
      binding={buildDesktopShellBinding({ displayName }, false)}
    />
  );
  return render(ui).container;
}

describe("desktop shell binding — manifest conformance (DoD 6)", () => {
  it("answers every SHELL_BINDING_FIELDS entry with a non-undefined value", () => {
    const binding = buildDesktopShellBinding(
      { displayName: "Sarah Chen" },
      false,
    );
    for (const field of SHELL_BINDING_FIELDS) {
      expect(binding[field]).not.toBeUndefined();
    }
  });

  it("declares its opt-outs literally (walletChip / topbarLeaf / projects detail)", () => {
    const binding = buildDesktopShellBinding(
      { displayName: "Sarah Chen" },
      false,
    );
    expect(binding.walletChip).toBeNull();
    expect(binding.topbarLeaf).toBeNull();
    // Closing the desktop project-detail gap is a diff on this literal, not a
    // discovery that a prop was never passed.
    expect(DESKTOP_PROJECTS_DETAIL.mode).toBe("disabled");
  });
});

describe("desktop rail identity — regression guard (DoD 7 + 10)", () => {
  it("renders the signed-in initial in the rail foot for a real display name", () => {
    // Fails on `main`: bootstrap passed no identity, so the person glyph always
    // rendered and [data-rail-initial] never appeared.
    const container = mountShell("Sarah Chen");
    const initial = container.querySelector("[data-rail-initial]");
    expect(initial).not.toBeNull();
    // Design value pinned numerically: prefs.name.slice(0, 1) — exactly one char.
    expect(initial!.textContent!.length).toBe(1);
  });

  it("falls back to the neutral person glyph when there is no display name", () => {
    const container = mountShell(null);
    expect(container.querySelector("[data-rail-initial]")).toBeNull();
    expect(container.querySelector("[data-rail-me] svg")).not.toBeNull();
  });
});

describe("desktop Activity — run forwarding (arity guard, PRD-04 Seam C)", () => {
  it("invokes onOpenRun WITH the row's { conversationId, runId }, not undefined", async () => {
    const onOpenRun = vi.fn();
    const { container } = render(
      <TransportProvider transport={activityTransport()}>
        <ActivityBinder onOpenRun={onOpenRun} />
      </TransportProvider>,
    );
    const row = await waitFor(() => {
      const el = container.querySelector("[data-testid='activity-row']");
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    fireEvent.click(row);
    // On `main` the binder passed `() => onOpenRun?.()`, dropping the id.
    // PRD-04 widens the argument to the row's conversation + run identity.
    expect(onOpenRun).toHaveBeenCalledWith({
      conversationId: "conv-abc",
      runId: "run_abc",
    });
  });
});
