import { Button, Card, Field } from "@enterprise-search/design-system";
import type { ReactElement } from "react";
import { useCallback, useEffect, useState } from "react";
import { useUserPreferences } from "../../me/useUserPreferences";

/**
 * Settings → You → Shortcuts.
 *
 * Renders the FE keymap registry from PR 2.2 with override slots. The
 * registry is the source of truth — orphan overrides (override id no
 * longer in registry) are silently ignored at render so a stale
 * preference row never blocks rendering.
 *
 * Chord recording: clicking "Record" on a row swaps the slot for a
 * keydown listener that captures the next chord and writes it to the
 * preferences row. The chord uses tinykeys' ``$mod+K`` syntax so it
 * matches what ``useKeymap`` parses.
 */

interface ShortcutEntry {
  id: string;
  label: string;
  category: "Navigation" | "Composer" | "Approvals";
  /** Default chord — what the keymap registers when no override is set. */
  defaultChord: string;
}

// PR 2.2's keymap currently registers four global chords. Adding a new
// shortcut here is paired with adding it to ``apps/frontend/src/app/
// keymap.ts``'s registry call site (Sidebar.tsx today) — until a
// formal registry export ships, this list is the contract.
const SHORTCUT_REGISTRY: ReadonlyArray<ShortcutEntry> = [
  {
    id: "chat.new",
    label: "Start a new chat",
    category: "Navigation",
    defaultChord: "$mod+N",
  },
  {
    id: "chat.search",
    label: "Focus chat search",
    category: "Navigation",
    defaultChord: "$mod+K",
  },
  {
    id: "chat.toggle.sidebar",
    label: "Toggle sidebar",
    category: "Navigation",
    defaultChord: "$mod+\\",
  },
  {
    id: "chat.approve.focused",
    label: "Approve focused approval card",
    category: "Approvals",
    defaultChord: "$mod+Enter",
  },
];

const CATEGORY_ORDER: ReadonlyArray<ShortcutEntry["category"]> = [
  "Navigation",
  "Composer",
  "Approvals",
];

export function Shortcuts(): ReactElement {
  const preferences = useUserPreferences();
  const data = preferences.data;
  const overrides = data?.shortcuts.overrides ?? {};
  const [recording, setRecording] = useState<string | null>(null);

  const onCaptured = useCallback(
    (id: string, chord: string) => {
      void preferences.save({
        shortcuts: { overrides: { ...overrides, [id]: chord } },
      });
      setRecording(null);
    },
    [overrides, preferences],
  );

  const onClear = useCallback(
    (id: string) => {
      const next = { ...overrides };
      delete next[id];
      void preferences.save({ shortcuts: { overrides: next } });
    },
    [overrides, preferences],
  );

  const onResetAll = useCallback(() => {
    void preferences.save({ shortcuts: { overrides: {} } });
  }, [preferences]);

  if (preferences.loading && data === null) {
    return (
      <div className="settings-section">
        <h2>Shortcuts</h2>
        <Card>
          <p>Loading preferences…</p>
        </Card>
      </div>
    );
  }

  return (
    <div className="settings-section">
      <div className="settings-section__header">
        <div>
          <h2>Shortcuts</h2>
          <p>Override default chords. Click Record then press the new combo.</p>
        </div>
        {Object.keys(overrides).length > 0 ? (
          <Button
            type="button"
            variant="secondary"
            title="Reset all shortcut overrides"
            onClick={onResetAll}
          >
            Reset to defaults
          </Button>
        ) : null}
      </div>

      {CATEGORY_ORDER.map((category) => {
        const rows = SHORTCUT_REGISTRY.filter((s) => s.category === category);
        if (rows.length === 0) {
          return null;
        }
        return (
          <Card key={category} className="me-shortcuts-card">
            <h3 className="me-shortcuts-category">{category}</h3>
            <ul className="me-shortcuts-list">
              {rows.map((entry) => {
                const override = overrides[entry.id];
                return (
                  <li key={entry.id} className="me-shortcuts-row">
                    <Field label={entry.label}>
                      <div className="me-shortcuts-row__controls">
                        {recording === entry.id ? (
                          <ChordRecorder
                            onCapture={(chord) => onCaptured(entry.id, chord)}
                            onCancel={() => setRecording(null)}
                          />
                        ) : (
                          <code
                            className="me-shortcuts-chord"
                            title="Current chord"
                          >
                            {prettyChord(override ?? entry.defaultChord)}
                          </code>
                        )}
                        {recording !== entry.id ? (
                          <Button
                            type="button"
                            variant="secondary"
                            title="Record a new chord"
                            onClick={() => setRecording(entry.id)}
                          >
                            Record
                          </Button>
                        ) : null}
                        {override !== undefined && recording !== entry.id ? (
                          <Button
                            type="button"
                            variant="ghost"
                            title="Reset to default"
                            onClick={() => onClear(entry.id)}
                          >
                            Reset
                          </Button>
                        ) : null}
                      </div>
                    </Field>
                  </li>
                );
              })}
            </ul>
          </Card>
        );
      })}

      {preferences.error ? (
        <p className="app-error">{preferences.error}</p>
      ) : null}
    </div>
  );
}

function ChordRecorder({
  onCapture,
  onCancel,
}: {
  onCapture: (chord: string) => void;
  onCancel: () => void;
}): ReactElement {
  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>): void {
    event.preventDefault();
    if (event.key === "Escape") {
      onCancel();
      return;
    }
    if (event.key === "Tab") {
      // Don't capture Tab — the user might be navigating away.
      return;
    }
    if (
      event.key === "Meta" ||
      event.key === "Control" ||
      event.key === "Shift" ||
      event.key === "Alt"
    ) {
      return;
    }
    const parts: string[] = [];
    if (event.metaKey || event.ctrlKey) {
      parts.push("$mod");
    }
    if (event.shiftKey) {
      parts.push("Shift");
    }
    if (event.altKey) {
      parts.push("Alt");
    }
    parts.push(event.key.length === 1 ? event.key.toUpperCase() : event.key);
    onCapture(parts.join("+"));
  }

  return (
    <input
      type="text"
      className="ui-input me-shortcuts-recorder"
      placeholder="Press a chord… (Esc cancels)"
      autoFocus
      onKeyDown={onKeyDown}
      onBlur={onCancel}
      readOnly
    />
  );
}

function prettyChord(chord: string): string {
  const isMac =
    typeof navigator !== "undefined" &&
    /Mac|iPhone|iPad/.test(navigator.platform);
  return chord
    .split("+")
    .map((part) =>
      part === "$mod" ? (isMac ? "⌘" : "Ctrl") : part === "Enter" ? "↵" : part,
    )
    .join(" + ");
}
