// @vitest-environment jsdom
import {
  KeyValueStoreProvider,
  RouterProvider,
  TransportProvider,
  type ArtifactRoute,
  type KeyValueStore,
  type Router,
  type ShellDestinationSlug,
} from "@0x-copilot/chat-surface";
import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";
import { cleanup, render } from "@testing-library/react";
import { type ReactElement } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { DestinationOutlet } from "./DestinationOutlet";

// The desktop vitest config runs with `globals: false`, so testing-library's
// automatic afterEach cleanup does not self-register — do it explicitly.
afterEach(() => {
  cleanup();
});

// Surfaces rendered inside the outlet (the fallback DestinationPlaceholder and
// the Phase-4 components' ItemLink resolvers) read the Router port. The outlet
// never triggers navigation in these tests, so a no-op fake satisfies the hook
// contract.
function fakeRouter(): Router<ArtifactRoute> {
  return {
    current: () => ({ kind: "chat", conversationId: "c1" }),
    navigate: () => undefined,
    subscribe: () => () => undefined,
  };
}

// A minimal Transport for both the Run cockpit path and the Phase-4 binders.
// `useRunSession` GETs the run list (empty → idle cockpit, no SSE), `TcChat`
// GETs the conversation messages, and the desktop binders GET the
// conversations / audit / connectors / skills / projects lists — all resolve
// to empty payloads so each surface renders its honest empty state. SSE is a
// no-op — the empty run list means the session never subscribes.
function fakeTransport(): Transport {
  return {
    request: <TRes,>(req: TypedRequest): Promise<TRes> => {
      const body = emptyPayloadFor(req.path);
      return Promise.resolve(body as unknown as TRes);
    },
    subscribeServerSentEvents: (
      _opts: SseSubscribeOptions,
    ): SseSubscription => ({
      close: () => undefined,
    }),
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

// Empty-but-well-shaped payload for each endpoint a surface reads, keyed by
// path. The binders defensively coalesce missing fields to `[]`, but returning
// the real field names keeps the fake honest.
function emptyPayloadFor(path: string): Record<string, unknown> {
  if (path.includes("/messages")) return { messages: [] };
  if (path.includes("/v1/agent/conversations")) return { conversations: [] };
  if (path.includes("/v1/audit")) return { rows: [] };
  if (path.includes("/v1/connectors")) {
    return { connectors: [], available: [], next_cursor: null };
  }
  if (path.includes("/v1/skills")) return { skills: [] };
  if (path.includes("/v1/projects")) return { items: [], next_cursor: null };
  return { runs: [] };
}

// Map-backed KeyValueStore for `useRunMode` (per-conversation mode persistence).
function fakeKeyValueStore(): KeyValueStore {
  const map = new Map<string, string>();
  return {
    get: (key) => map.get(key) ?? null,
    set: (key, value) => {
      if (value === null) {
        map.delete(key);
      } else {
        map.set(key, value);
      }
    },
    keys: (prefix) =>
      [...map.keys()].filter(
        (key) => prefix === undefined || key.startsWith(prefix),
      ),
  };
}

// The Phase-4 binders read the Transport port (via `useTransport`) exactly
// like the Run cockpit, so every destination now renders under the same
// providers `ChatShell` installs in the real app.
function renderOutlet(destination: ShellDestinationSlug) {
  const ui: ReactElement = (
    <TransportProvider transport={fakeTransport()}>
      <KeyValueStoreProvider store={fakeKeyValueStore()}>
        <RouterProvider router={fakeRouter()}>
          <DestinationOutlet destination={destination} />
        </RouterProvider>
      </KeyValueStoreProvider>
    </TransportProvider>
  );
  return render(ui);
}

// The Run cockpit uses the same providers as every other destination now.
function renderRunOutlet() {
  return renderOutlet("run");
}

function titleOf(container: HTMLElement): string | null | undefined {
  return container.querySelector(
    "[data-testid='destination-placeholder-title']",
  )?.textContent;
}

describe("DestinationOutlet", () => {
  it("mounts the Run cockpit (RunDestination) for the run slug", () => {
    const { container } = renderRunOutlet();
    const outlet = container.querySelector(
      "[data-testid='destination-outlet']",
    );
    expect(outlet?.getAttribute("data-destination")).toBe("run");
    // The real cockpit shell renders — its header + canvas, not a placeholder.
    expect(
      container.querySelector("[data-testid='run-destination']"),
    ).not.toBeNull();
    expect(
      container.querySelector("[data-testid='run-header']"),
    ).not.toBeNull();
    expect(
      container.querySelector("[data-testid='destination-placeholder']"),
    ).toBeNull();
  });

  it("mounts the real Phase-4 surface for each solo destination (no placeholder)", async () => {
    // Each slug → the marker testid of its real chat-surface component in its
    // honest empty state (the fake transport resolves every list to empty).
    const cases: ReadonlyArray<[ShellDestinationSlug, string]> = [
      ["chats", "chats-empty"],
      ["projects", "projects-destination"],
      ["activity", "activity-destination"],
      // Tools (relabelled) keeps the underlying `connectors` slug.
      ["connectors", "connectors-body"],
      // Skills (relabelled) keeps the underlying `tools` slug.
      ["tools", "skills-destination"],
    ];
    for (const [slug, marker] of cases) {
      const { container, findByTestId, unmount } = renderOutlet(slug);
      expect(
        container.querySelector("[data-testid='destination-outlet']"),
      ).not.toBeNull();
      // The real component renders (awaits the binder's fetch), and the old
      // "Coming in Phase 4" placeholder is gone.
      expect(await findByTestId(marker)).not.toBeNull();
      expect(
        container.querySelector("[data-testid='destination-placeholder']"),
      ).toBeNull();
      unmount();
    }
  });

  it("folds the deprecated agents slug onto the Activity surface", async () => {
    const { container, findByTestId } = renderOutlet("agents");
    expect(
      container
        .querySelector("[data-testid='destination-outlet']")
        ?.getAttribute("data-destination"),
    ).toBe("activity");
    expect(await findByTestId("activity-destination")).not.toBeNull();
  });

  it("folds the deprecated inbox slug onto the Activity surface", async () => {
    const { container, findByTestId } = renderOutlet("inbox");
    expect(
      container
        .querySelector("[data-testid='destination-outlet']")
        ?.getAttribute("data-destination"),
    ).toBe("activity");
    expect(await findByTestId("activity-destination")).not.toBeNull();
  });

  it("renders a generic honest placeholder for an unmapped slug", () => {
    const { container } = renderOutlet("memory");
    expect(
      container.querySelector("[data-testid='destination-placeholder']"),
    ).not.toBeNull();
    expect(titleOf(container)).toBe("Memory");
  });
});
