import {
  useCallback,
  useEffect,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import type {
  ConversationId,
  RunId,
  SkillId,
} from "@enterprise-search/api-types";

import { useRouter } from "../../providers/RouterProvider";
import { useTransport } from "../../providers/TransportProvider";
import type { ArtifactRoute } from "../../routing/router";
import { formatRelativeTime } from "../../util/time";

export interface PinnedChat {
  readonly conversationId: ConversationId;
  readonly title: string;
  readonly lastMessageAt: string;
  readonly subtitle?: string;
}

export type RecentRunStatus =
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "queued";

export interface RecentRun {
  readonly runId: RunId;
  readonly title: string;
  readonly status: RecentRunStatus;
  readonly startedAt: string;
}

export interface FavoriteTool {
  readonly skillId: SkillId;
  readonly name: string;
  readonly subtitle?: string;
}

export interface HomePayload {
  readonly pinned: ReadonlyArray<PinnedChat>;
  readonly recent_runs: ReadonlyArray<RecentRun>;
  readonly favorites: ReadonlyArray<FavoriteTool>;
}

type ViewState =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | { readonly kind: "ready"; readonly payload: HomePayload };

// Design tokens (see packages/design-system/src/styles.css). Names are kept
// for readability at use-sites; values are CSS variables so Settings →
// Appearance theme/accent changes flow through automatically.
const APP_BACKGROUND = "var(--color-bg)";
const PANEL_BACKGROUND = "var(--color-surface)";
const PANEL_CARD_BACKGROUND = "var(--color-bg-elevated)";
const PANEL_BORDER = "var(--color-border)";
const PANEL_BORDER_STRONG = "var(--color-border-strong)";
const TEXT_PRIMARY = "var(--color-text)";
const TEXT_SECONDARY = "var(--color-text-muted)";
const TEXT_FAINT = "var(--color-text-subtle)";
const ACCENT = "var(--color-accent)";
const SKELETON_FILL = "var(--color-surface-muted)";
const STATUS_RUNNING = "var(--color-accent)";
const STATUS_OK = "var(--color-success)";
const STATUS_FAIL = "var(--color-danger)";
const STATUS_IDLE = "var(--color-text-muted)";

const SKELETON_ROW_COUNT = 3;

function statusColor(status: RecentRunStatus): string {
  if (status === "running" || status === "queued") return STATUS_RUNNING;
  if (status === "succeeded") return STATUS_OK;
  if (status === "failed") return STATUS_FAIL;
  return STATUS_IDLE;
}

function statusLabel(status: RecentRunStatus): string {
  if (status === "running") return "Running";
  if (status === "queued") return "Queued";
  if (status === "succeeded") return "Succeeded";
  if (status === "failed") return "Failed";
  return "Cancelled";
}

function SkeletonCard({ index }: { index: number }): ReactElement {
  const style: CSSProperties = {
    height: 96,
    borderRadius: 10,
    border: `1px solid ${PANEL_BORDER}`,
    backgroundColor: PANEL_BACKGROUND,
    padding: 14,
    display: "flex",
    flexDirection: "column",
    gap: 10,
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
      data-testid="home-skeleton-card"
      data-skeleton-index={index}
      aria-hidden="true"
    >
      <div style={{ ...bar, width: "30%", height: 14 }} />
      <div style={{ ...bar, width: "70%" }} />
      <div style={{ ...bar, width: "55%" }} />
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
      data-testid="home-error"
      data-state="error"
    >
      <div style={{ fontSize: 14, fontWeight: 600 }}>Could not load home</div>
      <div style={subStyle}>{message}</div>
      <button
        type="button"
        onClick={onRetry}
        style={retryStyle}
        data-testid="home-retry"
      >
        Retry
      </button>
    </div>
  );
}

function SectionCard({
  title,
  testId,
  children,
}: {
  title: string;
  testId: string;
  children: ReactElement | ReactElement[];
}): ReactElement {
  const wrapper: CSSProperties = {
    border: `1px solid ${PANEL_BORDER}`,
    borderRadius: 12,
    backgroundColor: PANEL_BACKGROUND,
    padding: 18,
    display: "flex",
    flexDirection: "column",
    gap: 14,
  };
  const headerStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
  };
  const titleStyle: CSSProperties = {
    fontSize: 14,
    fontWeight: 600,
    color: TEXT_PRIMARY,
  };
  return (
    <section style={wrapper} data-testid={testId}>
      <div style={headerStyle}>
        <div style={titleStyle}>{title}</div>
      </div>
      {children}
    </section>
  );
}

function SectionEmpty({ hint }: { hint: string }): ReactElement {
  const style: CSSProperties = {
    color: TEXT_FAINT,
    fontSize: 13,
    fontStyle: "italic",
    padding: "4px 0",
  };
  return (
    <div style={style} role="status" data-testid="home-section-empty">
      {hint}
    </div>
  );
}

function PinnedCard({
  pinned,
  onOpen,
}: {
  pinned: PinnedChat;
  onOpen: (pinned: PinnedChat) => void;
}): ReactElement {
  const cardStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    alignItems: "flex-start",
    gap: 6,
    padding: "10px 12px",
    borderRadius: 10,
    border: `1px solid ${PANEL_BORDER}`,
    backgroundColor: PANEL_CARD_BACKGROUND,
    color: TEXT_PRIMARY,
    cursor: "pointer",
    textAlign: "left",
    width: "100%",
    boxSizing: "border-box",
  };
  const titleStyle: CSSProperties = {
    fontSize: 13,
    fontWeight: 600,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    width: "100%",
  };
  const metaStyle: CSSProperties = {
    fontSize: 12,
    color: TEXT_SECONDARY,
    display: "flex",
    gap: 8,
    width: "100%",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  return (
    <button
      type="button"
      onClick={() => onOpen(pinned)}
      style={cardStyle}
      aria-label={`Open chat ${pinned.title}`}
      data-testid="home-pinned-card"
      data-conversation-id={pinned.conversationId}
    >
      <div style={titleStyle}>{pinned.title}</div>
      <div style={metaStyle}>
        {pinned.subtitle !== undefined && pinned.subtitle.length > 0 ? (
          <span>{pinned.subtitle} ·</span>
        ) : null}
        <span>{formatRelativeTime(pinned.lastMessageAt)}</span>
      </div>
    </button>
  );
}

function RecentRunCard({
  run,
  onOpen,
}: {
  run: RecentRun;
  onOpen: (run: RecentRun) => void;
}): ReactElement {
  const cardStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 10,
    padding: "10px 12px",
    borderRadius: 10,
    border: `1px solid ${PANEL_BORDER}`,
    backgroundColor: PANEL_CARD_BACKGROUND,
    color: TEXT_PRIMARY,
    cursor: "pointer",
    textAlign: "left",
    width: "100%",
    boxSizing: "border-box",
  };
  const dotStyle: CSSProperties = {
    width: 8,
    height: 8,
    borderRadius: "50%",
    backgroundColor: statusColor(run.status),
    flexShrink: 0,
  };
  const titleStyle: CSSProperties = {
    fontSize: 13,
    fontWeight: 600,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    flex: 1,
  };
  const metaStyle: CSSProperties = {
    fontSize: 12,
    color: TEXT_SECONDARY,
    flexShrink: 0,
  };
  return (
    <button
      type="button"
      onClick={() => onOpen(run)}
      style={cardStyle}
      aria-label={`Open run ${run.title}`}
      data-testid="home-recent-run-card"
      data-run-id={run.runId}
      data-run-status={run.status}
    >
      <span
        style={dotStyle}
        aria-hidden="true"
        data-testid="home-recent-run-status-dot"
      />
      <span style={titleStyle}>{run.title}</span>
      <span style={metaStyle}>
        {statusLabel(run.status)} · {formatRelativeTime(run.startedAt)}
      </span>
    </button>
  );
}

function FavoriteCard({
  favorite,
  onOpen,
}: {
  favorite: FavoriteTool;
  onOpen: (favorite: FavoriteTool) => void;
}): ReactElement {
  const cardStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 4,
    padding: "10px 12px",
    borderRadius: 10,
    border: `1px solid ${PANEL_BORDER}`,
    backgroundColor: PANEL_CARD_BACKGROUND,
    color: TEXT_PRIMARY,
    cursor: "pointer",
    textAlign: "left",
    width: "100%",
    boxSizing: "border-box",
  };
  const nameStyle: CSSProperties = {
    fontSize: 13,
    fontWeight: 600,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const subStyle: CSSProperties = {
    fontSize: 12,
    color: TEXT_SECONDARY,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  return (
    <button
      type="button"
      onClick={() => onOpen(favorite)}
      style={cardStyle}
      aria-label={`Open tool ${favorite.name}`}
      data-testid="home-favorite-card"
      data-skill-id={favorite.skillId}
    >
      <div style={nameStyle}>{favorite.name}</div>
      {favorite.subtitle !== undefined && favorite.subtitle.length > 0 ? (
        <div style={subStyle}>{favorite.subtitle}</div>
      ) : null}
    </button>
  );
}

export function HomeDestination(): ReactElement {
  const transport = useTransport();
  const router = useRouter<ArtifactRoute>();
  const [state, setState] = useState<ViewState>({ kind: "loading" });
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    transport
      .request<HomePayload>({ method: "GET", path: "/v1/home" })
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
  }, [transport, reloadToken]);

  const handleRetry = useCallback(() => {
    setReloadToken((t) => t + 1);
  }, []);

  const handleOpenPinned = useCallback(
    (pinned: PinnedChat) => {
      router.navigate({
        kind: "chat",
        conversationId: pinned.conversationId,
      });
    },
    [router],
  );

  const handleOpenRun = useCallback(
    (run: RecentRun) => {
      router.navigate({ kind: "run", runId: run.runId });
    },
    [router],
  );

  const handleOpenFavorite = useCallback(
    (favorite: FavoriteTool) => {
      router.navigate({ kind: "skill", skillId: favorite.skillId });
    },
    [router],
  );

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
  const sectionGridStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 16,
  };
  const cardListStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 8,
  };

  let body: ReactElement | null = null;
  if (state.kind === "loading") {
    body = (
      <div
        style={sectionGridStyle}
        data-testid="home-sections"
        data-state="loading"
      >
        {Array.from({ length: SKELETON_ROW_COUNT }).map((_, i) => (
          <SkeletonCard key={i} index={i} />
        ))}
      </div>
    );
  } else if (state.kind === "error") {
    body = <ErrorPanel message={state.message} onRetry={handleRetry} />;
  } else {
    const { pinned, recent_runs, favorites } = state.payload;
    body = (
      <div
        style={sectionGridStyle}
        data-testid="home-sections"
        data-state="ready"
      >
        <SectionCard title="Pinned chats" testId="home-section-pinned">
          {pinned.length === 0 ? (
            <SectionEmpty hint="Pin a chat to keep it here." />
          ) : (
            <div style={cardListStyle}>
              {pinned.map((p) => (
                <PinnedCard
                  key={p.conversationId}
                  pinned={p}
                  onOpen={handleOpenPinned}
                />
              ))}
            </div>
          )}
        </SectionCard>
        <SectionCard title="Recent runs" testId="home-section-recent-runs">
          {recent_runs.length === 0 ? (
            <SectionEmpty hint="Recent runs will appear here as the agent works." />
          ) : (
            <div style={cardListStyle}>
              {recent_runs.map((r) => (
                <RecentRunCard key={r.runId} run={r} onOpen={handleOpenRun} />
              ))}
            </div>
          )}
        </SectionCard>
        <SectionCard title="Favorite tools" testId="home-section-favorites">
          {favorites.length === 0 ? (
            <SectionEmpty hint="Star a tool from the Tools destination to bookmark it." />
          ) : (
            <div style={cardListStyle}>
              {favorites.map((f) => (
                <FavoriteCard
                  key={f.skillId}
                  favorite={f}
                  onOpen={handleOpenFavorite}
                />
              ))}
            </div>
          )}
        </SectionCard>
      </div>
    );
  }

  return (
    <section
      aria-label="Home destination"
      data-testid="home-destination"
      data-state={state.kind}
      style={rootStyle}
    >
      <div style={containerStyle}>
        <div style={titleStyle}>Home</div>
        {body}
      </div>
    </section>
  );
}
