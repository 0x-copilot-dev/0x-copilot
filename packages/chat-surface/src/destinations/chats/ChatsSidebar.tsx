import {
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import { useRouter } from "../../providers/RouterProvider";
import { useTransport } from "../../providers/TransportProvider";
import type { ArtifactRoute } from "../../routing/router";

const PANEL_WIDTH = 256;
const BACKGROUND = "#0E1015";
const BORDER = "#22252E";
const TEXT_PRIMARY = "#E4E5E9";
const TEXT_SECONDARY = "#7E8492";
const TEXT_TERTIARY = "#5A6070";
const ACCENT = "#7B9BFF";
const ACTIVE_TINT = "rgba(123, 155, 255, 0.08)";
const SEARCH_BACKGROUND = "#16181F";

interface ChatsProjectThread {
  readonly id: string;
  readonly title: string;
  readonly updated_at: string;
}

interface ChatsProject {
  readonly id: string;
  readonly name: string;
  readonly threads: readonly ChatsProjectThread[];
}

interface ChatsProjectsResponse {
  readonly projects: readonly ChatsProject[];
}

type FetchState =
  | { readonly status: "loading" }
  | { readonly status: "error"; readonly message: string }
  | { readonly status: "ready"; readonly projects: readonly ChatsProject[] };

export interface ChatsSidebarProps {
  readonly fullscreen?: boolean;
  readonly onFullscreenChange?: (next: boolean) => void;
}

function activeConversationId(route: ArtifactRoute | null): string | null {
  if (route === null) return null;
  if (route.kind === "chat" || route.kind === "conversation") {
    return route.conversationId === "" ? null : route.conversationId;
  }
  return null;
}

function matchesSearch(haystack: string, needle: string): boolean {
  return haystack.toLowerCase().includes(needle.toLowerCase());
}

interface FilteredProject {
  readonly project: ChatsProject;
  readonly visibleThreads: readonly ChatsProjectThread[];
  readonly forceExpanded: boolean;
}

function applySearch(
  projects: readonly ChatsProject[],
  query: string,
): readonly FilteredProject[] {
  const trimmed = query.trim();
  if (trimmed === "") {
    return projects.map((p) => ({
      project: p,
      visibleThreads: p.threads,
      forceExpanded: false,
    }));
  }
  const out: FilteredProject[] = [];
  for (const project of projects) {
    const projectHit = matchesSearch(project.name, trimmed);
    const threadHits = project.threads.filter((t) =>
      matchesSearch(t.title, trimmed),
    );
    if (projectHit) {
      out.push({
        project,
        visibleThreads: project.threads,
        forceExpanded: false,
      });
    } else if (threadHits.length > 0) {
      out.push({
        project,
        visibleThreads: threadHits,
        forceExpanded: true,
      });
    }
  }
  return out;
}

function CaretGlyph({ open }: { open: boolean }): ReactElement {
  const style: CSSProperties = {
    transition: "transform 120ms ease",
    transform: open ? "rotate(90deg)" : "rotate(0deg)",
  };
  return (
    <svg
      aria-hidden
      focusable={false}
      width={10}
      height={10}
      viewBox="0 0 10 10"
      style={style}
    >
      <path
        d="M3 1.5L7 5L3 8.5"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function FullscreenGlyph({ pressed }: { pressed: boolean }): ReactElement {
  if (pressed) {
    return (
      <svg
        aria-hidden
        focusable={false}
        width={14}
        height={14}
        viewBox="0 0 16 16"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M6 2v4H2M10 2v4h4M6 14v-4H2M10 14v-4h4" />
      </svg>
    );
  }
  return (
    <svg
      aria-hidden
      focusable={false}
      width={14}
      height={14}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M2 6V2h4M14 6V2h-4M2 10v4h4M14 10v4h-4" />
    </svg>
  );
}

function SearchGlyph(): ReactElement {
  return (
    <svg
      aria-hidden
      focusable={false}
      width={12}
      height={12}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="7" cy="7" r="4.5" />
      <path d="M10.5 10.5L14 14" />
    </svg>
  );
}

export function ChatsSidebar({
  fullscreen = false,
  onFullscreenChange,
}: ChatsSidebarProps): ReactElement {
  const transport = useTransport();
  const router = useRouter<ArtifactRoute>();

  const [route, setRoute] = useState<ArtifactRoute | null>(() => {
    try {
      return router.current();
    } catch {
      return null;
    }
  });
  const [data, setData] = useState<FetchState>({ status: "loading" });
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(
    () => new Set(),
  );

  useEffect(() => {
    return router.subscribe((next) => setRoute(next));
  }, [router]);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    transport
      .request<ChatsProjectsResponse>({
        method: "GET",
        path: "/v1/chats/projects",
        signal: controller.signal,
      })
      .then((res) => {
        if (cancelled) return;
        setData({ status: "ready", projects: res.projects });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message =
          err instanceof Error ? err.message : "Failed to load chats";
        setData({ status: "error", message });
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [transport]);

  const activeThread = activeConversationId(route);

  const projects = data.status === "ready" ? data.projects : [];
  const autoExpandedProjectId = useMemo(() => {
    if (activeThread === null) return null;
    for (const p of projects) {
      if (p.threads.some((t) => t.id === activeThread)) return p.id;
    }
    return null;
  }, [projects, activeThread]);

  const filtered = useMemo(
    () => applySearch(projects, search),
    [projects, search],
  );

  const containerStyle: CSSProperties = {
    width: PANEL_WIDTH,
    minWidth: PANEL_WIDTH,
    height: "100%",
    backgroundColor: BACKGROUND,
    borderRight: `1px solid ${BORDER}`,
    color: TEXT_PRIMARY,
    display: "flex",
    flexDirection: "column",
    boxSizing: "border-box",
  };
  const headerStyle: CSSProperties = {
    padding: "12px 12px 8px",
    display: "flex",
    alignItems: "center",
    gap: 8,
    borderBottom: `1px solid ${BORDER}`,
  };
  const titleStyle: CSSProperties = {
    fontSize: 13,
    fontWeight: 600,
    letterSpacing: 0.2,
    flex: 1,
    margin: 0,
  };
  const fullscreenButtonStyle: CSSProperties = {
    width: 24,
    height: 24,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: fullscreen ? ACTIVE_TINT : "transparent",
    color: fullscreen ? ACCENT : TEXT_SECONDARY,
    border: "none",
    borderRadius: 4,
    cursor: "pointer",
    padding: 0,
  };
  const searchWrapperStyle: CSSProperties = {
    padding: "8px 12px",
    borderBottom: `1px solid ${BORDER}`,
    display: "flex",
    alignItems: "center",
    gap: 8,
  };
  const searchInputWrapStyle: CSSProperties = {
    flex: 1,
    display: "flex",
    alignItems: "center",
    gap: 6,
    padding: "6px 8px",
    backgroundColor: SEARCH_BACKGROUND,
    borderRadius: 6,
    color: TEXT_SECONDARY,
  };
  const searchInputStyle: CSSProperties = {
    flex: 1,
    background: "transparent",
    border: "none",
    outline: "none",
    color: TEXT_PRIMARY,
    fontSize: 12,
    minWidth: 0,
  };
  const listStyle: CSSProperties = {
    flex: 1,
    overflowY: "auto",
    listStyle: "none",
    margin: 0,
    padding: "6px 0",
  };

  return (
    <aside
      aria-label="Chats sidebar"
      data-component="chats-sidebar"
      data-fullscreen={fullscreen ? "on" : "off"}
      style={containerStyle}
    >
      <div style={headerStyle}>
        <h2 style={titleStyle}>Chats</h2>
        <button
          type="button"
          aria-label={fullscreen ? "Exit fullscreen" : "Enter fullscreen"}
          aria-pressed={fullscreen}
          onClick={() => onFullscreenChange?.(!fullscreen)}
          style={fullscreenButtonStyle}
          data-testid="chats-fullscreen-toggle"
        >
          <FullscreenGlyph pressed={fullscreen} />
        </button>
      </div>

      <div style={searchWrapperStyle}>
        <label style={searchInputWrapStyle}>
          <SearchGlyph />
          <input
            type="search"
            aria-label="Search chats"
            placeholder="Search projects and threads"
            value={search}
            onChange={(e) => setSearch(e.currentTarget.value)}
            style={searchInputStyle}
          />
        </label>
      </div>

      {data.status === "loading" ? (
        <div
          data-testid="chats-sidebar-loading"
          style={{ padding: 12, color: TEXT_SECONDARY, fontSize: 12 }}
        >
          Loading chats…
        </div>
      ) : data.status === "error" ? (
        <div
          role="alert"
          data-testid="chats-sidebar-error"
          style={{ padding: 12, color: "#E97070", fontSize: 12 }}
        >
          {data.message}
        </div>
      ) : filtered.length === 0 ? (
        <div
          data-testid="chats-sidebar-empty"
          style={{ padding: 12, color: TEXT_TERTIARY, fontSize: 12 }}
        >
          No projects match.
        </div>
      ) : (
        <ul style={listStyle} data-testid="chats-sidebar-projects">
          {filtered.map(({ project, visibleThreads, forceExpanded }) => {
            const isOpen =
              forceExpanded ||
              expanded.has(project.id) ||
              project.id === autoExpandedProjectId;
            const toggle = (): void => {
              setExpanded((prev) => {
                const next = new Set(prev);
                if (next.has(project.id)) {
                  next.delete(project.id);
                } else {
                  next.add(project.id);
                }
                return next;
              });
            };
            const projectRowStyle: CSSProperties = {
              display: "flex",
              alignItems: "center",
              gap: 6,
              padding: "6px 10px",
              color: TEXT_SECONDARY,
              background: "transparent",
              border: "none",
              cursor: "pointer",
              width: "100%",
              textAlign: "left",
              fontSize: 12,
              fontWeight: 600,
              letterSpacing: 0.1,
            };
            return (
              <li key={project.id} data-project-id={project.id}>
                <button
                  type="button"
                  aria-label={`Toggle ${project.name}`}
                  aria-expanded={isOpen}
                  onClick={toggle}
                  style={projectRowStyle}
                  data-state={isOpen ? "open" : "closed"}
                >
                  <CaretGlyph open={isOpen} />
                  <span style={{ flex: 1 }}>{project.name}</span>
                </button>
                {isOpen ? (
                  <ul
                    style={{
                      listStyle: "none",
                      margin: 0,
                      padding: "2px 0 6px 22px",
                    }}
                    data-testid={`chats-threads-${project.id}`}
                  >
                    {visibleThreads.map((thread) => {
                      const isActive = thread.id === activeThread;
                      const handleClick = (): void => {
                        router.navigate({
                          kind: "chat",
                          conversationId: thread.id,
                        });
                      };
                      const threadRowStyle: CSSProperties = {
                        display: "block",
                        width: "100%",
                        textAlign: "left",
                        padding: "5px 8px",
                        marginRight: 8,
                        background: isActive ? ACTIVE_TINT : "transparent",
                        color: isActive ? TEXT_PRIMARY : TEXT_SECONDARY,
                        border: "none",
                        borderLeft: `2px solid ${isActive ? ACCENT : "transparent"}`,
                        borderRadius: 4,
                        cursor: "pointer",
                        fontSize: 12,
                        lineHeight: "16px",
                      };
                      return (
                        <li key={thread.id} data-thread-id={thread.id}>
                          <button
                            type="button"
                            onClick={handleClick}
                            aria-current={isActive ? "page" : undefined}
                            data-state={isActive ? "active" : "inactive"}
                            style={threadRowStyle}
                          >
                            {thread.title}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </aside>
  );
}
