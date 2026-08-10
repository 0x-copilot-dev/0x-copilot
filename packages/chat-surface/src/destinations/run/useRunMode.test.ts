// useRunMode — KeyValueStore persistence + ⌘M toggle tests (FR-3.7/3.8).

import { act, renderHook } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { afterEach, describe, expect, it } from "vitest";

import type { ConversationId } from "@0x-copilot/api-types";

import { KeyValueStoreProvider } from "../../providers/KeyValueStoreProvider";
import type { KeyValueStore } from "../../storage/key-value-store";
import {
  DEFAULT_RUN_FOCUS_PANEL_COLLAPSED,
  DEFAULT_RUN_STUDIO_RAIL_COLLAPSED,
  readRunFocusPanelCollapsed,
  readRunMode,
  readRunStudioRailCollapsed,
  runFocusPanelCollapsedKey,
  runModeKey,
  runStudioRailCollapsedKey,
  useRunMode,
  useRunPanelCollapsed,
  useRunStudioRailCollapsed,
  writeRunFocusPanelCollapsed,
  writeRunMode,
  writeRunStudioRailCollapsed,
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

  it("restores a persisted studio value", () => {
    const store = makeStore({ [runModeKey(CONV)]: "studio" });
    expect(readRunMode(store, CONV)).toBe("studio");
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

  it("writeRunMode persists explicit modes via the store", () => {
    const store = makeStore();
    writeRunMode(store, CONV, "focus");
    expect(store.get(runModeKey(CONV))).toBe("focus");
    writeRunMode(store, CONV, "studio");
    expect(store.get(runModeKey(CONV))).toBe("studio");
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

  it("setMode restores Studio and persists to the store", () => {
    const store = makeStore({ [runModeKey(CONV)]: "focus" });
    const { result } = renderRunMode(store);
    act(() => {
      result.current.setMode("studio");
    });
    expect(result.current.mode).toBe("studio");
    expect(store.get(runModeKey(CONV))).toBe("studio");
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

// ============================================================
// WS-F — useRunPanelCollapsed (Focus Run-details collapse)
// ============================================================

function renderPanelCollapsed(
  store: KeyValueStore,
  conversationId: ConversationId = CONV,
) {
  return renderHook(() => useRunPanelCollapsed({ conversationId }), {
    wrapper: wrapperFor(store),
  });
}

describe("readRunFocusPanelCollapsed / persistence helpers", () => {
  // Focus hands the canvas to the chat, so it opens with the Run-details
  // column folded. This is the one place the two rails disagree.
  it("defaults to COLLAPSED (true) when nothing is persisted", () => {
    expect(readRunFocusPanelCollapsed(makeStore(), CONV)).toBe(true);
  });

  it('honours an explicit "0" so a reader who opened the panel keeps it', () => {
    const key = runFocusPanelCollapsedKey(CONV);
    expect(readRunFocusPanelCollapsed(makeStore({ [key]: "1" }), CONV)).toBe(
      true,
    );
    expect(readRunFocusPanelCollapsed(makeStore({ [key]: "0" }), CONV)).toBe(
      false,
    );
    // Unrecognised ⇒ the mode's default, which for Focus is collapsed.
    expect(readRunFocusPanelCollapsed(makeStore({ [key]: "yes" }), CONV)).toBe(
      true,
    );
  });

  it("round-trips through write/read", () => {
    const store = makeStore();
    writeRunFocusPanelCollapsed(store, CONV, true);
    expect(readRunFocusPanelCollapsed(store, CONV)).toBe(true);
    writeRunFocusPanelCollapsed(store, CONV, false);
    expect(readRunFocusPanelCollapsed(store, CONV)).toBe(false);
  });
});

describe("useRunPanelCollapsed", () => {
  it("hydrates the persisted collapse flag on mount", () => {
    const store = makeStore({ [runFocusPanelCollapsedKey(CONV)]: "1" });
    const { result } = renderPanelCollapsed(store);
    expect(result.current.collapsed).toBe(true);
  });

  it("persists an explicit setCollapsed to the KeyValueStore", () => {
    const store = makeStore();
    const { result } = renderPanelCollapsed(store);
    act(() => result.current.setCollapsed(true));
    expect(result.current.collapsed).toBe(true);
    expect(store.get(runFocusPanelCollapsedKey(CONV))).toBe("1");
  });

  it("toggles collapsed↔expanded and persists", () => {
    const store = makeStore();
    const { result } = renderPanelCollapsed(store);
    // Focus starts collapsed, so the first toggle OPENS the panel.
    expect(result.current.collapsed).toBe(true);
    act(() => result.current.toggle());
    expect(result.current.collapsed).toBe(false);
    expect(store.get(runFocusPanelCollapsedKey(CONV))).toBe("0");
    act(() => result.current.toggle());
    expect(result.current.collapsed).toBe(true);
    expect(store.get(runFocusPanelCollapsedKey(CONV))).toBe("1");
  });

  it("opens by default, and stays open once the reader opened it", () => {
    const store = makeStore();
    expect(renderPanelCollapsed(store).result.current.collapsed).toBe(true);
    writeRunFocusPanelCollapsed(store, CONV, false);
    expect(renderPanelCollapsed(store).result.current.collapsed).toBe(false);
  });

  it("re-hydrates when the conversation changes (per-conversation state)", () => {
    const other = "conv-2" as ConversationId;
    const store = makeStore({
      [runFocusPanelCollapsedKey(CONV)]: "0",
      [runFocusPanelCollapsedKey(other)]: "1",
    });
    const { result, rerender } = renderHook(
      ({ id }: { id: ConversationId }) =>
        useRunPanelCollapsed({ conversationId: id }),
      { wrapper: wrapperFor(store), initialProps: { id: CONV } },
    );
    expect(result.current.collapsed).toBe(false);
    rerender({ id: other });
    expect(result.current.collapsed).toBe(true);
  });
});

// ============================================================
// useRunStudioRailCollapsed (Studio workspace-rail fold)
// ============================================================

function renderStudioRailCollapsed(
  store: KeyValueStore,
  conversationId: ConversationId = CONV,
) {
  return renderHook(() => useRunStudioRailCollapsed({ conversationId }), {
    wrapper: wrapperFor(store),
  });
}

describe("readRunStudioRailCollapsed / persistence helpers", () => {
  it("defaults to expanded (false) when nothing is persisted", () => {
    expect(readRunStudioRailCollapsed(makeStore(), CONV)).toBe(false);
  });

  it('reads only the literal "1" as collapsed', () => {
    const key = runStudioRailCollapsedKey(CONV);
    expect(readRunStudioRailCollapsed(makeStore({ [key]: "1" }), CONV)).toBe(
      true,
    );
    expect(readRunStudioRailCollapsed(makeStore({ [key]: "0" }), CONV)).toBe(
      false,
    );
    expect(readRunStudioRailCollapsed(makeStore({ [key]: "yes" }), CONV)).toBe(
      false,
    );
  });

  it("round-trips through write/read", () => {
    const store = makeStore();
    writeRunStudioRailCollapsed(store, CONV, true);
    expect(readRunStudioRailCollapsed(store, CONV)).toBe(true);
    writeRunStudioRailCollapsed(store, CONV, false);
    expect(readRunStudioRailCollapsed(store, CONV)).toBe(false);
  });

  // The Studio rail and the Focus panel are different objects doing different
  // jobs; sharing a key would fold one when the reader folded the other.
  it("uses a key distinct from the Focus panel's", () => {
    expect(runStudioRailCollapsedKey(CONV)).not.toBe(
      runFocusPanelCollapsedKey(CONV),
    );
    // Write BOTH to explicit, opposite values. Asserting against a default
    // would not prove isolation now that the two rails default differently —
    // the read could be returning its own default rather than the value the
    // other rail wrote.
    const store = makeStore();
    writeRunStudioRailCollapsed(store, CONV, true);
    writeRunFocusPanelCollapsed(store, CONV, false);
    expect(readRunStudioRailCollapsed(store, CONV)).toBe(true);
    expect(readRunFocusPanelCollapsed(store, CONV)).toBe(false);

    // …and back the other way, so neither key is merely being ignored.
    writeRunStudioRailCollapsed(store, CONV, false);
    writeRunFocusPanelCollapsed(store, CONV, true);
    expect(readRunStudioRailCollapsed(store, CONV)).toBe(false);
    expect(readRunFocusPanelCollapsed(store, CONV)).toBe(true);
  });

  // The defaults themselves are the contract the Run cockpit reads: Studio
  // opens with its workspace, Focus opens without it.
  it("defaults differ per rail, on the same empty store", () => {
    const store = makeStore();
    expect(readRunStudioRailCollapsed(store, CONV)).toBe(
      DEFAULT_RUN_STUDIO_RAIL_COLLAPSED,
    );
    expect(readRunFocusPanelCollapsed(store, CONV)).toBe(
      DEFAULT_RUN_FOCUS_PANEL_COLLAPSED,
    );
    expect(DEFAULT_RUN_STUDIO_RAIL_COLLAPSED).toBe(false);
    expect(DEFAULT_RUN_FOCUS_PANEL_COLLAPSED).toBe(true);
  });
});

describe("useRunStudioRailCollapsed", () => {
  it("hydrates the persisted fold on mount", () => {
    const store = makeStore({ [runStudioRailCollapsedKey(CONV)]: "1" });
    const { result } = renderStudioRailCollapsed(store);
    expect(result.current.collapsed).toBe(true);
  });

  it("persists an explicit setCollapsed to the KeyValueStore", () => {
    const store = makeStore();
    const { result } = renderStudioRailCollapsed(store);
    act(() => result.current.setCollapsed(true));
    expect(result.current.collapsed).toBe(true);
    expect(store.get(runStudioRailCollapsedKey(CONV))).toBe("1");
  });

  it("toggles folded↔expanded and persists", () => {
    const store = makeStore();
    const { result } = renderStudioRailCollapsed(store);
    act(() => result.current.toggle());
    expect(result.current.collapsed).toBe(true);
    act(() => result.current.toggle());
    expect(result.current.collapsed).toBe(false);
    expect(store.get(runStudioRailCollapsedKey(CONV))).toBe("0");
  });

  it("re-hydrates when the conversation changes (per-conversation state)", () => {
    const other = "conv-2" as ConversationId;
    const store = makeStore({ [runStudioRailCollapsedKey(other)]: "1" });
    const { result, rerender } = renderHook(
      ({ id }: { id: ConversationId }) =>
        useRunStudioRailCollapsed({ conversationId: id }),
      { wrapper: wrapperFor(store), initialProps: { id: CONV } },
    );
    expect(result.current.collapsed).toBe(false);
    rerender({ id: other });
    expect(result.current.collapsed).toBe(true);
  });
});
