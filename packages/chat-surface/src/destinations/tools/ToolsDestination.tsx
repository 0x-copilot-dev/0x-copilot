import type { Skill } from "@enterprise-search/api-types";
import { Badge, Button, TextInput } from "@enterprise-search/design-system";
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

interface SkillListResponse {
  readonly skills: readonly Skill[];
}

type FetchState =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | { readonly kind: "ready"; readonly skills: readonly Skill[] };

function formatLastUsed(updatedAt: string): string {
  const parsed = Date.parse(updatedAt);
  if (Number.isNaN(parsed)) return "—";
  const diffMs = Date.now() - parsed;
  if (diffMs < 60_000) return "just now";
  if (diffMs < 3_600_000) return `${Math.floor(diffMs / 60_000)}m ago`;
  if (diffMs < 86_400_000) return `${Math.floor(diffMs / 3_600_000)}h ago`;
  const days = Math.floor(diffMs / 86_400_000);
  if (days < 30) return `${days}d ago`;
  return updatedAt.slice(0, 10);
}

export function ToolsDestination(): ReactElement {
  const transport = useTransport();
  const router = useRouter<ArtifactRoute>();

  const [search, setSearch] = useState("");
  const [fetchTick, setFetchTick] = useState(0);
  const [state, setState] = useState<FetchState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    transport
      .request<SkillListResponse>({ method: "GET", path: "/v1/skills" })
      .then((res) => {
        if (cancelled) return;
        setState({ kind: "ready", skills: res.skills });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message =
          err instanceof Error ? err.message : "Failed to load skills.";
        setState({ kind: "error", message });
      });
    return () => {
      cancelled = true;
    };
  }, [transport, fetchTick]);

  const filtered = useMemo(() => {
    if (state.kind !== "ready") return [];
    const needle = search.trim().toLowerCase();
    if (needle === "") return state.skills;
    return state.skills.filter((s) => {
      const haystack =
        `${s.display_name} ${s.name} ${s.description}`.toLowerCase();
      return haystack.includes(needle);
    });
  }, [state, search]);

  const handleCardClick = (skillId: string): void => {
    router.navigate({ kind: "skill", skillId });
  };

  const handleKeyActivate = (
    e: KeyboardEvent<HTMLDivElement>,
    skillId: string,
  ): void => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      handleCardClick(skillId);
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
    gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
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
    minHeight: 120,
  };
  const nameStyle: CSSProperties = {
    fontSize: 14,
    fontWeight: 600,
    color: TEXT_PRIMARY,
    margin: 0,
  };
  const descStyle: CSSProperties = {
    fontSize: 13,
    color: TEXT_SECONDARY,
    margin: 0,
    flex: 1,
    display: "-webkit-box",
    WebkitLineClamp: 2,
    WebkitBoxOrient: "vertical",
    overflow: "hidden",
  };
  const footerStyle: CSSProperties = {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    fontSize: 12,
    color: TEXT_SECONDARY,
  };
  const emptyStyle: CSSProperties = {
    padding: 24,
    color: TEXT_SECONDARY,
    fontSize: 13,
  };

  return (
    <section
      data-component="tools-destination"
      aria-label="Tools destination"
      style={containerStyle}
    >
      <div style={filterBarStyle} data-testid="tools-filter-bar">
        <TextInput
          aria-label="Search skills"
          data-testid="tools-search"
          placeholder="Search skills"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>
      <div style={bodyStyle} data-testid="tools-body">
        {state.kind === "loading" ? (
          <div style={gridStyle} data-testid="tools-skeleton">
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <div
                key={i}
                data-testid="tools-skeleton-card"
                style={{
                  ...cardStyle,
                  cursor: "default",
                }}
              >
                <span
                  style={{
                    display: "inline-block",
                    width: "60%",
                    height: 12,
                    borderRadius: 4,
                    backgroundColor: BORDER,
                  }}
                  aria-hidden="true"
                />
                <span
                  style={{
                    display: "inline-block",
                    width: "100%",
                    height: 10,
                    borderRadius: 4,
                    backgroundColor: BORDER,
                  }}
                  aria-hidden="true"
                />
                <span
                  style={{
                    display: "inline-block",
                    width: "80%",
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
            data-testid="tools-error"
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
              data-testid="tools-retry"
            >
              Retry
            </Button>
          </div>
        ) : filtered.length === 0 ? (
          <div data-testid="tools-empty" style={emptyStyle}>
            {state.skills.length === 0
              ? "No skills installed."
              : "No skills match your search."}
          </div>
        ) : (
          <div style={gridStyle} role="list" aria-label="Skills">
            {filtered.map((skill) => {
              const installed = skill.enabled;
              return (
                <div
                  key={skill.skill_id}
                  role="listitem"
                  tabIndex={0}
                  data-testid="tools-card"
                  data-skill-id={skill.skill_id}
                  data-enabled={installed ? "true" : "false"}
                  onClick={() => handleCardClick(skill.skill_id)}
                  onKeyDown={(e) => handleKeyActivate(e, skill.skill_id)}
                  style={cardStyle}
                >
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      gap: 8,
                    }}
                  >
                    <h3 style={nameStyle}>
                      {skill.display_name || skill.name}
                    </h3>
                    {installed ? (
                      <Badge tone="success">Installed</Badge>
                    ) : (
                      <Badge tone="neutral">Available</Badge>
                    )}
                  </div>
                  <p style={descStyle}>
                    {skill.description || "No description provided."}
                  </p>
                  <div style={footerStyle}>
                    <span>Last used {formatLastUsed(skill.updated_at)}</span>
                    {installed ? (
                      <Button
                        variant="secondary"
                        size="sm"
                        data-testid="tools-manage"
                        onClick={stopCardPropagation}
                      >
                        Manage
                      </Button>
                    ) : (
                      <Button
                        variant="primary"
                        size="sm"
                        data-testid="tools-install"
                        onClick={stopCardPropagation}
                      >
                        Install
                      </Button>
                    )}
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
