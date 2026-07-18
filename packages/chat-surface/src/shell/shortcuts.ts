// shortcuts.ts — the DESIGN-SPEC.md §6 keyboard-shortcut table: the single
// source of truth (SSOT) mapping each chord to a named intent + display
// metadata.
//
// Consumed by:
//   • `useShellShortcuts` (this PR) — attaches a keydown listener and
//     dispatches each chord to its caller-supplied callback.
//   • the Shortcuts settings page (DESIGN-SPEC.md §4 Appearance → Shortcuts,
//     PR-6.8) — renders `{ chord.display, label }` read-only.
//
// FR-6.15 forbids a second copy of this mapping — add or adjust chords here
// only. The chord list is exactly DESIGN-SPEC.md §6 (twelve chords); do not
// invent chords.

/**
 * The named intent a chord maps to. The values double as the callback prop
 * names on {@link UseShellShortcutsOptions} (FR-6.10), so the options object
 * is literally a partial record keyed by intent.
 */
export type ShortcutIntent =
  | "onNewRun"
  | "onOpenPalette"
  | "onOpenSettings"
  | "onOpenLocalModelPicker"
  | "onSearchActivity"
  | "onSwitchMode"
  | "onRewind"
  | "onStepForward"
  | "onJumpLive"
  | "onPauseRun"
  | "onApprove"
  | "onReject";

/**
 * Whether a chord is always available (`global`) or only meaningful while the
 * Run cockpit is the active destination (`run`). The hook fires every chord
 * regardless of scope; the desktop wiring (PR-6.6, FR-6.13) uses `scope` to
 * guard the run-scoped callbacks so they no-op off Run.
 */
export type ShortcutScope = "global" | "run";

/**
 * The structural shape of the fields a chord needs from a `KeyboardEvent` to
 * be matched. A DOM `KeyboardEvent` is assignable to this, and plain objects
 * satisfy it in tests — keeping {@link matchesChord} framework-agnostic.
 */
export interface ShortcutKeyEvent {
  readonly key: string;
  readonly metaKey: boolean;
  readonly ctrlKey: boolean;
  readonly shiftKey: boolean;
  readonly altKey: boolean;
}

/**
 * A single chord: its human-readable display form plus the exact match data.
 * The command modifier (⌘ on macOS / Ctrl elsewhere) is required for every
 * §6 chord and Alt is always forbidden, so those invariants live in
 * {@link matchesChord} rather than being repeated per chord.
 */
export interface ShortcutChord {
  /** Display form shown in the Shortcuts settings page, e.g. `"⌘⇧M"`. */
  readonly display: string;
  /**
   * The normalized `KeyboardEvent.key` this chord matches. Single-character
   * keys are lowercased (`"n"`, `","`, `"."`); named keys keep their DOM
   * spelling (`"ArrowLeft"`, `"Enter"`, `"Backspace"`).
   */
  readonly key: string;
  /**
   * Whether Shift must be held. This is what keeps `⌘⇧M` (shift) distinct
   * from `⌘M` (no shift) — see FR-6.12.
   */
  readonly shift: boolean;
}

/** One row of the shortcut table. */
export interface ShellShortcut {
  readonly chord: ShortcutChord;
  readonly intent: ShortcutIntent;
  /** Human-readable action name (DESIGN-SPEC.md §6). */
  readonly label: string;
  readonly scope: ShortcutScope;
  /**
   * Whether the chord still fires while a text input/textarea/select/
   * contenteditable is focused. Only `⌘K` and `⌘,` are input-safe (FR-6.11).
   */
  readonly inputSafe: boolean;
}

export type ShellShortcutMap = readonly ShellShortcut[];

/**
 * The per-intent callback map: a partial record keyed by intent (FR-6.10).
 * Every callback is optional — a chord whose callback is left undefined is a
 * no-op.
 */
export type ShellShortcutCallbacks = Partial<
  Record<ShortcutIntent, () => void>
>;

/**
 * Options for {@link useShellShortcuts}: the callback map plus `enabled`
 * (default `true`), which detaches the listener when false — the FR-6.12 risk
 * mitigation against stealing keystrokes.
 */
export type UseShellShortcutsOptions = ShellShortcutCallbacks & {
  /** When false, no listener is attached. Defaults to true. */
  readonly enabled?: boolean;
};

/**
 * The DESIGN-SPEC.md §6 chord set. Five global chords, then the seven
 * run-scoped chords. `inputSafe` is true only for `⌘K` / `⌘,`.
 */
export const SHELL_SHORTCUTS: ShellShortcutMap = [
  // --- Global chords (available anywhere in the shell) ---
  {
    chord: { display: "⌘N", key: "n", shift: false },
    intent: "onNewRun",
    label: "New run",
    scope: "global",
    inputSafe: false,
  },
  {
    chord: { display: "⌘K", key: "k", shift: false },
    intent: "onOpenPalette",
    label: "Command palette",
    scope: "global",
    inputSafe: true,
  },
  {
    chord: { display: "⌘,", key: ",", shift: false },
    intent: "onOpenSettings",
    label: "Settings",
    scope: "global",
    inputSafe: true,
  },
  {
    chord: { display: "⌘⇧M", key: "m", shift: true },
    intent: "onOpenLocalModelPicker",
    label: "Local model picker",
    scope: "global",
    inputSafe: false,
  },
  {
    chord: { display: "⌘⇧F", key: "f", shift: true },
    intent: "onSearchActivity",
    label: "Search activity",
    scope: "global",
    inputSafe: false,
  },
  // --- Run-scoped chords (only meaningful while the Run cockpit is active) ---
  {
    chord: { display: "⌘M", key: "m", shift: false },
    intent: "onSwitchMode",
    label: "Switch mode",
    scope: "run",
    inputSafe: false,
  },
  {
    chord: { display: "⌘←", key: "ArrowLeft", shift: false },
    intent: "onRewind",
    label: "Rewind timeline",
    scope: "run",
    inputSafe: false,
  },
  {
    chord: { display: "⌘→", key: "ArrowRight", shift: false },
    intent: "onStepForward",
    label: "Step forward",
    scope: "run",
    inputSafe: false,
  },
  {
    chord: { display: "⌘L", key: "l", shift: false },
    intent: "onJumpLive",
    label: "Jump to live",
    scope: "run",
    inputSafe: false,
  },
  {
    chord: { display: "⌘.", key: ".", shift: false },
    intent: "onPauseRun",
    label: "Pause run",
    scope: "run",
    inputSafe: false,
  },
  {
    chord: { display: "⌘↵", key: "Enter", shift: false },
    intent: "onApprove",
    label: "Approve action",
    scope: "run",
    inputSafe: false,
  },
  {
    chord: { display: "⌘⌫", key: "Backspace", shift: false },
    intent: "onReject",
    label: "Reject action",
    scope: "run",
    inputSafe: false,
  },
];

/**
 * Normalize a `KeyboardEvent.key` for chord comparison: single-character keys
 * are lowercased so a shifted letter (`"M"`) matches its canonical key
 * (`"m"`); named keys (`"ArrowLeft"`, `"Enter"`) are returned unchanged.
 */
export function normalizeShortcutKey(key: string): string {
  return key.length === 1 ? key.toLowerCase() : key;
}

/**
 * True when `event` matches `chord` with **exact** modifiers. Every §6 chord
 * requires the command modifier (`metaKey` on macOS / `ctrlKey` elsewhere) and
 * forbids Alt; Shift is required only for the chord that opts in via
 * `chord.shift`. This exactness is what keeps `⌘⇧M` from also firing `⌘M` and
 * rejects any event carrying extra or missing modifiers (FR-6.12).
 */
export function matchesChord(
  event: ShortcutKeyEvent,
  chord: ShortcutChord,
): boolean {
  return (
    (event.metaKey || event.ctrlKey) &&
    !event.altKey &&
    event.shiftKey === chord.shift &&
    normalizeShortcutKey(event.key) === chord.key
  );
}
