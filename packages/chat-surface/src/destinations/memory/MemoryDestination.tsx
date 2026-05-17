import {
  Badge,
  Card,
  IconButton,
  TextInput,
} from "@enterprise-search/design-system";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import { useTransport } from "../../providers/TransportProvider";

export type MemoryType = "user" | "project" | "reference";

export interface Memory {
  readonly id: string;
  readonly type: MemoryType;
  readonly title: string;
  readonly description: string;
  readonly lastUpdatedIso: string;
  readonly pinned: boolean;
}

export interface MemoryDestinationProps {
  readonly onTogglePin?: (memory: Memory) => void;
  readonly onDelete?: (memory: Memory) => void;
}

interface MemoryResponse {
  readonly memories: readonly Memory[];
}

type TabState =
  | { readonly status: "idle" }
  | { readonly status: "loading" }
  | { readonly status: "error"; readonly message: string }
  | { readonly status: "ready"; readonly memories: readonly Memory[] };

interface TabDef {
  readonly type: MemoryType;
  readonly label: string;
  readonly tagLabel: string;
}

const TABS: readonly TabDef[] = [
  { type: "user", label: "User memories", tagLabel: "User" },
  { type: "project", label: "Project memories", tagLabel: "Project" },
  { type: "reference", label: "Reference memories", tagLabel: "Reference" },
];

const TAG_TONE: Record<
  MemoryType,
  "neutral" | "success" | "warning" | "danger" | "accent"
> = {
  user: "accent",
  project: "success",
  reference: "neutral",
};

// Design tokens (see packages/design-system/src/styles.css). Names are kept
// for readability at use-sites; values are CSS variables so Settings →
// Appearance theme/accent changes flow through automatically.
const PANEL_BG = "var(--color-bg)";
const PANEL_BORDER = "var(--color-border)";
const TEXT_PRIMARY = "var(--color-text)";
const TEXT_SECONDARY = "var(--color-text-muted)";
const ACCENT = "var(--color-accent)";
const SKELETON_CARD_BG = "var(--color-bg-elevated)";
const SKELETON_BAR_BG = "var(--color-surface-muted)";

const rootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  height: "100%",
  overflow: "hidden",
  background: PANEL_BG,
  color: TEXT_PRIMARY,
};

const headerStyle: CSSProperties = {
  padding: "1rem 1.5rem 0.5rem",
  borderBottom: `1px solid ${PANEL_BORDER}`,
  flex: "0 0 auto",
};

const titleStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-xl, 1.25rem)",
  fontWeight: "var(--font-weight-semibold, 600)",
};

const subtitleStyle: CSSProperties = {
  marginTop: "0.25rem",
  fontSize: "var(--font-size-sm, 0.875rem)",
  color: TEXT_SECONDARY,
};

const tabRowStyle: CSSProperties = {
  display: "flex",
  gap: "0.25rem",
  marginTop: "0.875rem",
  alignItems: "stretch",
};

const tabButtonStyle: CSSProperties = {
  background: "transparent",
  border: "none",
  color: TEXT_SECONDARY,
  padding: "0.5rem 0.875rem",
  fontSize: "var(--font-size-sm, 0.875rem)",
  fontWeight: "var(--font-weight-medium, 500)",
  cursor: "pointer",
  borderBottom: "2px solid transparent",
  marginBottom: "-1px",
};

const tabActiveStyle: CSSProperties = {
  color: TEXT_PRIMARY,
  borderBottom: `2px solid ${ACCENT}`,
};

const bodyStyle: CSSProperties = {
  flex: "1 1 auto",
  overflowY: "auto",
  padding: "1.25rem 1.5rem",
};

const searchRowStyle: CSSProperties = {
  marginBottom: "1rem",
};

const listStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "0.625rem",
};

const cardHeaderStyle: CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  gap: "0.5rem",
  justifyContent: "space-between",
};

const cardTitleStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-md, 1rem)",
  fontWeight: "var(--font-weight-semibold, 600)",
  color: TEXT_PRIMARY,
};

const cardDescStyle: CSSProperties = {
  marginTop: "0.375rem",
  color: TEXT_SECONDARY,
  fontSize: "var(--font-size-sm, 0.875rem)",
  display: "-webkit-box",
  WebkitLineClamp: 2,
  WebkitBoxOrient: "vertical",
  overflow: "hidden",
};

const cardFooterStyle: CSSProperties = {
  marginTop: "0.625rem",
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  fontSize: "var(--font-size-xs, 0.75rem)",
  color: TEXT_SECONDARY,
};

const cardActionsStyle: CSSProperties = {
  display: "flex",
  gap: "0.25rem",
  alignItems: "center",
};

const skeletonCardStyle: CSSProperties = {
  background: SKELETON_CARD_BG,
  border: `1px solid ${PANEL_BORDER}`,
  borderRadius: "0.5rem",
  padding: "0.875rem",
  display: "flex",
  flexDirection: "column",
  gap: "0.5rem",
};

const skeletonBoxStyle: CSSProperties = {
  background: SKELETON_BAR_BG,
  borderRadius: "0.25rem",
  height: "0.75rem",
};

export function MemoryDestination(
  props?: MemoryDestinationProps,
): ReactElement {
  const transport = useTransport();
  const [activeTab, setActiveTab] = useState<MemoryType>("user");
  const [byTab, setByTab] = useState<Record<MemoryType, TabState>>({
    user: { status: "idle" },
    project: { status: "idle" },
    reference: { status: "idle" },
  });
  const [query, setQuery] = useState<string>("");
  const [pinOverrides, setPinOverrides] = useState<Record<string, boolean>>({});
  const startedRef = useRef<Set<MemoryType>>(new Set());

  useEffect(() => {
    if (startedRef.current.has(activeTab)) {
      return;
    }
    startedRef.current.add(activeTab);
    setByTab((prev) => ({ ...prev, [activeTab]: { status: "loading" } }));
    const controller = new AbortController();
    let cancelled = false;
    transport
      .request<MemoryResponse>({
        method: "GET",
        path: "/v1/memory",
        query: { type: activeTab },
        signal: controller.signal,
      })
      .then((res) => {
        if (cancelled) return;
        setByTab((prev) => ({
          ...prev,
          [activeTab]: {
            status: "ready",
            memories: res.memories ?? [],
          },
        }));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message =
          err instanceof Error ? err.message : "Failed to load memories";
        setByTab((prev) => ({
          ...prev,
          [activeTab]: { status: "error", message },
        }));
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [activeTab, transport]);

  const handleTogglePin = (memory: Memory): void => {
    setPinOverrides((prev) => ({
      ...prev,
      [memory.id]: !(prev[memory.id] ?? memory.pinned),
    }));
    props?.onTogglePin?.(memory);
  };

  const handleDelete = (memory: Memory): void => {
    props?.onDelete?.(memory);
  };

  const current = byTab[activeTab];

  const sortedFiltered = useMemo<readonly Memory[]>(() => {
    if (current.status !== "ready") return [];
    const needle = query.trim().toLowerCase();
    const filtered = needle
      ? current.memories.filter((m) => {
          return (
            m.title.toLowerCase().includes(needle) ||
            m.description.toLowerCase().includes(needle)
          );
        })
      : current.memories;
    return [...filtered].sort((a, b) => {
      const aPinned = pinOverrides[a.id] ?? a.pinned;
      const bPinned = pinOverrides[b.id] ?? b.pinned;
      if (aPinned === bPinned) return 0;
      return aPinned ? -1 : 1;
    });
  }, [current, query, pinOverrides]);

  return (
    <div style={rootStyle} data-testid="memory-destination">
      <header style={headerStyle}>
        <h1 style={titleStyle}>Memory</h1>
        <div style={subtitleStyle}>What the agent remembers about you</div>
        <div role="tablist" aria-label="Memory tabs" style={tabRowStyle}>
          {TABS.map((tab) => {
            const isActive = tab.type === activeTab;
            return (
              <button
                key={tab.type}
                type="button"
                role="tab"
                aria-selected={isActive}
                aria-controls={`memory-panel-${tab.type}`}
                id={`memory-tab-${tab.type}`}
                onClick={() => setActiveTab(tab.type)}
                style={{
                  ...tabButtonStyle,
                  ...(isActive ? tabActiveStyle : null),
                }}
              >
                {tab.label}
              </button>
            );
          })}
        </div>
      </header>
      <div
        style={bodyStyle}
        role="tabpanel"
        id={`memory-panel-${activeTab}`}
        aria-labelledby={`memory-tab-${activeTab}`}
      >
        <div style={searchRowStyle}>
          <TextInput
            type="search"
            placeholder="Search memories"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            aria-label="Search memories"
          />
        </div>
        {renderTabBody(
          current,
          sortedFiltered,
          query,
          pinOverrides,
          handleTogglePin,
          handleDelete,
        )}
      </div>
    </div>
  );
}

function renderTabBody(
  state: TabState,
  filtered: readonly Memory[],
  query: string,
  pinOverrides: Record<string, boolean>,
  onTogglePin: (memory: Memory) => void,
  onDelete: (memory: Memory) => void,
): ReactElement {
  if (state.status === "idle" || state.status === "loading") {
    return renderSkeleton();
  }
  if (state.status === "error") {
    return (
      <Card tone="danger" data-testid="memory-error">
        Failed to load memories: {state.message}
      </Card>
    );
  }
  if (state.memories.length === 0) {
    return (
      <Card tone="muted" data-testid="memory-empty">
        <div
          style={{
            fontSize: "var(--font-size-md, 1rem)",
            fontWeight: "var(--font-weight-medium, 500)",
            marginBottom: "0.25rem",
          }}
        >
          No memories yet
        </div>
        <div
          style={{
            color: TEXT_SECONDARY,
            fontSize: "var(--font-size-sm, 0.875rem)",
          }}
        >
          The agent will save what it learns here.
        </div>
      </Card>
    );
  }
  if (filtered.length === 0) {
    return (
      <Card tone="muted" data-testid="memory-empty-search">
        No memories match &ldquo;{query}&rdquo;.
      </Card>
    );
  }
  return (
    <div style={listStyle} data-testid="memory-list">
      {filtered.map((memory) => {
        const isPinned = pinOverrides[memory.id] ?? memory.pinned;
        return (
          <Card key={memory.id}>
            <div style={cardHeaderStyle}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <h2 style={cardTitleStyle}>{memory.title}</h2>
                <p style={cardDescStyle}>{memory.description}</p>
              </div>
              <Badge tone={TAG_TONE[memory.type]}>
                {labelForType(memory.type)}
              </Badge>
            </div>
            <div style={cardFooterStyle}>
              <span>Updated {formatRelative(memory.lastUpdatedIso)}</span>
              <div style={cardActionsStyle}>
                <IconButton
                  size="sm"
                  variant="ghost"
                  aria-label={isPinned ? "Unpin memory" : "Pin memory"}
                  aria-pressed={isPinned}
                  onClick={() => onTogglePin(memory)}
                  title={isPinned ? "Unpin" : "Pin"}
                >
                  {isPinned ? "📌" : "📍"}
                </IconButton>
                <IconButton
                  size="sm"
                  variant="ghost"
                  aria-label="Delete memory"
                  onClick={() => onDelete(memory)}
                  title="Delete"
                >
                  ✕
                </IconButton>
              </div>
            </div>
          </Card>
        );
      })}
    </div>
  );
}

function renderSkeleton(): ReactElement {
  return (
    <div style={listStyle} data-testid="memory-loading">
      {[0, 1, 2].map((i) => (
        <div key={i} style={skeletonCardStyle}>
          <div
            style={{ ...skeletonBoxStyle, width: "40%", height: "0.875rem" }}
            aria-hidden="true"
          />
          <div
            style={{ ...skeletonBoxStyle, width: "90%" }}
            aria-hidden="true"
          />
          <div
            style={{ ...skeletonBoxStyle, width: "70%" }}
            aria-hidden="true"
          />
        </div>
      ))}
    </div>
  );
}

function labelForType(type: MemoryType): string {
  const def = TABS.find((t) => t.type === type);
  return def ? def.tagLabel : type;
}

function formatRelative(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "—";
  const deltaMs = Date.now() - t;
  if (deltaMs < 0) return "just now";
  const minutes = Math.floor(deltaMs / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  const weeks = Math.floor(days / 7);
  if (weeks < 4) return `${weeks}w ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  const years = Math.floor(days / 365);
  return `${years}y ago`;
}
