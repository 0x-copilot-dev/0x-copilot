import {
  useCallback,
  useEffect,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import { useRouter } from "../../providers/RouterProvider";
import { useTransport } from "../../providers/TransportProvider";
import type { ArtifactRoute } from "../../routing/router";

export type InboxFilter = "all" | "mentions" | "approvals" | "errors";

export type InboxItemKind = "mention" | "approval" | "error";

export type InboxItemId = string & { readonly __brand: "InboxItemId" };

export interface InboxItem {
  readonly id: InboxItemId;
  readonly kind: InboxItemKind;
  readonly title: string;
  readonly source: string;
  readonly receivedAt: string;
  readonly route?: ArtifactRoute;
}

export interface InboxPayload {
  readonly items: ReadonlyArray<InboxItem>;
  readonly counts: Readonly<Record<InboxFilter, number>>;
}

type ViewState =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | { readonly kind: "ready"; readonly payload: InboxPayload };

interface FilterDescriptor {
  readonly slug: InboxFilter;
  readonly label: string;
  readonly emptyTitle: string;
  readonly emptyHint: string;
}

const FILTERS: ReadonlyArray<FilterDescriptor> = [
  {
    slug: "all",
    label: "All",
    emptyTitle: "Inbox zero",
    emptyHint: "New notifications will land here as the agent works.",
  },
  {
    slug: "mentions",
    label: "Mentions",
    emptyTitle: "No mentions",
    emptyHint: "When a teammate or agent @-mentions you it shows up here.",
  },
  {
    slug: "approvals",
    label: "Approvals",
    emptyTitle: "No pending approvals",
    emptyHint: "Approval requests from runs will queue up here.",
  },
  {
    slug: "errors",
    label: "Errors",
    emptyTitle: "No errors",
    emptyHint:
      "Failed runs and connector errors surface here when they happen.",
  },
];

// Design tokens (see packages/design-system/src/styles.css). Names are kept
// for readability at use-sites; values are CSS variables so Settings →
// Appearance theme/accent changes flow through automatically.
const APP_BACKGROUND = "var(--color-bg)";
const PANEL_BACKGROUND = "var(--color-surface)";
const PANEL_BORDER = "var(--color-border)";
const PANEL_BORDER_STRONG = "var(--color-border-strong)";
const TEXT_PRIMARY = "var(--color-text)";
const TEXT_SECONDARY = "var(--color-text-muted)";
const TEXT_FAINT = "var(--color-text-subtle)";
const ACCENT = "var(--color-accent)";
const SKELETON_FILL = "var(--color-surface-muted)";
const BADGE_MENTION = "var(--color-accent)";
const BADGE_APPROVAL = "var(--color-warning)";
const BADGE_ERROR = "var(--color-danger)";
const DANGER = "var(--color-danger)";

const SKELETON_ROW_COUNT = 5;

function formatRelativeTime(iso: string, now: number = Date.now()): string {
  const parsed = Date.parse(iso);
  if (Number.isNaN(parsed)) return "—";
  const diff = Math.max(0, now - parsed);
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  const years = Math.floor(months / 12);
  return `${years}y ago`;
}

function badgeColor(kind: InboxItemKind): string {
  if (kind === "mention") return BADGE_MENTION;
  if (kind === "approval") return BADGE_APPROVAL;
  return BADGE_ERROR;
}

function badgeLabel(kind: InboxItemKind): string {
  if (kind === "mention") return "Mention";
  if (kind === "approval") return "Approval";
  return "Error";
}

function SkeletonRow({ index }: { index: number }): ReactElement {
  const style: CSSProperties = {
    height: 64,
    borderRadius: 10,
    border: `1px solid ${PANEL_BORDER}`,
    backgroundColor: PANEL_BACKGROUND,
    padding: 14,
    display: "flex",
    alignItems: "center",
    gap: 12,
    opacity: 0.7,
  };
  const bar: CSSProperties = {
    backgroundColor: SKELETON_FILL,
    borderRadius: 4,
    height: 12,
  };
  return (
    <div
      style={style}
      data-testid="inbox-skeleton-row"
      data-skeleton-index={index}
      aria-hidden="true"
    >
      <div
        style={{
          width: 56,
          height: 18,
          borderRadius: 9,
          backgroundColor: SKELETON_FILL,
        }}
      />
      <div
        style={{ flex: 1, display: "flex", flexDirection: "column", gap: 8 }}
      >
        <div style={{ ...bar, width: "55%", height: 14 }} />
        <div style={{ ...bar, width: "30%" }} />
      </div>
    </div>
  );
}

function KindBadge({ kind }: { kind: InboxItemKind }): ReactElement {
  const tone = badgeColor(kind);
  // Previously appended hex alpha "33" (~20%) to the tone literal; CSS
  // custom properties can't be string-concatenated that way, so build a
  // translucent border via color-mix against the token instead.
  const borderTone = `color-mix(in srgb, ${tone} 20%, transparent)`;
  const style: CSSProperties = {
    fontSize: 11,
    fontWeight: 600,
    color: tone,
    backgroundColor: SKELETON_FILL,
    border: `1px solid ${borderTone}`,
    borderRadius: 6,
    padding: "2px 8px",
    textTransform: "uppercase",
    letterSpacing: 0.4,
    flexShrink: 0,
  };
  return (
    <span style={style} data-testid="inbox-row-badge" data-kind={kind}>
      {badgeLabel(kind)}
    </span>
  );
}

function ItemRow({
  item,
  pending,
  rowError,
  onOpen,
  onMarkRead,
}: {
  item: InboxItem;
  pending: boolean;
  rowError: string | null;
  onOpen: (item: InboxItem) => void;
  onMarkRead: (item: InboxItem) => void;
}): ReactElement {
  const wrapper: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 4,
  };
  const rowStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 12,
    padding: "12px 14px",
    borderRadius: 10,
    border: `1px solid ${PANEL_BORDER}`,
    backgroundColor: PANEL_BACKGROUND,
    color: TEXT_PRIMARY,
    boxSizing: "border-box",
  };
  const titleButtonStyle: CSSProperties = {
    flex: 1,
    minWidth: 0,
    background: "transparent",
    border: "none",
    color: TEXT_PRIMARY,
    padding: 0,
    margin: 0,
    cursor: "pointer",
    textAlign: "left",
    display: "flex",
    flexDirection: "column",
    gap: 4,
  };
  const titleStyle: CSSProperties = {
    fontSize: 14,
    fontWeight: 600,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const metaStyle: CSSProperties = {
    fontSize: 12,
    color: TEXT_SECONDARY,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const markStyle: CSSProperties = {
    height: 30,
    padding: "0 12px",
    borderRadius: 8,
    border: `1px solid ${PANEL_BORDER_STRONG}`,
    backgroundColor: "transparent",
    color: ACCENT,
    fontSize: 12,
    fontWeight: 600,
    cursor: pending ? "default" : "pointer",
    opacity: pending ? 0.6 : 1,
    flexShrink: 0,
  };
  const errorStyle: CSSProperties = {
    color: DANGER,
    fontSize: 12,
    paddingLeft: 14,
  };
  return (
    <div
      style={wrapper}
      data-testid="inbox-item"
      data-item-kind={item.kind}
      data-item-id={item.id}
    >
      <div style={rowStyle}>
        <KindBadge kind={item.kind} />
        <button
          type="button"
          onClick={() => onOpen(item)}
          style={titleButtonStyle}
          aria-label={`Open ${item.title}`}
          data-testid="inbox-item-open"
        >
          <span style={titleStyle}>{item.title}</span>
          <span style={metaStyle}>
            {item.source} · {formatRelativeTime(item.receivedAt)}
          </span>
        </button>
        <button
          type="button"
          onClick={() => onMarkRead(item)}
          style={markStyle}
          disabled={pending}
          aria-label={`Mark ${item.title} as read`}
          data-testid="inbox-item-mark-read"
        >
          {pending ? "Marking…" : "Mark read"}
        </button>
      </div>
      {rowError !== null ? (
        <div
          role="alert"
          style={errorStyle}
          data-testid="inbox-item-error"
          data-item-id={item.id}
        >
          {rowError}
        </div>
      ) : null}
    </div>
  );
}

function ErrorPanel({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}): ReactElement {
  const wrapper: CSSProperties = {
    border: `1px solid ${PANEL_BORDER}`,
    borderRadius: 12,
    backgroundColor: PANEL_BACKGROUND,
    padding: 32,
    textAlign: "center",
    color: TEXT_PRIMARY,
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: 12,
  };
  const subStyle: CSSProperties = { color: TEXT_SECONDARY, fontSize: 13 };
  const retryStyle: CSSProperties = {
    height: 32,
    padding: "0 14px",
    borderRadius: 8,
    border: `1px solid ${PANEL_BORDER_STRONG}`,
    backgroundColor: "transparent",
    color: ACCENT,
    fontSize: 13,
    fontWeight: 600,
    cursor: "pointer",
  };
  return (
    <div
      role="alert"
      style={wrapper}
      data-testid="inbox-error"
      data-state="error"
    >
      <div style={{ fontSize: 14, fontWeight: 600 }}>Could not load inbox</div>
      <div style={subStyle}>{message}</div>
      <button
        type="button"
        onClick={onRetry}
        style={retryStyle}
        data-testid="inbox-retry"
      >
        Retry
      </button>
    </div>
  );
}

function EmptyPanel({
  title,
  hint,
}: {
  title: string;
  hint: string;
}): ReactElement {
  const wrapper: CSSProperties = {
    border: `1px dashed ${PANEL_BORDER_STRONG}`,
    borderRadius: 12,
    padding: 48,
    textAlign: "center",
    color: TEXT_PRIMARY,
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: 8,
  };
  return (
    <div role="status" style={wrapper} data-testid="inbox-empty">
      <div style={{ fontSize: 16, fontWeight: 600 }}>{title}</div>
      <div style={{ color: TEXT_FAINT, fontSize: 13, maxWidth: 420 }}>
        {hint}
      </div>
    </div>
  );
}

function TabBar({
  active,
  counts,
  onSelect,
}: {
  active: InboxFilter;
  counts: Readonly<Record<InboxFilter, number>>;
  onSelect: (filter: InboxFilter) => void;
}): ReactElement {
  const wrapper: CSSProperties = {
    display: "flex",
    gap: 4,
    borderBottom: `1px solid ${PANEL_BORDER}`,
  };
  return (
    <div role="tablist" aria-label="Inbox filters" style={wrapper}>
      {FILTERS.map((descriptor) => {
        const isActive = descriptor.slug === active;
        const count = counts[descriptor.slug];
        const buttonStyle: CSSProperties = {
          height: 36,
          padding: "0 14px",
          borderRadius: 0,
          border: "none",
          background: "transparent",
          color: isActive ? TEXT_PRIMARY : TEXT_SECONDARY,
          fontSize: 13,
          fontWeight: isActive ? 600 : 500,
          cursor: "pointer",
          borderBottom: `2px solid ${isActive ? ACCENT : "transparent"}`,
          marginBottom: -1,
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
        };
        const countStyle: CSSProperties = {
          fontSize: 11,
          fontWeight: 600,
          color: isActive ? ACCENT : TEXT_FAINT,
          backgroundColor: SKELETON_FILL,
          borderRadius: 999,
          padding: "1px 8px",
          minWidth: 18,
          textAlign: "center",
        };
        return (
          <button
            key={descriptor.slug}
            type="button"
            role="tab"
            aria-selected={isActive}
            aria-controls={`inbox-panel-${descriptor.slug}`}
            id={`inbox-tab-${descriptor.slug}`}
            data-testid={`inbox-tab-${descriptor.slug}`}
            onClick={() => onSelect(descriptor.slug)}
            style={buttonStyle}
          >
            {descriptor.label}
            <span
              style={countStyle}
              data-testid={`inbox-count-${descriptor.slug}`}
            >
              {count}
            </span>
          </button>
        );
      })}
    </div>
  );
}

interface OptimisticState {
  readonly removed: ReadonlySet<string>;
  readonly pending: ReadonlySet<string>;
  readonly errors: Readonly<Record<string, string>>;
}

const EMPTY_OPTIMISTIC: OptimisticState = {
  removed: new Set<string>(),
  pending: new Set<string>(),
  errors: {},
};

export function InboxDestination(): ReactElement {
  const transport = useTransport();
  const router = useRouter<ArtifactRoute>();
  const [active, setActive] = useState<InboxFilter>("all");
  const [state, setState] = useState<ViewState>({ kind: "loading" });
  const [reloadToken, setReloadToken] = useState(0);
  const [optimistic, setOptimistic] =
    useState<OptimisticState>(EMPTY_OPTIMISTIC);

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    setOptimistic(EMPTY_OPTIMISTIC);
    transport
      .request<InboxPayload>({
        method: "GET",
        path: "/v1/inbox",
        query: { filter: active },
      })
      .then((response) => {
        if (cancelled) return;
        setState({ kind: "ready", payload: response });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const message =
          error instanceof Error ? error.message : "Network error";
        setState({ kind: "error", message });
      });
    return () => {
      cancelled = true;
    };
  }, [transport, active, reloadToken]);

  const handleSelectTab = useCallback((next: InboxFilter) => {
    setActive(next);
  }, []);

  const handleRetry = useCallback(() => {
    setReloadToken((t) => t + 1);
  }, []);

  const handleOpen = useCallback(
    (item: InboxItem) => {
      if (item.route !== undefined) {
        router.navigate(item.route);
        return;
      }
      router.navigate({ kind: "workspace", workspaceId: item.id });
    },
    [router],
  );

  const handleMarkRead = useCallback(
    (item: InboxItem) => {
      setOptimistic((prev) => {
        const pending = new Set(prev.pending);
        pending.add(item.id);
        const nextErrors = { ...prev.errors };
        delete nextErrors[item.id];
        return { removed: prev.removed, pending, errors: nextErrors };
      });
      transport
        .request<unknown>({
          method: "POST",
          path: `/v1/inbox/${item.id}/read`,
          body: {},
        })
        .then(() => {
          setOptimistic((prev) => {
            const removed = new Set(prev.removed);
            removed.add(item.id);
            const pending = new Set(prev.pending);
            pending.delete(item.id);
            return { removed, pending, errors: prev.errors };
          });
        })
        .catch((error: unknown) => {
          const message =
            error instanceof Error ? error.message : "Could not mark read";
          setOptimistic((prev) => {
            const pending = new Set(prev.pending);
            pending.delete(item.id);
            return {
              removed: prev.removed,
              pending,
              errors: { ...prev.errors, [item.id]: message },
            };
          });
        });
    },
    [transport],
  );

  const descriptor =
    FILTERS.find((f) => f.slug === active) ?? (FILTERS[0] as FilterDescriptor);

  const rootStyle: CSSProperties = {
    width: "100%",
    height: "100%",
    minHeight: 0,
    backgroundColor: APP_BACKGROUND,
    color: TEXT_PRIMARY,
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    overflow: "auto",
  };
  const containerStyle: CSSProperties = {
    width: "100%",
    maxWidth: 1000,
    margin: "0 auto",
    padding: "24px 28px 48px",
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    gap: 20,
  };
  const titleStyle: CSSProperties = {
    fontSize: 20,
    fontWeight: 600,
  };
  const listStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 10,
  };

  const placeholderCounts: Record<InboxFilter, number> = {
    all: 0,
    mentions: 0,
    approvals: 0,
    errors: 0,
  };
  const counts: Record<InboxFilter, number> =
    state.kind === "ready" ? { ...state.payload.counts } : placeholderCounts;

  let body: ReactElement | null = null;
  if (state.kind === "loading") {
    body = (
      <div style={listStyle} data-testid="inbox-list" data-state="loading">
        {Array.from({ length: SKELETON_ROW_COUNT }).map((_, i) => (
          <SkeletonRow key={i} index={i} />
        ))}
      </div>
    );
  } else if (state.kind === "error") {
    body = <ErrorPanel message={state.message} onRetry={handleRetry} />;
  } else {
    const visible = state.payload.items.filter(
      (i) => !optimistic.removed.has(i.id),
    );
    if (visible.length === 0) {
      body = (
        <EmptyPanel title={descriptor.emptyTitle} hint={descriptor.emptyHint} />
      );
    } else {
      body = (
        <div style={listStyle} data-testid="inbox-list" data-state="ready">
          {visible.map((item) => (
            <ItemRow
              key={item.id}
              item={item}
              pending={optimistic.pending.has(item.id)}
              rowError={optimistic.errors[item.id] ?? null}
              onOpen={handleOpen}
              onMarkRead={handleMarkRead}
            />
          ))}
        </div>
      );
    }
  }

  return (
    <section
      aria-label="Inbox destination"
      data-testid="inbox-destination"
      data-state={state.kind}
      data-active-filter={active}
      style={rootStyle}
    >
      <div style={containerStyle}>
        <div style={titleStyle}>Inbox</div>
        <TabBar active={active} counts={counts} onSelect={handleSelectTab} />
        <div
          role="tabpanel"
          id={`inbox-panel-${active}`}
          aria-labelledby={`inbox-tab-${active}`}
        >
          {body}
        </div>
      </div>
    </section>
  );
}
