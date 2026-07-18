import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";

import { useRouter } from "../providers/RouterProvider";
import type { ArtifactRoute } from "../routing/router";
import { ROUTE_TABLE } from "../routing/route-table";

export interface CommandPaletteEntry {
  readonly id: string;
  readonly label: string;
  readonly hint?: string;
  readonly route: ArtifactRoute;
}

export interface CommandPaletteProps {
  /**
   * Optional extra entries appended to the built-in destination list.
   * Phase 1 ships static destinations; Phase 2+ will pass Transport-backed
   * chat / project / library results through this prop.
   */
  readonly extraEntries?: ReadonlyArray<CommandPaletteEntry>;
}

const DESTINATION_ENTRIES: ReadonlyArray<CommandPaletteEntry> = Object.values(
  ROUTE_TABLE,
).map((entry) => ({
  id: `destination:${entry.kind}`,
  label: entry.label,
  hint: "Destination",
  route: defaultRouteForKind(entry.kind),
}));

// Phase 1 placeholder data. Phase 2 wires real lookups via Transport.
const PLACEHOLDER_ENTRIES: ReadonlyArray<CommandPaletteEntry> = [
  {
    id: "chat:welcome",
    label: "Welcome to Copilot",
    hint: "Chat",
    route: { kind: "chat", conversationId: "welcome" },
  },
  {
    id: "chat:q4-revenue-review",
    label: "Q4 revenue review",
    hint: "Chat",
    route: { kind: "chat", conversationId: "q4-revenue-review" },
  },
  {
    id: "workspace:acme",
    label: "Acme workspace",
    hint: "Workspace",
    route: { kind: "workspace", workspaceId: "wsp_acme" },
  },
];

export function CommandPalette({
  extraEntries,
}: CommandPaletteProps): React.ReactNode {
  const router = useRouter<ArtifactRoute | null>();
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const allEntries = useMemo<ReadonlyArray<CommandPaletteEntry>>(
    () => [
      ...DESTINATION_ENTRIES,
      ...PLACEHOLDER_ENTRIES,
      ...(extraEntries ?? []),
    ],
    [extraEntries],
  );

  const results = useMemo<ReadonlyArray<CommandPaletteEntry>>(() => {
    const q = query.trim().toLowerCase();
    if (q.length === 0) {
      return allEntries;
    }
    return allEntries.filter((entry) => entry.label.toLowerCase().includes(q));
  }, [allEntries, query]);

  // Keep selection in bounds as results shrink.
  useEffect(() => {
    if (selectedIndex >= results.length && results.length > 0) {
      setSelectedIndex(0);
    }
  }, [results.length, selectedIndex]);

  const close = useCallback(() => {
    setIsOpen(false);
    setQuery("");
    setSelectedIndex(0);
  }, []);

  const choose = useCallback(
    (entry: CommandPaletteEntry) => {
      router.navigate(entry.route);
      close();
    },
    [router, close],
  );

  // Cmd+K / Ctrl+K global toggle, Esc close. Mounted on globalThis.document
  // (substrate touchpoint — see HashRouter for the same convention).
  useEffect(() => {
    const doc = globalThis.document;
    if (doc === undefined) {
      return;
    }
    const onKeyDown = (event: KeyboardEvent): void => {
      const isPaletteToggle =
        (event.metaKey || event.ctrlKey) &&
        !event.shiftKey &&
        !event.altKey &&
        event.key.toLowerCase() === "k";
      if (isPaletteToggle) {
        event.preventDefault();
        setIsOpen((prev) => !prev);
        return;
      }
      if (event.key === "Escape" && isOpen) {
        event.preventDefault();
        close();
      }
    };
    doc.addEventListener("keydown", onKeyDown);
    return () => {
      doc.removeEventListener("keydown", onKeyDown);
    };
  }, [isOpen, close]);

  // Focus the input each time the palette opens.
  useEffect(() => {
    if (isOpen) {
      inputRef.current?.focus();
    }
  }, [isOpen]);

  const onInputKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLInputElement>) => {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setSelectedIndex((prev) =>
          results.length === 0 ? 0 : (prev + 1) % results.length,
        );
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setSelectedIndex((prev) =>
          results.length === 0
            ? 0
            : (prev - 1 + results.length) % results.length,
        );
        return;
      }
      if (event.key === "Enter") {
        event.preventDefault();
        const entry = results[selectedIndex];
        if (entry !== undefined) {
          choose(entry);
        }
      }
    },
    [results, selectedIndex, choose],
  );

  if (!isOpen) {
    return null;
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
      style={scrimStyle}
      onClick={close}
    >
      <div style={cardStyle} onClick={(e) => e.stopPropagation()}>
        <input
          ref={inputRef}
          type="text"
          role="combobox"
          aria-label="Search destinations and chats"
          aria-expanded={true}
          aria-controls="command-palette-results"
          value={query}
          placeholder="Search destinations, chats, projects…"
          onChange={(event) => {
            setQuery(event.target.value);
            setSelectedIndex(0);
          }}
          onKeyDown={onInputKeyDown}
          style={inputStyle}
        />
        <ul
          id="command-palette-results"
          role="listbox"
          aria-label="Results"
          style={listStyle}
        >
          {results.length === 0 ? (
            <li role="option" aria-selected={false} style={emptyStyle}>
              No matches
            </li>
          ) : (
            results.map((entry, idx) => {
              const isSelected = idx === selectedIndex;
              return (
                <li
                  key={entry.id}
                  role="option"
                  aria-selected={isSelected}
                  data-selected={isSelected ? "true" : undefined}
                  style={isSelected ? rowSelectedStyle : rowStyle}
                  onMouseEnter={() => setSelectedIndex(idx)}
                  onClick={() => choose(entry)}
                >
                  <span style={labelStyle}>{entry.label}</span>
                  {entry.hint !== undefined ? (
                    <span style={hintStyle}>{entry.hint}</span>
                  ) : null}
                </li>
              );
            })
          )}
        </ul>
      </div>
    </div>
  );
}

function defaultRouteForKind(kind: ArtifactRoute["kind"]): ArtifactRoute {
  switch (kind) {
    case "chat":
      return { kind: "chat", conversationId: "new" };
    case "conversation":
      return { kind: "conversation", conversationId: "new" };
    case "run":
      return { kind: "run", runId: "latest" };
    case "subagent":
      return { kind: "subagent", runId: "latest", subagentId: "root" };
    case "tool-result":
      return { kind: "tool-result", runId: "latest", stepId: "1" };
    case "mcp":
      return { kind: "mcp", serverId: "all" };
    case "mcp-tool":
      return { kind: "mcp-tool", serverId: "all", toolName: "all" };
    case "skill":
      return { kind: "skill", skillId: "all" };
    case "workspace":
      return { kind: "workspace", workspaceId: "current" };
  }
}

const scrimStyle: CSSProperties = {
  position: "fixed",
  inset: 0,
  backgroundColor: "rgba(0, 0, 0, 0.45)",
  display: "flex",
  alignItems: "flex-start",
  justifyContent: "center",
  paddingTop: "12vh",
  zIndex: 1000,
};

const cardStyle: CSSProperties = {
  width: "min(560px, 92vw)",
  maxHeight: "60vh",
  backgroundColor: "#1a1a1a",
  color: "#e8e8e8",
  borderRadius: 8,
  border: "1px solid #333",
  boxShadow: "0 24px 48px rgba(0, 0, 0, 0.4)",
  display: "flex",
  flexDirection: "column",
  overflow: "hidden",
};

const inputStyle: CSSProperties = {
  border: "none",
  borderBottom: "1px solid #2a2a2a",
  outline: "none",
  padding: "14px 16px",
  fontSize: "var(--font-size-md)",
  background: "transparent",
  color: "inherit",
};

const listStyle: CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 4,
  overflowY: "auto",
};

const rowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  padding: "8px 12px",
  borderRadius: 4,
  cursor: "pointer",
};

const rowSelectedStyle: CSSProperties = {
  ...rowStyle,
  backgroundColor: "#2a2a2a",
};

const labelStyle: CSSProperties = {
  fontSize: "var(--font-size-md)",
};

const hintStyle: CSSProperties = {
  fontSize: "var(--font-size-xs)",
  color: "#888",
  marginLeft: 12,
};

const emptyStyle: CSSProperties = {
  padding: "12px 16px",
  color: "#888",
  fontSize: "var(--font-size-md)",
};
