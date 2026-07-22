import { useEffect } from "react";
import { tinykeys } from "tinykeys";

/**
 * Global keymap layer for the chat surface (PR 2.2).
 *
 * Wraps `tinykeys` with a single conventions:
 *
 *   1. `$mod` resolves to `Cmd` on macOS and `Ctrl` elsewhere — the chord
 *      form documented in the design doc (`⌘N`, `⌘K`, `⌘\`, `⌘↩`) maps
 *      to `$mod+N`, `$mod+K`, `$mod+\\`, `$mod+Enter` respectively.
 *
 *   2. Bindings are **input-focus-aware**. When the user is typing in an
 *      `<input>`, `<textarea>`, or `[contenteditable]`, the binding fires
 *      ONLY when its key opts in via `bypassInputFocus: true`. Default-
 *      off avoids stealing keystrokes while the user is typing.
 *
 *   3. Bindings register against `window` so they catch the chord no
 *      matter where focus currently lives in the chat surface. The
 *      cleanup function removes them on unmount; StrictMode double-
 *      invocation is safe.
 *
 * Why `tinykeys` and not a hand-rolled listener: chord parsing,
 * `$mod` resolution, repeat suppression, and key-up vs key-down handling
 * are all small but easy-to-get-wrong details. tinykeys is ~400 B
 * minified, has no runtime deps, and is platform-aware. We considered
 * `cmdk`/`kbar` (command palettes — too much for 4 chords),
 * `react-hotkeys-hook` (≈3 KB and React-only), and `mousetrap`
 * (unmaintained since 2018); see `docs/new-design/pr-2.2` § 3.4.
 */

export type KeymapHandler = (event: KeyboardEvent) => void | boolean;

export interface KeymapBinding {
  /** Handler to invoke. Return `false` to opt out of preventDefault. */
  handler: KeymapHandler;
  /**
   * Fire even when the user is typing in an input/textarea/contenteditable.
   * Default `false` — set `true` for `$mod+K`-style bindings whose intent
   * is "focus the search field", which itself happens to be an `<input>`.
   */
  bypassInputFocus?: boolean;
}

export type KeymapBindings = Record<string, KeymapHandler | KeymapBinding>;

/**
 * Register chord → handler bindings on `window` for the lifetime of the
 * calling component. Re-registers on bindings change; cleans up on unmount.
 *
 * The component owns the lifecycle — there is no provider; pass a stable
 * (`useMemo` / module-level) bindings object to avoid churning the
 * subscription on every render.
 */
export function useKeymap(bindings: KeymapBindings): void {
  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    const wrapped: Record<string, (event: KeyboardEvent) => void> = {};
    for (const [chord, value] of Object.entries(bindings)) {
      const binding: KeymapBinding =
        typeof value === "function" ? { handler: value } : value;
      wrapped[chord] = (event: KeyboardEvent) => {
        if (!binding.bypassInputFocus && isTypingTarget(event.target)) {
          return;
        }
        const result = binding.handler(event);
        // Default to preventDefault so a chord like ⌘N doesn't open a new
        // browser window; opt-out by returning `false`.
        if (result !== false && !event.defaultPrevented) {
          event.preventDefault();
        }
      };
    }
    // tinykeys v4 introduced a default `ignore` that silently drops any event
    // whose target is an input/select/textarea/contenteditable (unless it is
    // the listener target itself). That default runs BEFORE our wrapper, so a
    // `bypassInputFocus: true` binding could never fire while the user is
    // typing — the exact case it exists for (⌘K → focus search). Override it
    // so input-focus policy lives in ONE place (the wrapper above); keep the
    // useful parts of the default (held-key repeat + IME composition).
    return tinykeys(window, wrapped, {
      ignore: (event) => event.repeat || event.isComposing,
    });
  }, [bindings]);
}

/** True when the keyboard event would land on an editable control. */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  if (target.tagName === "INPUT" || target.tagName === "TEXTAREA") {
    return true;
  }
  // `isContentEditable` is the right answer in real browsers; jsdom returns
  // `undefined` for un-attached elements, so we accept either it or the
  // attribute reflection.
  return target.isContentEditable === true || target.contentEditable === "true";
}
