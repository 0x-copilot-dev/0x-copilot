import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
} from "react";

import type { ProjectId } from "@enterprise-search/api-types";

import { useRouter } from "../../providers/RouterProvider";
import { useTransport } from "../../providers/TransportProvider";
import type { ArtifactRoute } from "../../routing/router";
import { formatRelativeTime } from "../../util/time";

export interface Project {
  readonly id: ProjectId;
  readonly name: string;
  readonly lastActivityAt: string;
  readonly chatCount: number;
  readonly ownerName: string;
  readonly ownerAvatarUrl?: string;
}

interface ProjectsResponse {
  readonly projects: ReadonlyArray<Project>;
}

type ViewState =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | { readonly kind: "ready"; readonly projects: ReadonlyArray<Project> };

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
const ACCENT_CONTRAST = "var(--color-accent-contrast)";
const DANGER = "var(--color-danger)";
const SKELETON_FILL = "var(--color-surface-muted)";
const AVATAR_BG = "var(--color-border-strong)";

const SKELETON_CARD_COUNT = 6;

function initialsOf(name: string): string {
  const cleaned = name.trim();
  if (cleaned.length === 0) return "?";
  const parts = cleaned.split(/\s+/);
  if (parts.length === 1) return parts[0]!.slice(0, 2).toUpperCase();
  return (parts[0]![0]! + parts[parts.length - 1]![0]!).toUpperCase();
}

function ProjectAvatar({
  ownerName,
  ownerAvatarUrl,
}: {
  ownerName: string;
  ownerAvatarUrl?: string;
}): ReactElement {
  const wrapper: CSSProperties = {
    width: 28,
    height: 28,
    borderRadius: "50%",
    overflow: "hidden",
    flexShrink: 0,
    backgroundColor: AVATAR_BG,
    color: TEXT_PRIMARY,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: 11,
    fontWeight: 600,
  };
  if (ownerAvatarUrl !== undefined && ownerAvatarUrl.length > 0) {
    return (
      <img
        src={ownerAvatarUrl}
        alt={ownerName}
        style={{ ...wrapper, objectFit: "cover" }}
      />
    );
  }
  return (
    <div
      role="img"
      aria-label={ownerName}
      title={ownerName}
      style={wrapper}
      data-testid="project-card-avatar-initials"
    >
      {initialsOf(ownerName)}
    </div>
  );
}

function SkeletonCard({ index }: { index: number }): ReactElement {
  const style: CSSProperties = {
    height: 116,
    borderRadius: 10,
    border: `1px solid ${PANEL_BORDER}`,
    backgroundColor: PANEL_BACKGROUND,
    padding: 16,
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
      data-testid="projects-skeleton-card"
      data-skeleton-index={index}
      aria-hidden="true"
    >
      <div style={{ ...bar, width: "60%", height: 14 }} />
      <div style={{ ...bar, width: "40%" }} />
      <div style={{ flex: 1 }} />
      <div style={{ ...bar, width: "30%" }} />
    </div>
  );
}

function ProjectCard({
  project,
  onOpen,
}: {
  project: Project;
  onOpen: (project: Project) => void;
}): ReactElement {
  const card: CSSProperties = {
    height: 116,
    borderRadius: 10,
    border: `1px solid ${PANEL_BORDER}`,
    backgroundColor: PANEL_BACKGROUND,
    padding: 16,
    display: "flex",
    flexDirection: "column",
    gap: 8,
    textAlign: "left",
    color: TEXT_PRIMARY,
    cursor: "pointer",
    boxSizing: "border-box",
  };
  const nameStyle: CSSProperties = {
    fontSize: 14,
    fontWeight: 600,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const metaStyle: CSSProperties = {
    fontSize: 12,
    color: TEXT_SECONDARY,
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
  };
  const footerStyle: CSSProperties = {
    marginTop: "auto",
    display: "flex",
    alignItems: "center",
    gap: 8,
    fontSize: 12,
    color: TEXT_SECONDARY,
  };
  return (
    <button
      type="button"
      onClick={() => onOpen(project)}
      style={card}
      aria-label={`Open project ${project.name}`}
      data-testid="project-card"
      data-project-id={project.id}
    >
      <div style={nameStyle}>{project.name}</div>
      <div style={metaStyle}>
        <span>{formatRelativeTime(project.lastActivityAt)}</span>
        <span>
          {project.chatCount} chat{project.chatCount === 1 ? "" : "s"}
        </span>
      </div>
      <div style={footerStyle}>
        <ProjectAvatar
          ownerName={project.ownerName}
          ownerAvatarUrl={project.ownerAvatarUrl}
        />
        <span
          style={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {project.ownerName}
        </span>
      </div>
    </button>
  );
}

function NewProjectControl({
  onCreate,
}: {
  onCreate: (name: string) => Promise<void>;
}): ReactElement {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const close = useCallback(() => {
    setOpen(false);
    setName("");
    setError(null);
    setSubmitting(false);
  }, []);

  const submit = useCallback(
    async (event?: FormEvent<HTMLFormElement>) => {
      if (event !== undefined) event.preventDefault();
      const trimmed = name.trim();
      if (trimmed.length === 0) return;
      setSubmitting(true);
      setError(null);
      try {
        await onCreate(trimmed);
        close();
      } catch (e) {
        const message = e instanceof Error ? e.message : "Failed to create";
        setError(message);
        setSubmitting(false);
      }
    },
    [name, onCreate, close],
  );

  const onKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLInputElement>) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
      }
    },
    [close],
  );

  const triggerStyle: CSSProperties = {
    height: 36,
    padding: "0 14px",
    borderRadius: 8,
    border: `1px solid ${PANEL_BORDER_STRONG}`,
    backgroundColor: "transparent",
    color: ACCENT,
    fontSize: 13,
    fontWeight: 600,
    cursor: "pointer",
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
  };
  const formStyle: CSSProperties = {
    display: "flex",
    gap: 8,
    alignItems: "center",
  };
  const inputStyle: CSSProperties = {
    flex: 1,
    height: 36,
    padding: "0 12px",
    borderRadius: 8,
    border: `1px solid ${PANEL_BORDER_STRONG}`,
    backgroundColor: PANEL_BACKGROUND,
    color: TEXT_PRIMARY,
    fontSize: 13,
    outline: "none",
  };
  const submitStyle: CSSProperties = {
    height: 36,
    padding: "0 14px",
    borderRadius: 8,
    border: "none",
    backgroundColor: ACCENT,
    color: ACCENT_CONTRAST,
    fontSize: 13,
    fontWeight: 600,
    cursor: "pointer",
    opacity: submitting || name.trim().length === 0 ? 0.6 : 1,
  };
  const cancelStyle: CSSProperties = {
    height: 36,
    padding: "0 12px",
    borderRadius: 8,
    border: `1px solid ${PANEL_BORDER}`,
    backgroundColor: "transparent",
    color: TEXT_SECONDARY,
    fontSize: 13,
    cursor: "pointer",
  };
  const errorStyle: CSSProperties = {
    color: DANGER,
    fontSize: 12,
    marginTop: 6,
  };

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        style={triggerStyle}
        data-testid="projects-new-trigger"
        aria-label="New project"
      >
        + New project
      </button>
    );
  }
  return (
    <div data-testid="projects-new-form-wrapper">
      <form onSubmit={submit} style={formStyle} aria-label="New project form">
        <input
          ref={inputRef}
          type="text"
          value={name}
          placeholder="Project name"
          aria-label="Project name"
          onChange={(e) => setName(e.target.value)}
          onKeyDown={onKeyDown}
          style={inputStyle}
          disabled={submitting}
          data-testid="projects-new-input"
        />
        <button
          type="submit"
          style={submitStyle}
          disabled={submitting || name.trim().length === 0}
          data-testid="projects-new-submit"
        >
          {submitting ? "Creating…" : "Create"}
        </button>
        <button
          type="button"
          onClick={close}
          style={cancelStyle}
          disabled={submitting}
        >
          Cancel
        </button>
      </form>
      {error !== null ? (
        <div style={errorStyle} role="alert" data-testid="projects-new-error">
          {error}
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
      data-testid="projects-error"
      data-state="error"
    >
      <div style={{ fontSize: 14, fontWeight: 600 }}>
        Could not load projects
      </div>
      <div style={subStyle}>{message}</div>
      <button
        type="button"
        onClick={onRetry}
        style={retryStyle}
        data-testid="projects-retry"
      >
        Retry
      </button>
    </div>
  );
}

function EmptyState({
  onCreate,
}: {
  onCreate: (name: string) => Promise<void>;
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
    gap: 14,
  };
  return (
    <div role="status" style={wrapper} data-testid="projects-empty">
      <div style={{ fontSize: 16, fontWeight: 600 }}>No projects yet</div>
      <div style={{ color: TEXT_FAINT, fontSize: 13, maxWidth: 420 }}>
        Group related chats, runs, and saved artifacts into projects. Create the
        first one to get started.
      </div>
      <NewProjectControl onCreate={onCreate} />
    </div>
  );
}

export function ProjectsDestination(): ReactElement {
  const transport = useTransport();
  const router = useRouter<ArtifactRoute>();
  const [state, setState] = useState<ViewState>({ kind: "loading" });
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    transport
      .request<ProjectsResponse>({ method: "GET", path: "/v1/projects" })
      .then((response) => {
        if (cancelled) return;
        setState({ kind: "ready", projects: response.projects });
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

  const handleOpen = useCallback(
    (project: Project) => {
      router.navigate({ kind: "workspace", workspaceId: project.id });
    },
    [router],
  );

  const handleCreate = useCallback(
    async (name: string): Promise<void> => {
      const created = await transport.request<Project>({
        method: "POST",
        path: "/v1/projects",
        body: { name },
      });
      setState((prev) => {
        if (prev.kind !== "ready") {
          return { kind: "ready", projects: [created] };
        }
        return { kind: "ready", projects: [created, ...prev.projects] };
      });
    },
    [transport],
  );

  const handleRetry = useCallback(() => {
    setReloadToken((t) => t + 1);
  }, []);

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
  const headerStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
  };
  const titleStyle: CSSProperties = {
    fontSize: 20,
    fontWeight: 600,
  };
  const gridStyle: CSSProperties = {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
    gap: 16,
  };

  const grid = useMemo(() => {
    if (state.kind === "loading") {
      return (
        <div style={gridStyle} data-testid="projects-grid" data-state="loading">
          {Array.from({ length: SKELETON_CARD_COUNT }).map((_, i) => (
            <SkeletonCard key={i} index={i} />
          ))}
        </div>
      );
    }
    if (state.kind === "ready") {
      return (
        <div style={gridStyle} data-testid="projects-grid" data-state="ready">
          {state.projects.map((project) => (
            <ProjectCard
              key={project.id}
              project={project}
              onOpen={handleOpen}
            />
          ))}
        </div>
      );
    }
    return null;
  }, [state, gridStyle, handleOpen]);

  return (
    <section
      aria-label="Projects destination"
      data-testid="projects-destination"
      data-state={state.kind}
      style={rootStyle}
    >
      <div style={containerStyle}>
        <div style={headerStyle}>
          <div style={titleStyle}>Projects</div>
          {state.kind === "ready" && state.projects.length > 0 ? (
            <NewProjectControl onCreate={handleCreate} />
          ) : null}
        </div>
        {state.kind === "error" ? (
          <ErrorPanel message={state.message} onRetry={handleRetry} />
        ) : null}
        {state.kind === "ready" && state.projects.length === 0 ? (
          <EmptyState onCreate={handleCreate} />
        ) : null}
        {grid}
      </div>
    </section>
  );
}
