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

// DestinationPlaceholder (rendered inside the outlet) reads the Router port for
// its bridge navigation. The outlet never passes bridges, so `navigate` is
// never called — a no-op fake satisfies the hook contract.
function fakeRouter(): Router<ArtifactRoute> {
  return {
    current: () => ({ kind: "chat", conversationId: "c1" }),
    navigate: () => undefined,
    subscribe: () => () => undefined,
  };
}

// A minimal Transport for the Run cockpit path. `useRunSession` GETs the run
// list (empty here → idle cockpit, no SSE), and `TcChat` GETs the conversation
// messages; both resolve to empty payloads. SSE is a no-op — the empty run list
// means the session never subscribes.
function fakeTransport(): Transport {
  return {
    request: <TRes,>(req: TypedRequest): Promise<TRes> => {
      const body = req.path.includes("/messages")
        ? { messages: [] }
        : { runs: [] };
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

function renderOutlet(destination: ShellDestinationSlug) {
  const ui: ReactElement = (
    <RouterProvider router={fakeRouter()}>
      <DestinationOutlet destination={destination} />
    </RouterProvider>
  );
  return render(ui);
}

// The Run cockpit needs the Transport + KeyValueStore providers that ChatShell
// installs above the outlet in the real app.
function renderRunOutlet() {
  const ui: ReactElement = (
    <TransportProvider transport={fakeTransport()}>
      <KeyValueStoreProvider store={fakeKeyValueStore()}>
        <RouterProvider router={fakeRouter()}>
          <DestinationOutlet destination="run" />
        </RouterProvider>
      </KeyValueStoreProvider>
    </TransportProvider>
  );
  return render(ui);
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

  it("renders an honest placeholder for each not-yet-built destination", () => {
    const cases: ReadonlyArray<[ShellDestinationSlug, string]> = [
      ["chats", "Chats"],
      ["projects", "Projects"],
      ["activity", "Activity"],
      // Tools (relabelled) keeps the underlying `connectors` slug.
      ["connectors", "Tools"],
      // Skills (relabelled) keeps the underlying `tools` slug.
      ["tools", "Skills"],
    ];
    for (const [slug, expectedTitle] of cases) {
      const { container, unmount } = renderOutlet(slug);
      expect(
        container.querySelector("[data-testid='destination-outlet']"),
      ).not.toBeNull();
      expect(titleOf(container)).toBe(expectedTitle);
      // No fake data / spinner / retry — deterministic placeholder only.
      expect(
        container.querySelector("[data-testid='destination-placeholder']"),
      ).not.toBeNull();
      unmount();
    }
  });

  it("folds the deprecated agents slug onto the Activity surface", () => {
    const { container } = renderOutlet("agents");
    expect(
      container
        .querySelector("[data-testid='destination-outlet']")
        ?.getAttribute("data-destination"),
    ).toBe("activity");
    expect(titleOf(container)).toBe("Activity");
  });

  it("folds the deprecated inbox slug onto the Activity surface", () => {
    const { container } = renderOutlet("inbox");
    expect(
      container
        .querySelector("[data-testid='destination-outlet']")
        ?.getAttribute("data-destination"),
    ).toBe("activity");
    expect(titleOf(container)).toBe("Activity");
  });

  it("renders a generic honest placeholder for an unmapped slug", () => {
    const { container } = renderOutlet("memory");
    expect(
      container.querySelector("[data-testid='destination-placeholder']"),
    ).not.toBeNull();
    expect(titleOf(container)).toBe("Memory");
  });
});
