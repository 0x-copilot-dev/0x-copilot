// useRunMode — KeyValueStore persistence + ⌘M toggle tests (FR-3.7/3.8).

import { act, renderHook } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { afterEach, describe, expect, it } from "vitest";

import type { ConversationId } from "@0x-copilot/api-types";

import { KeyValueStoreProvider } from "../../providers/KeyValueStoreProvider";
import type { KeyValueStore } from "../../storage/key-value-store";
import {
  readRunMode,
  runModeKey,
  useRunMode,
  writeRunMode,
  type RunMode,
} from "./useRunMode";

const CONV = "conv-1" as ConversationId;

/** Map-backed KeyValueStore for assertions on persisted values. */
function makeStore(seed?: Record<string, string>): KeyValueStore {
  const map = new Map<string, string>(Object.entries(seed ?? {}));
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

function wrapperFor(store: KeyValueStore) {
  return ({ children }: { children: ReactNode }): ReactNode =>
    createElement(KeyValueStoreProvider, { store, children });
}

function renderRunMode(
  store: KeyValueStore,
  options?: { conversationId?: ConversationId; enabled?: boolean },
) {
  return renderHook(
    () =>
      useRunMode({
        conversationId: options?.conversationId ?? CONV,
        enabled: options?.enabled,
      }),
    { wrapper: wrapperFor(store) },
  );
}

function dispatchKey(init: KeyboardEventInit): void {
  act(() => {
    globalThis.document.dispatchEvent(new KeyboardEvent("keydown", init));
  });
}

afterEach(() => {
  globalThis.document.body.innerHTML = "";
});

describe("readRunMode / persistence helpers", () => {
  it("defaults to studio when nothing is persisted", () => {
    expect(readRunMode(makeStore(), CONV)).toBe("studio");
  });

  it("restores a persisted focus value", () => {
    const store = makeStore({ [runModeKey(CONV)]: "focus" });
    expect(readRunMode(store, CONV)).toBe("focus");
  });

  it("coerces a legacy 'auto' value to studio (FR-3.7)", () => {
    const store = makeStore({ [runModeKey(CONV)]: "auto" });
    expect(readRunMode(store, CONV)).toBe("studio");
  });

  it("coerces any unrecognised value to studio", () => {
    const store = makeStore({ [runModeKey(CONV)]: "hologram" });
    expect(readRunMode(store, CONV)).toBe("studio");
  });

  it("namespaces the key per conversation", () => {
    expect(runModeKey(CONV)).toBe("chats.thread.conv-1.run_mode");
    expect(runModeKey("conv-2" as ConversationId)).toBe(
      "chats.thread.conv-2.run_mode",
    );
  });

  it("writeRunMode persists via the store", () => {
    const store = makeStore();
    writeRunMode(store, CONV, "focus");
    expect(store.get(runModeKey(CONV))).toBe("focus");
  });
});

describe("useRunMode — state + persistence", () => {
  it("initialises from the persisted value", () => {
    const store = makeStore({ [runModeKey(CONV)]: "focus" });
    const { result } = renderRunMode(store);
    expect(result.current.mode).toBe("focus");
  });

  it("defaults to studio with an empty store", () => {
    const { result } = renderRunMode(makeStore());
    expect(result.current.mode).toBe("studio");
  });

  it("setMode updates state and persists to the store", () => {
    const store = makeStore();
    const { result } = renderRunMode(store);
    act(() => {
      result.current.setMode("focus");
    });
    expect(result.current.mode).toBe("focus");
    expect(store.get(runModeKey(CONV))).toBe("focus");
  });

  it("toggle flips studio↔focus and persists each step", () => {
    const store = makeStore();
    const { result } = renderRunMode(store);
    act(() => {
      result.current.toggle();
    });
    expect(result.current.mode).toBe("focus");
    expect(store.get(runModeKey(CONV))).toBe("focus");
    act(() => {
      result.current.toggle();
    });
    expect(result.current.mode).toBe("studio");
    expect(store.get(runModeKey(CONV))).toBe("studio");
  });

  it("keeps modes independent per conversation", () => {
    const store = makeStore({
      [runModeKey(CONV)]: "focus",
      [runModeKey("conv-2" as ConversationId)]: "studio",
    });
    expect(renderRunMode(store).result.current.mode).toBe("focus");
    expect(
      renderRunMode(store, { conversationId: "conv-2" as ConversationId })
        .result.current.mode,
    ).toBe("studio");
  });
});

describe("useRunMode — ⌘M / Ctrl+M shortcut (FR-3.8)", () => {
  it("toggles on ⌘M (metaKey)", () => {
    const { result } = renderRunMode(makeStore());
    dispatchKey({ key: "m", metaKey: true });
    expect(result.current.mode).toBe("focus");
    dispatchKey({ key: "m", metaKey: true });
    expect(result.current.mode).toBe("studio");
  });

  it("toggles on Ctrl+M", () => {
    const { result } = renderRunMode(makeStore());
    dispatchKey({ key: "m", ctrlKey: true });
    expect(result.current.mode).toBe("focus");
  });

  it("treats uppercase M the same (some browsers report uppercase with a modifier)", () => {
    const { result } = renderRunMode(makeStore());
    dispatchKey({ key: "M", metaKey: true });
    expect(result.current.mode).toBe("focus");
  });

  it("persists the toggled mode from the shortcut", () => {
    const store = makeStore();
    renderRunMode(store);
    dispatchKey({ key: "m", metaKey: true });
    expect(store.get(runModeKey(CONV))).toBe("focus");
  });

  it("does NOT fire on plain m", () => {
    const { result } = renderRunMode(makeStore());
    dispatchKey({ key: "m" });
    expect(result.current.mode).toBe("studio");
  });

  it("does NOT fire on ⌘⇧M or ⌘⌥M", () => {
    const { result } = renderRunMode(makeStore());
    dispatchKey({ key: "m", metaKey: true, shiftKey: true });
    dispatchKey({ key: "m", metaKey: true, altKey: true });
    expect(result.current.mode).toBe("studio");
  });

  it("is suppressed while a text input is focused", () => {
    const input = globalThis.document.createElement("input");
    globalThis.document.body.appendChild(input);
    input.focus();
    expect(globalThis.document.activeElement).toBe(input);

    const { result } = renderRunMode(makeStore());
    dispatchKey({ key: "m", metaKey: true });
    expect(result.current.mode).toBe("studio");

    input.blur();
    dispatchKey({ key: "m", metaKey: true });
    expect(result.current.mode).toBe("focus");
  });

  it("is suppressed while a textarea (composer) is focused", () => {
    const textarea = globalThis.document.createElement("textarea");
    globalThis.document.body.appendChild(textarea);
    textarea.focus();

    const { result } = renderRunMode(makeStore());
    dispatchKey({ key: "m", metaKey: true });
    expect(result.current.mode).toBe("studio");
  });

  it("still fires when a non-text input (checkbox) is focused", () => {
    const checkbox = globalThis.document.createElement("input");
    checkbox.type = "checkbox";
    globalThis.document.body.appendChild(checkbox);
    checkbox.focus();

    const { result } = renderRunMode(makeStore());
    dispatchKey({ key: "m", metaKey: true });
    expect(result.current.mode).toBe("focus");
  });

  it("does NOT attach the listener when enabled=false", () => {
    const { result } = renderRunMode(makeStore(), { enabled: false });
    dispatchKey({ key: "m", metaKey: true });
    expect(result.current.mode).toBe("studio");
  });

  it("detaches the listener on unmount", () => {
    // After unmount the store is the durable witness: a stray ⌘M must
    // not persist a toggle through a detached listener.
    const store = makeStore();
    const { unmount } = renderRunMode(store);
    unmount();
    dispatchKey({ key: "m", metaKey: true });
    expect(readRunMode(store, CONV)).toBe("studio");
  });
});

describe("useRunMode — result stability", () => {
  it("returns a stable setMode/toggle identity across re-renders", () => {
    const { result, rerender } = renderRunMode(makeStore());
    const first = result.current;
    rerender();
    expect(result.current.setMode).toBe(first.setMode);
    expect(result.current.toggle).toBe(first.toggle);
  });

  it("exposes the RunMode literal union", () => {
    // Type-level anchor: RunMode must be exactly "studio" | "focus".
    const modes: RunMode[] = ["studio", "focus"];
    expect(modes).toHaveLength(2);
  });
});
