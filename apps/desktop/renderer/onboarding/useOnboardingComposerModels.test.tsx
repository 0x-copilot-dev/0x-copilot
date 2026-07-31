// @vitest-environment jsdom
//
// The FTUE composer's model catalog + selection. These tests cover the seam the
// first-run surface shares with the Run cockpit: the model picked here is
// remembered, so the cockpit opens on it after the handoff instead of
// recomputing a generic default.
//
// The memory only works because bootstrap mounts `KeyValueStoreProvider` ABOVE
// the first-run gate — the gate renders outside `ChatShell`, and without that
// provider the hook resolves the port's no-op default store and every write
// silently vanishes. The wrapper here is the test-side stand-in for it.

import {
  COMPOSER_MODEL_PREFERENCE_KEY,
  KeyValueStoreProvider,
  type KeyValueStore,
} from "@0x-copilot/chat-surface";
import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { type ReactNode } from "react";
import { afterEach, describe, expect, it } from "vitest";

import {
  LOCAL_ENGINE_MODEL_ID,
  useOnboardingComposerModels,
  type OnboardingLocalEngine,
} from "./useOnboardingComposerModels";

afterEach(() => {
  cleanup();
});

class MemoryKeyValueStore implements KeyValueStore {
  readonly map = new Map<string, string>();

  get(key: string): string | null {
    return this.map.get(key) ?? null;
  }

  set(key: string, value: string | null): void {
    if (value === null) {
      this.map.delete(key);
      return;
    }
    this.map.set(key, value);
  }

  keys(prefix?: string): readonly string[] {
    return [...this.map.keys()].filter(
      (key) => prefix === undefined || key.startsWith(prefix),
    );
  }
}

// Two configured providers so the remembered pick and the auto-default are
// DIFFERENT models — otherwise the memory would not be observable at all.
function fakeTransport(): Transport {
  return {
    request: <TRes,>(req: TypedRequest): Promise<TRes> => {
      const body = req.path.includes("/v1/agent/models")
        ? {
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
              {
                id: "claude-sonnet-5",
                provider: "anthropic",
                model_name: "claude-sonnet-5",
                name: "Claude Sonnet 5",
                configured: true,
                supports_streaming: true,
              },
            ],
          }
        : { models: [] };
      return Promise.resolve(body as unknown as TRes);
    },
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

const CLOUD_ENGINE: OnboardingLocalEngine = {
  localModelPct: null,
  modelName: null,
};
const LOCAL_ENGINE: OnboardingLocalEngine = {
  localModelPct: 100,
  modelName: "qwen3:4b",
};

function renderModels(
  store: KeyValueStore,
  local: OnboardingLocalEngine = CLOUD_ENGINE,
) {
  const transport = fakeTransport();
  return renderHook(() => useOnboardingComposerModels(transport, local), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <KeyValueStoreProvider store={store}>{children}</KeyValueStoreProvider>
    ),
  });
}

function storedPreference(store: MemoryKeyValueStore): {
  last: string | null;
} {
  const raw = store.get(COMPOSER_MODEL_PREFERENCE_KEY);
  return raw === null ? { last: null } : JSON.parse(raw);
}

describe("useOnboardingComposerModels — model memory", () => {
  it("remembers the model picked in the FTUE so the cockpit opens on it", async () => {
    const store = new MemoryKeyValueStore();
    const { result } = renderModels(store);
    await waitFor(() => expect(result.current.models.length).toBe(2));

    result.current.onModelChange("claude-sonnet-5");

    await waitFor(() =>
      expect(result.current.selectedModel).toBe("claude-sonnet-5"),
    );
    // No conversation exists yet, so it is recorded as the last-used model —
    // which is what the cockpit reads for the chat the handoff creates.
    expect(storedPreference(store).last).toBe("claude-sonnet-5");
  });

  it("opens on the remembered model instead of the auto-default", async () => {
    const store = new MemoryKeyValueStore();
    store.set(
      COMPOSER_MODEL_PREFERENCE_KEY,
      JSON.stringify({ last: "claude-sonnet-5", chats: [] }),
    );
    const { result } = renderModels(store);

    // The auto-default would be gpt-5.4-mini (OpenAI leads the priority order).
    await waitFor(() =>
      expect(result.current.selectedModel).toBe("claude-sonnet-5"),
    );
  });

  it("keeps the on-device model selected on the local-engine path", async () => {
    const store = new MemoryKeyValueStore();
    store.set(
      COMPOSER_MODEL_PREFERENCE_KEY,
      JSON.stringify({ last: "claude-sonnet-5", chats: [] }),
    );
    const { result } = renderModels(store, LOCAL_ENGINE);

    // Wait for the CLOUD catalog to land and flush the selection pass it
    // triggers — asserting earlier would pass trivially, since the on-device row
    // is the only model in the list until that fetch resolves.
    await waitFor(() =>
      expect(
        result.current.models.some((m) => m.id === "claude-sonnet-5"),
      ).toBe(true),
    );
    await act(async () => {});

    // A guard on the memory, not on the pre-existing local path: choosing
    // on-device is the decision this flow exists to make, and the newly-added
    // remembered-pick lookup must not displace it. It holds because the lookup
    // sits below keep-current — move it above and this test goes red.
    expect(result.current.selectedModel).toBe(LOCAL_ENGINE_MODEL_ID);
  });

  it("ignores a remembered model that is not usable", async () => {
    const store = new MemoryKeyValueStore();
    store.set(
      COMPOSER_MODEL_PREFERENCE_KEY,
      JSON.stringify({ last: "gone-4", chats: [] }),
    );
    const { result } = renderModels(store);

    await waitFor(() =>
      expect(result.current.selectedModel).toBe("gpt-5.4-mini"),
    );
  });
});
