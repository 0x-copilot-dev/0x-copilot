// CommandPalette — global ⌘K palette (substrate-shared).
//
// Two layers (PRD-D):
//   1. A static COMMAND LAUNCHER — `SHELL_COMMANDS` (the 13 v3 design commands).
//      Shown on an empty query (so ⌘K works as a keyboard launcher without
//      typing) and, once typing, filtered and shown ABOVE the search hits.
//      Activating a command calls the host's `onCommand(intent)`.
//   2. The live BACKEND SEARCH index — all entity/search data flows through
//      `PaletteSearchPort.search()`. This component imports no fetch/transport
//      primitive; hosts implement the port and pass it via `searchPort`.
//
// Keyboard: ↑↓ move selection across the flattened [commands, …hits] list
// (wraps); Enter activates the selected item; ESC closes. Input autofocuses on
// open. Search is debounced 150ms with a generation counter so a stale in-flight
// query can't overwrite newer results.
//
// ARIA: role="dialog"+aria-modal on the scrim; role="combobox" input;
// role="listbox" list; role="option" rows; aria-activedescendant mirrors the
// selected row id.

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
} from "react";

import type {
  PaletteHit,
  PaletteHitKind,
  PaletteSearchContext,
  PaletteSearchResponse,
} from "@0x-copilot/api-types";

import { Icon } from "../icons/Icon";
import type { PaletteSearchPort } from "../ports/PaletteSearchPort";
import { useOptionalDeploymentProfile } from "../providers/DeploymentProfileProvider";

import { PaletteHitRow } from "./PaletteHitRow";
import {
  SHELL_COMMANDS,
  filterShellCommands,
  type ShellCommand,
  type ShellCommandIntent,
} from "./shellCommands";

// Design placeholder (copilot.css .cmdk__in). Profile-neutral — the command
// layer is the same everywhere; "the team" is only appended on a team profile.
const PALETTE_PLACEHOLDER_SOLO = "Search commands, settings, tools…";
const PALETTE_PLACEHOLDER_TEAM = "Search the team, commands, settings, tools…";

export interface CommandPaletteProps {
  readonly open: boolean;
  readonly onRequestClose: () => void;
  readonly searchPort: PaletteSearchPort;
  /**
   * The static command launcher list. Defaults to the 13 v3 `SHELL_COMMANDS`.
   */
  readonly commands?: readonly ShellCommand[];
  /**
   * Activated when a command row fires. The host maps the intent to real
   * navigation (web router / desktop router). When omitted, a command is
   * close-only (the palette still closes).
   */
  readonly onCommand?: (intent: ShellCommandIntent) => void;
  /** Ranking context forwarded to the port. */
  readonly context?: PaletteSearchContext;
  /** Soft cap on hits per search. Server may clamp further. */
  readonly limit?: number;
  /**
   * Fired when a `navigation` search hit is activated. When omitted, activating
   * such a hit closes the palette with no other effect.
   */
  readonly onNavigate?: (route: string, hit: PaletteHit) => void;
  /**
   * Fired when an `action`/`command` search hit is activated. When omitted,
   * close-only.
   */
  readonly onRunAction?: (token: string, hit: PaletteHit) => void;
  /** Debounce window for the search input. Defaults to 150ms. */
  readonly debounceMs?: number;
  /**
   * @deprecated Superseded by the static command layer (`commands`). The
   * empty-query view now shows `SHELL_COMMANDS`, not host starter actions.
   * Kept optional so existing hosts compile; ignored.
   */
  readonly starterActions?: ReadonlyArray<PaletteHit>;
  /** @deprecated The design empty/no-match state is "No matches." with no hint. */
  readonly onConnectToolHint?: () => void;
}

// Search hits keep their kind grouping (Navigation / Entities / Actions);
// `command`-kind backend hits are folded into "Actions" since the static
// launcher now owns commands.
const KIND_ORDER: ReadonlyArray<PaletteHitKind> = [
  "navigation",
  "entity",
  "action",
  "command",
];

const GROUP_LABELS: Readonly<Record<PaletteHitKind, string>> = {
  navigation: "Navigation",
  entity: "Results",
  action: "Actions",
  command: "Commands",
};

const RESULTS_LIST_ID = "command-palette-results";

export function CommandPalette({
  open,
  onRequestClose,
  searchPort,
  commands = SHELL_COMMANDS,
  onCommand,
  context,
  limit,
  onNavigate,
  onRunAction,
  debounceMs = 150,
}: CommandPaletteProps): ReactElement | null {
  const profile = useOptionalDeploymentProfile();
  const placeholder =
    profile === "team" ? PALETTE_PLACEHOLDER_TEAM : PALETTE_PLACEHOLDER_SOLO;
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<ReadonlyArray<PaletteHit>>([]);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [hasSearched, setHasSearched] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLUListElement | null>(null);
  const generationRef = useRef(0);

  useEffect(() => {
    if (!open) {
      setQuery("");
      setHits([]);
      setSelectedIndex(0);
      setHasSearched(false);
      return;
    }
    const handle = setTimeout(() => {
      inputRef.current?.focus();
    }, 0);
    return () => clearTimeout(handle);
  }, [open]);

  // Debounced search (only when typing — the empty query shows commands only).
  useEffect(() => {
    if (!open) {
      return;
    }
    const trimmed = query.trim();
    if (trimmed.length === 0) {
      setHits([]);
      setHasSearched(false);
      return;
    }
    const myGen = ++generationRef.current;
    const handle = setTimeout(() => {
      searchPort
        .search({ q: trimmed, context, limit })
        .then((res: PaletteSearchResponse) => {
          if (myGen !== generationRef.current) {
            return;
          }
          setHits(res.hits);
          setHasSearched(true);
          setSelectedIndex(0);
        })
        .catch(() => {
          if (myGen !== generationRef.current) {
            return;
          }
          setHits([]);
          setHasSearched(true);
          setSelectedIndex(0);
        });
    }, debounceMs);
    return () => clearTimeout(handle);
  }, [open, query, searchPort, context, limit, debounceMs]);

  const isEmptyQuery = query.trim().length === 0;

  // Command layer: on empty query, all commands; while typing, filtered by
  // label+keyword. Shown above the search hits.
  const commandRows = useMemo<ReadonlyArray<ShellCommand>>(
    () => filterShellCommands(query, commands),
    [query, commands],
  );

  // Search hits only appear once typing.
  const searchHits = isEmptyQuery ? [] : hits;

  // Flattened selectable list: [commands…, searchHits…].
  const selectableCount = commandRows.length + searchHits.length;

  const grouped = useMemo<
    ReadonlyArray<readonly [PaletteHitKind, ReadonlyArray<PaletteHit>]>
  >(() => {
    const map = new Map<PaletteHitKind, PaletteHit[]>();
    for (const k of KIND_ORDER) {
      map.set(k, []);
    }
    for (const hit of searchHits) {
      map.get(hit.kind)?.push(hit);
    }
    return KIND_ORDER.map((k) => [k, map.get(k) ?? []] as const).filter(
      ([, list]) => list.length > 0,
    );
  }, [searchHits]);

  useEffect(() => {
    if (selectedIndex >= selectableCount && selectableCount > 0) {
      setSelectedIndex(0);
    }
  }, [selectableCount, selectedIndex]);

  const activateHit = useCallback(
    (hit: PaletteHit) => {
      if (hit.kind === "entity" && hit.target !== undefined) {
        const row = listRef.current?.querySelector<HTMLElement>(
          `[data-hit-id="${cssEscape(hit.id)}"] [data-testid="item-link"]`,
        );
        if (row !== null && row !== undefined) {
          row.click();
        }
      } else if (hit.kind === "navigation" && hit.route !== undefined) {
        onNavigate?.(hit.route, hit);
      } else if (
        (hit.kind === "action" || hit.kind === "command") &&
        hit.action_token !== undefined
      ) {
        onRunAction?.(hit.action_token, hit);
      }
      onRequestClose();
    },
    [onNavigate, onRunAction, onRequestClose],
  );

  const activateIndex = useCallback(
    (index: number) => {
      if (index < commandRows.length) {
        onCommand?.(commandRows[index].intent);
        onRequestClose();
        return;
      }
      const hit = searchHits[index - commandRows.length];
      if (hit !== undefined) {
        activateHit(hit);
      }
    },
    [commandRows, searchHits, onCommand, onRequestClose, activateHit],
  );

  const selectedRowId = useMemo<string | undefined>(() => {
    if (selectedIndex < commandRows.length) {
      const cmd = commandRows[selectedIndex];
      return cmd !== undefined ? commandDomId(cmd) : undefined;
    }
    const hit = searchHits[selectedIndex - commandRows.length];
    return hit !== undefined ? rowDomId(hit) : undefined;
  }, [commandRows, searchHits, selectedIndex]);

  const onInputKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLInputElement>) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onRequestClose();
        return;
      }
      if (selectableCount === 0) {
        return;
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setSelectedIndex((prev) => (prev + 1) % selectableCount);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setSelectedIndex(
          (prev) => (prev - 1 + selectableCount) % selectableCount,
        );
        return;
      }
      if (event.key === "Enter") {
        event.preventDefault();
        activateIndex(selectedIndex);
      }
    },
    [selectableCount, selectedIndex, activateIndex, onRequestClose],
  );

  if (!open) {
    return null;
  }

  const showNoResults = !isEmptyQuery && hasSearched && selectableCount === 0;

  // flatIdx runs across the command rows then the grouped search hits.
  let flatIdx = -1;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
      style={scrimStyle}
      onClick={onRequestClose}
      data-testid="command-palette"
    >
      <div
        style={cardStyle}
        onClick={(e) => e.stopPropagation()}
        data-testid="command-palette-card"
      >
        <div style={inputRowStyle}>
          <Icon name="search" size={15} style={searchIconStyle} />
          <input
            ref={inputRef}
            type="text"
            role="combobox"
            aria-label="Search"
            aria-expanded={true}
            aria-controls={RESULTS_LIST_ID}
            aria-autocomplete="list"
            aria-activedescendant={selectedRowId}
            value={query}
            placeholder={placeholder}
            onChange={(event) => {
              setQuery(event.target.value);
              setSelectedIndex(0);
            }}
            onKeyDown={onInputKeyDown}
            style={inputStyle}
            data-testid="command-palette-input"
          />
        </div>
        <ul
          ref={listRef}
          id={RESULTS_LIST_ID}
          role="listbox"
          aria-label="Results"
          style={listStyle}
          data-testid="command-palette-listbox"
        >
          {/* Command launcher (flat, no header — design .cmdk__row). */}
          {commandRows.map((cmd) => {
            flatIdx++;
            const isSelected = flatIdx === selectedIndex;
            const idx = flatIdx;
            return (
              <li key={cmd.id} role="presentation" style={commandLiStyle}>
                <button
                  type="button"
                  role="option"
                  id={commandDomId(cmd)}
                  aria-selected={isSelected}
                  data-testid="palette-command"
                  data-command-id={cmd.id}
                  style={commandRowStyle(isSelected)}
                  onClick={() => activateIndex(idx)}
                  onMouseEnter={() => setSelectedIndex(idx)}
                >
                  <Icon name={cmd.icon} size={14} style={commandIconStyle} />
                  <span style={commandLabelStyle}>{cmd.label}</span>
                  <span style={commandKeywordStyle}>{cmd.keyword}</span>
                </button>
              </li>
            );
          })}
          {/* Live search results, grouped by kind. */}
          {grouped.map(([kind, list]) => (
            <li key={kind} style={groupStyle} role="presentation">
              <div
                style={groupHeaderStyle}
                data-testid="palette-group-header"
                data-group-kind={kind}
              >
                {GROUP_LABELS[kind]}
              </div>
              <ul style={groupListStyle} role="presentation">
                {list.map((hit) => {
                  flatIdx++;
                  const isSelected = flatIdx === selectedIndex;
                  return (
                    <PaletteHitRow
                      key={hit.id}
                      hit={hit}
                      isSelected={isSelected}
                      id={rowDomId(hit)}
                      onActivate={activateHit}
                      onHover={() => setSelectedIndex(flatIdx)}
                    />
                  );
                })}
              </ul>
            </li>
          ))}
          {showNoResults ? (
            <li
              role="presentation"
              style={emptyStyle}
              data-testid="palette-no-results"
            >
              <div>No matches.</div>
            </li>
          ) : null}
        </ul>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function rowDomId(hit: PaletteHit): string {
  return `palette-hit-${hit.id}`;
}

function commandDomId(cmd: ShellCommand): string {
  return `palette-cmd-${cmd.id}`;
}

function cssEscape(value: string): string {
  const css = (globalThis as { CSS?: { escape?: (s: string) => string } }).CSS;
  if (css?.escape !== undefined) {
    return css.escape(value);
  }
  return value.replace(/[^a-zA-Z0-9_-]/g, (ch) => `\\${ch}`);
}

// ---------------------------------------------------------------------------
// Styles — design copilot.css `.cmdk*`
// ---------------------------------------------------------------------------

const scrimStyle: CSSProperties = {
  position: "fixed",
  inset: 0,
  backgroundColor: "rgba(4, 4, 6, 0.6)",
  backdropFilter: "blur(2px)",
  WebkitBackdropFilter: "blur(2px)",
  display: "flex",
  alignItems: "flex-start",
  justifyContent: "center",
  paddingTop: "13vh",
  zIndex: 80,
};

const cardStyle: CSSProperties = {
  width: "min(540px, 92vw)",
  backgroundColor: "var(--color-surface)",
  color: "var(--color-text)",
  borderRadius: 11,
  border: "1px solid var(--color-border-strong)",
  boxShadow: "0 26px 70px -18px rgba(0, 0, 0, 0.8)",
  display: "flex",
  flexDirection: "column",
  overflow: "hidden",
};

const inputRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  padding: "12px 14px",
  borderBottom: "1px solid var(--color-border)",
};

const searchIconStyle: CSSProperties = {
  color: "var(--color-text-subtle)",
  flex: "none",
};

const inputStyle: CSSProperties = {
  flex: 1,
  border: "none",
  outline: "none",
  padding: 0,
  fontSize: "var(--font-size-md)",
  background: "transparent",
  color: "inherit",
};

const listStyle: CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 5,
  maxHeight: 320,
  overflowY: "auto",
};

const commandLiStyle: CSSProperties = {
  listStyle: "none",
};

function commandRowStyle(selected: boolean): CSSProperties {
  return {
    display: "flex",
    alignItems: "center",
    gap: 10,
    width: "100%",
    padding: "8px 10px",
    border: "none",
    borderRadius: "var(--radius-sm)",
    background: selected ? "var(--color-surface-muted)" : "transparent",
    color: "inherit",
    cursor: "pointer",
    font: "inherit",
    textAlign: "left",
  };
}

const commandIconStyle: CSSProperties = {
  color: "var(--color-text-subtle)",
  flex: "none",
};

const commandLabelStyle: CSSProperties = {
  flex: 1,
  fontSize: "var(--font-size-xs)",
  color: "var(--color-text)",
};

const commandKeywordStyle: CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: 9.5,
  color: "var(--color-text-subtle)",
  whiteSpace: "nowrap",
};

const groupStyle: CSSProperties = {
  listStyle: "none",
  paddingTop: 4,
};

const groupHeaderStyle: CSSProperties = {
  padding: "6px 12px 2px 12px",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-2xs)",
  textTransform: "uppercase",
  letterSpacing: "0.1em",
  color: "var(--color-text-subtle)",
};

const groupListStyle: CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
};

const emptyStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "flex-start",
  gap: 6,
  padding: "12px 16px",
  color: "var(--color-text-subtle)",
  fontSize: "var(--font-size-sm)",
};
