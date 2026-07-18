// useRunMode — KeyValueStore-backed Studio/Focus mode owner for the Run
// destination, plus the global ⌘M / Ctrl+M toggle.
//
// Source: docs/plan/desktop-redesign/phase-3/PRD.md
//   - FR-3.7: persist the mode in the KeyValueStore port under a
//     per-conversation key; coerce any legacy "auto" value to "studio"
//     on read (Auto was dropped in PR-3.1).
//   - FR-3.8: a global ⌘M (Meta/Ctrl+M) shortcut toggles Studio↔Focus
//     while Run is the active destination, and MUST NOT fire while a
//     text input / composer is focused.
//
// Ownership (PRD §5 "Single source of truth"): the *mode value* is owned
// here; ThreadCanvas stays a controlled presentation host (`mode` /
// `onModeChange`). This hook is the thing that reads/persists the value
// and feeds it to `ThreadCanvas.mode` in PR-3.5.
//
// Substrate-agnostic: persistence goes through the KeyValueStore port
// (web → localStorage, desktop → extension Memento); the keyboard
// listener mounts on `globalThis.document` exactly like
// `shell/useCommandPaletteHotkey`, so non-DOM hosts no-op.

import { useCallback, useEffect, useRef, useState } from "react";

import type { ConversationId } from "@0x-copilot/api-types";

import { useKeyValueStore } from "../../providers/KeyValueStoreProvider";
import type { KeyValueStore } from "../../storage/key-value-store";
import type { ThreadMode } from "../../thread-canvas";

/**
 * The Run cockpit layout mode. Aliased to `ThreadMode` (owned by
 * `ThreadCanvas`) rather than re-declared so the two never drift and the
 * value flows straight into `ThreadCanvas.mode` with no coercion — single
 * source of truth for the `"studio" | "focus"` union.
 */
export type RunMode = ThreadMode;

/** Default layout when nothing is persisted (PRD US-3.2: default Studio). */
export const DEFAULT_RUN_MODE: RunMode = "studio";

// Per-conversation KV namespace. Shares the `chats.thread.<id>.*` prefix
// used by `apps/frontend/src/features/chat/chatDepthKv.ts` — one thread
// namespace, one suffix per persisted preference.
const RUN_MODE_KEY_PREFIX = "chats.thread.";
const RUN_MODE_KEY_SUFFIX = ".run_mode";

/** Per-conversation KeyValueStore key for the persisted Run mode. */
export function runModeKey(conversationId: ConversationId): string {
  return `${RUN_MODE_KEY_PREFIX}${conversationId}${RUN_MODE_KEY_SUFFIX}`;
}

/**
 * Read the persisted mode for a conversation. Only the literal `"focus"`
 * resolves to Focus; everything else — `"studio"`, the legacy `"auto"`
 * value (FR-3.7 coercion), `null`, and any unrecognised string — resolves
 * to the default Studio layout. This "unknown ⇒ default" shape means a
 * future mode value written by a newer client degrades safely instead of
 * throwing on an older one.
 */
export function readRunMode(
  store: KeyValueStore,
  conversationId: ConversationId,
): RunMode {
  return store.get(runModeKey(conversationId)) === "focus"
    ? "focus"
    : DEFAULT_RUN_MODE;
}

/** Persist the mode for a conversation. */
export function writeRunMode(
  store: KeyValueStore,
  conversationId: ConversationId,
  mode: RunMode,
): void {
  store.set(runModeKey(conversationId), mode);
}

export interface UseRunModeOptions {
  /** Conversation whose mode is read/persisted (per-conversation key). */
  readonly conversationId: ConversationId;
  /**
   * Gates the global ⌘M listener. Pass `false` when Run is not the
   * active destination so the shortcut only toggles while Run is live
   * (FR-3.8). Defaults to `true`.
   */
  readonly enabled?: boolean;
}

export interface UseRunModeResult {
  /** Current layout mode. */
  readonly mode: RunMode;
  /** Set an explicit mode; persists to the KeyValueStore. */
  readonly setMode: (mode: RunMode) => void;
  /** Flip Studio↔Focus; persists to the KeyValueStore. */
  readonly toggle: () => void;
}

/**
 * True when the given element captures text entry (a composer / input /
 * textarea / contenteditable host), so ⌘M should not steal the chord
 * from it (FR-3.8). Non-text inputs (checkbox, button, …) do NOT capture
 * "m", so the toggle still fires when one is focused.
 */
const NON_TEXT_INPUT_TYPES = new Set<string>([
  "button",
  "checkbox",
  "color",
  "file",
  "hidden",
  "image",
  "radio",
  "range",
  "reset",
  "submit",
]);

function isEditableElement(element: Element | null): boolean {
  if (element === null) {
    return false;
  }
  if (element.tagName === "TEXTAREA") {
    return true;
  }
  if (element.tagName === "INPUT") {
    return !NON_TEXT_INPUT_TYPES.has((element as HTMLInputElement).type);
  }
  if (element instanceof globalThis.HTMLElement) {
    return element.isContentEditable;
  }
  return false;
}

export function useRunMode({
  conversationId,
  enabled = true,
}: UseRunModeOptions): UseRunModeResult {
  const store = useKeyValueStore();
  const [mode, setModeState] = useState<RunMode>(() =>
    readRunMode(store, conversationId),
  );

  // Re-hydrate when the conversation (or store) changes — mode is
  // persisted per conversation, so switching runs restores that run's
  // last-used layout (US-3.2 restore-on-reopen).
  useEffect(() => {
    setModeState(readRunMode(store, conversationId));
  }, [store, conversationId]);

  const setMode = useCallback(
    (next: RunMode): void => {
      writeRunMode(store, conversationId, next);
      setModeState(next);
    },
    [store, conversationId],
  );

  // Latest-mode ref so the ⌘M listener toggles from current state without
  // re-subscribing on every mode change — one stable global listener.
  const modeRef = useRef(mode);
  useEffect(() => {
    modeRef.current = mode;
  }, [mode]);

  const toggle = useCallback((): void => {
    setMode(modeRef.current === "studio" ? "focus" : "studio");
  }, [setMode]);

  // ⌘M / Ctrl+M global toggle (FR-3.8). Same substrate convention as
  // useCommandPaletteHotkey: listener on `globalThis.document`, detached
  // on unmount / dependency change. Gated to `enabled` (Run active) and
  // suppressed while a text input / composer is focused.
  useEffect(() => {
    if (!enabled) {
      return;
    }
    const doc = globalThis.document;
    if (doc === undefined) {
      return;
    }
    const onKeyDown = (event: KeyboardEvent): void => {
      const isModeToggle =
        (event.metaKey || event.ctrlKey) &&
        !event.shiftKey &&
        !event.altKey &&
        event.key.toLowerCase() === "m";
      if (!isModeToggle) {
        return;
      }
      if (isEditableElement(doc.activeElement)) {
        return;
      }
      event.preventDefault();
      toggle();
    };
    doc.addEventListener("keydown", onKeyDown);
    return () => {
      doc.removeEventListener("keydown", onKeyDown);
    };
  }, [enabled, toggle]);

  return { mode, setMode, toggle };
}
