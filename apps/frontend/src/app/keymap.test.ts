import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { isTypingTarget, useKeymap } from "./keymap";

/**
 * KeyboardEvent.code for the keys these tests press. Real browser keydown
 * events always carry `code`; tinykeys v4 rejects any event without it
 * (`isKeyboardEvent` guards on `key && code && getModifierState`), so the
 * synthetic events must include it to be honest stand-ins.
 */
function codeFor(key: string): string {
  if (key === "\\") {
    return "Backslash";
  }
  return /^[a-z]$/i.test(key) ? `Key${key.toUpperCase()}` : key;
}

function pressKey(
  key: string,
  modifier: { ctrl?: boolean; meta?: boolean } = {},
  target: EventTarget | null = window,
): boolean {
  const event = new KeyboardEvent("keydown", {
    key,
    code: codeFor(key),
    ctrlKey: !!modifier.ctrl,
    metaKey: !!modifier.meta,
    bubbles: true,
    cancelable: true,
  });
  if (target && target !== window) {
    target.dispatchEvent(event);
  } else {
    window.dispatchEvent(event);
  }
  return event.defaultPrevented;
}

describe("isTypingTarget", () => {
  it("returns true for input, textarea, and contenteditable", () => {
    const input = document.createElement("input");
    const textarea = document.createElement("textarea");
    const div = document.createElement("div");
    div.contentEditable = "true";
    expect(isTypingTarget(input)).toBe(true);
    expect(isTypingTarget(textarea)).toBe(true);
    expect(isTypingTarget(div)).toBe(true);
  });

  it("returns false for non-editable elements and null", () => {
    expect(isTypingTarget(document.createElement("button"))).toBe(false);
    expect(isTypingTarget(null)).toBe(false);
  });
});

describe("useKeymap", () => {
  it("invokes a binding on the matching chord and prevents default", () => {
    const handler = vi.fn();
    renderHook(() => useKeymap({ "$mod+K": handler }));
    const prevented = pressKey("k", { ctrl: true });
    expect(handler).toHaveBeenCalledTimes(1);
    expect(prevented).toBe(true);
  });

  it("skips a binding while focus is in an input by default", () => {
    const handler = vi.fn();
    renderHook(() => useKeymap({ "$mod+N": handler }));
    const input = document.createElement("input");
    document.body.appendChild(input);
    input.focus();
    pressKey("n", { ctrl: true }, input);
    expect(handler).not.toHaveBeenCalled();
    input.remove();
  });

  it("fires when bypassInputFocus is true even with input focus", () => {
    const handler = vi.fn();
    renderHook(() =>
      useKeymap({ "$mod+K": { handler, bypassInputFocus: true } }),
    );
    const input = document.createElement("input");
    document.body.appendChild(input);
    input.focus();
    pressKey("k", { ctrl: true }, input);
    expect(handler).toHaveBeenCalledTimes(1);
    input.remove();
  });

  it("does not preventDefault when handler returns false", () => {
    renderHook(() => useKeymap({ "$mod+\\": () => false }));
    const prevented = pressKey("\\", { ctrl: true });
    expect(prevented).toBe(false);
  });

  it("unregisters bindings on unmount", () => {
    const handler = vi.fn();
    const { unmount } = renderHook(() => useKeymap({ "$mod+K": handler }));
    unmount();
    pressKey("k", { ctrl: true });
    expect(handler).not.toHaveBeenCalled();
  });
});
