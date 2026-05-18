// useCommandPaletteHotkey — keyboard hook tests.

import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useCommandPaletteHotkey } from "./useCommandPaletteHotkey";

function dispatchKey(init: KeyboardEventInit): void {
  document.dispatchEvent(new KeyboardEvent("keydown", init));
}

describe("useCommandPaletteHotkey", () => {
  it("fires onOpen when ⌘K (metaKey) is pressed", () => {
    const onOpen = vi.fn();
    renderHook(() => useCommandPaletteHotkey({ onOpen }));
    dispatchKey({ key: "k", metaKey: true });
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("fires onOpen when Ctrl+K is pressed", () => {
    const onOpen = vi.fn();
    renderHook(() => useCommandPaletteHotkey({ onOpen }));
    dispatchKey({ key: "k", ctrlKey: true });
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("does NOT fire on plain k", () => {
    const onOpen = vi.fn();
    renderHook(() => useCommandPaletteHotkey({ onOpen }));
    dispatchKey({ key: "k" });
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("does NOT fire on ⌘+Shift+K (reserved for other shortcuts)", () => {
    const onOpen = vi.fn();
    renderHook(() => useCommandPaletteHotkey({ onOpen }));
    dispatchKey({ key: "k", metaKey: true, shiftKey: true });
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("removes the listener on unmount", () => {
    const onOpen = vi.fn();
    const { unmount } = renderHook(() => useCommandPaletteHotkey({ onOpen }));
    unmount();
    dispatchKey({ key: "k", metaKey: true });
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("does NOT attach the listener when enabled=false", () => {
    const onOpen = vi.fn();
    renderHook(() => useCommandPaletteHotkey({ onOpen, enabled: false }));
    dispatchKey({ key: "k", metaKey: true });
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("treats uppercase K the same as lowercase k (some browsers report uppercase with metaKey)", () => {
    const onOpen = vi.fn();
    renderHook(() => useCommandPaletteHotkey({ onOpen }));
    dispatchKey({ key: "K", metaKey: true });
    expect(onOpen).toHaveBeenCalledTimes(1);
  });
});
