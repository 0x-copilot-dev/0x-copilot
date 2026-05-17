import type { McpAuthState, McpServer } from "@enterprise-search/api-types";
import {
  AppIcon,
  Badge,
  Button,
  TextInput,
} from "@enterprise-search/design-system";
import {
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type MouseEvent,
  type ReactElement,
} from "react";

import { useRouter } from "../../providers/RouterProvider";
import { useTransport } from "../../providers/TransportProvider";
import type { ArtifactRoute } from "../../routing/router";

// Design tokens (see packages/design-system/src/styles.css). Names are kept
// for readability at use-sites; values are CSS variables so Settings →
// Appearance theme/accent changes flow through automatically.
const BACKGROUND = "var(--color-bg)";
const BORDER = "var(--color-border)";
const TEXT_PRIMARY = "var(--color-text)";
const TEXT_SECONDARY = "var(--color-text-muted)";
const HEADER_BG = "var(--color-bg-elevated)";

export interface McpServerRow extends McpServer {
  readonly tool_count?: number;
  readonly last_used_at?: string | null;
}

interface McpServerListResponse {
  readonly servers: readonly McpServerRow[];
}

type FetchState =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | { readonly kind: "ready"; readonly servers: readonly McpServerRow[] };

type BadgeTone = "neutral" | "success" | "warning" | "danger" | "accent";

interface AuthStatusView {
  readonly label: string;
  readonly tone: BadgeTone;
  readonly affordance:
    | "connect"
    | "reauthorize"
    | "pending"
    | "disconnect-only";
}

function authStatusView(state: McpAuthState): AuthStatusView {
  if (state === "authenticated")
    return { label: "Connected", tone: "success", affordance: "reauthorize" };
  if (state === "auth_pending")
    return { label: "Authorizing", tone: "warning", affordance: "pending" };
  if (state === "auth_failed")
    return { label: "Expired", tone: "danger", affordance: "connect" };
  if (state === "unauthenticated")
    return { label: "Needs auth", tone: "warning", affordance: "connect" };
  if (state === "auth_skipped")
    return {
      label: "Auth skipped",
      tone: "neutral",
      affordance: "disconnect-only",
    };
  return {
    label: "Auth unsupported",
    tone: "neutral",
    affordance: "disconnect-only",
  };
}

function formatLastUsed(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "Never";
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return "—";
  const diff = Date.now() - parsed;
  if (diff < 60_000) return "just now";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  const days = Math.floor(diff / 86_400_000);
  if (days < 30) return `${days}d ago`;
  return value.slice(0, 10);
}

export function ConnectorsDestination(): ReactElement {
  const transport = useTransport();
  const router = useRouter<ArtifactRoute>();

  const [search, setSearch] = useState("");
  const [fetchTick, setFetchTick] = useState(0);
  const [state, setState] = useState<FetchState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    transport
      .request<McpServerListResponse>({
        method: "GET",
        path: "/v1/mcp/servers",
      })
      .then((res) => {
        if (cancelled) return;
        setState({ kind: "ready", servers: res.servers });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message =
          err instanceof Error ? err.message : "Failed to load connectors.";
        setState({ kind: "error", message });
      });
    return () => {
      cancelled = true;
    };
  }, [transport, fetchTick]);

  const filtered = useMemo(() => {
    if (state.kind !== "ready") return [];
    const needle = search.trim().toLowerCase();
    if (needle === "") return state.servers;
    return state.servers.filter((s) => {
      const haystack = `${s.display_name} ${s.name}`.toLowerCase();
      return haystack.includes(needle);
    });
  }, [state, search]);

  const handleCardClick = (serverId: string): void => {
    router.navigate({ kind: "mcp", serverId });
  };

  const handleKeyActivate = (
    e: KeyboardEvent<HTMLDivElement>,
    serverId: string,
  ): void => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      handleCardClick(serverId);
    }
  };

  const stopCardPropagation = (e: MouseEvent<HTMLButtonElement>): void => {
    e.stopPropagation();
  };

  const containerStyle: CSSProperties = {
    width: "100%",
    height: "100%",
    minHeight: 0,
    display: "flex",
    flexDirection: "column",
    backgroundColor: BACKGROUND,
    color: TEXT_PRIMARY,
    boxSizing: "border-box",
  };
  const filterBarStyle: CSSProperties = {
    position: "sticky",
    top: 0,
    zIndex: 2,
    display: "flex",
    gap: 12,
    padding: "12px 16px",
    backgroundColor: BACKGROUND,
    borderBottom: `1px solid ${BORDER}`,
    alignItems: "center",
  };
  const bodyStyle: CSSProperties = {
    flex: 1,
    minHeight: 0,
    overflow: "auto",
    padding: 16,
  };
  const gridStyle: CSSProperties = {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
    gap: 12,
  };
  const cardStyle: CSSProperties = {
    padding: 16,
    backgroundColor: HEADER_BG,
    border: `1px solid ${BORDER}`,
    borderRadius: 8,
    display: "flex",
    flexDirection: "column",
    gap: 10,
    cursor: "pointer",
    minHeight: 132,
  };
  const headerRowStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 10,
  };
  const nameStyle: CSSProperties = {
    fontSize: 14,
    fontWeight: 600,
    color: TEXT_PRIMARY,
    margin: 0,
    flex: 1,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const metaStyle: CSSProperties = {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    fontSize: 12,
    color: TEXT_SECONDARY,
  };
  const actionsStyle: CSSProperties = {
    display: "flex",
    gap: 8,
  };
  const emptyStyle: CSSProperties = {
    padding: 24,
    color: TEXT_SECONDARY,
    fontSize: 13,
  };

  return (
    <section
      data-component="connectors-destination"
      aria-label="Connectors destination"
      style={containerStyle}
    >
      <div style={filterBarStyle} data-testid="connectors-filter-bar">
        <TextInput
          aria-label="Search connectors"
          data-testid="connectors-search"
          placeholder="Search connectors"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>
      <div style={bodyStyle} data-testid="connectors-body">
        {state.kind === "loading" ? (
          <div style={gridStyle} data-testid="connectors-skeleton">
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <div
                key={i}
                data-testid="connectors-skeleton-card"
                style={{ ...cardStyle, cursor: "default" }}
              >
                <span
                  style={{
                    display: "inline-block",
                    width: "55%",
                    height: 12,
                    borderRadius: 4,
                    backgroundColor: BORDER,
                  }}
                  aria-hidden="true"
                />
                <span
                  style={{
                    display: "inline-block",
                    width: "30%",
                    height: 10,
                    borderRadius: 4,
                    backgroundColor: BORDER,
                  }}
                  aria-hidden="true"
                />
                <span
                  style={{
                    display: "inline-block",
                    width: "70%",
                    height: 10,
                    borderRadius: 4,
                    backgroundColor: BORDER,
                  }}
                  aria-hidden="true"
                />
              </div>
            ))}
          </div>
        ) : state.kind === "error" ? (
          <div
            data-testid="connectors-error"
            style={{
              padding: 24,
              display: "flex",
              gap: 12,
              alignItems: "center",
              color: TEXT_PRIMARY,
            }}
          >
            <span>{state.message}</span>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setFetchTick((n) => n + 1)}
              data-testid="connectors-retry"
            >
              Retry
            </Button>
          </div>
        ) : filtered.length === 0 ? (
          <div data-testid="connectors-empty" style={emptyStyle}>
            {state.servers.length === 0
              ? "No connectors yet."
              : "No connectors match your search."}
          </div>
        ) : (
          <div style={gridStyle} role="list" aria-label="Connectors">
            {filtered.map((server) => {
              const view = authStatusView(server.auth_state);
              const toolCount = server.tool_count ?? 0;
              return (
                <div
                  key={server.server_id}
                  role="listitem"
                  tabIndex={0}
                  data-testid="connectors-card"
                  data-server-id={server.server_id}
                  data-auth-state={server.auth_state}
                  onClick={() => handleCardClick(server.server_id)}
                  onKeyDown={(e) => handleKeyActivate(e, server.server_id)}
                  style={cardStyle}
                >
                  <div style={headerRowStyle}>
                    <AppIcon
                      name={server.name}
                      color={server.brand_color ?? undefined}
                      logoUrl={server.logo_url}
                    />
                    <h3 style={nameStyle}>
                      {server.display_name || server.name}
                    </h3>
                    <Badge tone={view.tone}>{view.label}</Badge>
                  </div>
                  <div style={metaStyle}>
                    <span>
                      {toolCount} {toolCount === 1 ? "tool" : "tools"}
                    </span>
                    <span>Last used {formatLastUsed(server.last_used_at)}</span>
                  </div>
                  <div style={actionsStyle}>
                    {view.affordance === "connect" ? (
                      <Button
                        variant="primary"
                        size="sm"
                        data-testid="connectors-connect"
                        onClick={stopCardPropagation}
                      >
                        Connect
                      </Button>
                    ) : null}
                    {view.affordance === "pending" ? (
                      <Button
                        variant="secondary"
                        size="sm"
                        disabled
                        data-testid="connectors-pending"
                      >
                        Authorizing…
                      </Button>
                    ) : null}
                    {view.affordance === "reauthorize" ? (
                      <>
                        <Button
                          variant="secondary"
                          size="sm"
                          data-testid="connectors-reauthorize"
                          onClick={stopCardPropagation}
                        >
                          Reauthorize
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          data-testid="connectors-disconnect"
                          onClick={stopCardPropagation}
                        >
                          Disconnect
                        </Button>
                      </>
                    ) : null}
                    {view.affordance === "disconnect-only" ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        data-testid="connectors-disconnect"
                        onClick={stopCardPropagation}
                      >
                        Disconnect
                      </Button>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </section>
  );
}
