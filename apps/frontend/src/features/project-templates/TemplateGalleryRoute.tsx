// TemplateGalleryRoute — data binder for the Phase 6.5 Project Templates
// destination (sub-PRD `projects-extensions-prd.md` §7.6).
//
// Mirrors the P6-C ProjectsRoute / P5-C RoutinesRoute pattern:
//   1. Fetches `GET /v1/project-templates` via `projectTemplatesApi` and
//      owns loading / error / ready states (§7.6 TemplateGallery).
//   2. Surfaces a viewer-filter (`all` / `mine`) without re-encoding the
//      filter as a server param; templates list responses are tenant
//      sized and the filter is small enough for client-side projection.
//      Sub-PRD §7.6 FilterTabs (multi-value OR per cross-audit §1.5).
//   3. Proxies mutation calls (fork / delete) back to the backend; on
//      success, the local list is reconciled.
//   4. Fork → on success, calls `onForked(newProjectId)` if the host wires
//      a callback (the App.tsx adapter navigates to the new project's
//      detail surface).
//
// No SSE: sub-PRD §7 does not define a `/v1/project-templates/stream`
// channel. List freshness on this surface is fetch-driven only (the
// initial load + refetch on mutation). When P6.5-A1's backend gains an
// SSE channel, layer it in here in the same exponential-backoff shape
// the ProjectsRoute uses.

import { useCallback, useEffect, useState, type ReactElement } from "react";

import type { RequestIdentity } from "../../api/config";
import {
  deleteProjectTemplate,
  fetchProjectTemplates,
  forkProjectTemplate,
} from "../../api/projectTemplatesApi";
import type {
  ForkProjectTemplateRequest,
  ProjectTemplate,
  ProjectTemplateId,
  ProjectTemplateListResponse,
} from "../../api/projectTemplatesApi";
import { errorMessage } from "../../utils/errors";

interface TemplateGalleryRouteProps {
  readonly identity: RequestIdentity;
  /**
   * Callback invoked with the freshly-forked project's id when fork
   * succeeds. The host (App.tsx) navigates to the new project's detail
   * surface. Optional so unit tests can drive the route without a
   * router stub.
   */
  readonly onForked?: (newProjectId: string) => void;
}

/** Viewer-relative filter (sub-PRD §7.6). */
type GalleryFilter = "all" | "mine";

type ViewState =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | {
      readonly kind: "ready";
      readonly items: ReadonlyArray<ProjectTemplate>;
    };

export function TemplateGalleryRoute({
  identity,
  onForked,
}: TemplateGalleryRouteProps): ReactElement {
  const [state, setState] = useState<ViewState>({ kind: "loading" });
  const [reloadToken, setReloadToken] = useState(0);
  const [filter, setFilter] = useState<GalleryFilter>("all");
  const [pendingError, setPendingError] = useState<string | null>(null);

  // ---- Initial fetch ------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });

    fetchProjectTemplates(identity, { sort: "created_at:desc", limit: 50 })
      .then((list: ProjectTemplateListResponse) => {
        if (cancelled) return;
        setState({ kind: "ready", items: list.items });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          kind: "error",
          message: errorMessage(error, "Could not load templates."),
        });
      });

    return () => {
      cancelled = true;
    };
  }, [identity, reloadToken]);

  // ---- Mutation helpers ---------------------------------------------------

  const handleFork = useCallback(
    async (
      template: ProjectTemplate,
      override: ForkProjectTemplateRequest,
    ): Promise<void> => {
      setPendingError(null);
      try {
        const forked = await forkProjectTemplate(
          identity,
          template.id,
          override,
        );
        if (onForked !== undefined) {
          onForked(forked.id);
        }
      } catch (error: unknown) {
        setPendingError(errorMessage(error, "Could not fork template."));
      }
    },
    [identity, onForked],
  );

  const handleDelete = useCallback(
    async (id: ProjectTemplateId): Promise<void> => {
      setPendingError(null);
      try {
        await deleteProjectTemplate(identity, id);
        setState((prev) => removeById(prev, id));
      } catch (error: unknown) {
        setPendingError(errorMessage(error, "Could not delete template."));
      }
    },
    [identity],
  );

  // ---- Render -------------------------------------------------------------
  if (state.kind === "error") {
    return (
      <section
        aria-label="Template gallery"
        data-testid="template-gallery-route"
        data-state="error"
        style={{
          height: "100%",
          width: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 24,
          boxSizing: "border-box",
          backgroundColor: "var(--color-bg)",
          color: "var(--color-text)",
        }}
      >
        <div
          role="alert"
          data-testid="template-gallery-route-error"
          style={{
            border: "1px solid var(--color-border)",
            borderRadius: 12,
            backgroundColor: "var(--color-surface)",
            padding: 32,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 12,
            maxWidth: 480,
          }}
        >
          <div style={{ fontSize: 14, fontWeight: 600 }}>
            Could not load templates
          </div>
          <div
            style={{ fontSize: 13, color: "var(--color-text-muted)" }}
            data-testid="template-gallery-route-error-message"
          >
            {state.message}
          </div>
          <button
            type="button"
            data-testid="template-gallery-route-retry"
            onClick={() => setReloadToken((t) => t + 1)}
            style={{
              height: 32,
              padding: "0 14px",
              borderRadius: 8,
              border: "1px solid var(--color-border-strong)",
              backgroundColor: "transparent",
              color: "var(--color-accent)",
              fontSize: 13,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Retry
          </button>
        </div>
      </section>
    );
  }

  const items = state.kind === "ready" ? state.items : [];
  // Client-side projection of the "mine" filter — sub-PRD §7.6
  // ACL allows any tenant member to list every template, so the toggle is
  // a viewer-relative view, not a server-side filter.
  const visible = items.filter((t) =>
    filter === "mine" ? t.owner_user_id === identity.userId : true,
  );

  return (
    <section
      aria-label="Template gallery"
      data-testid="template-gallery-route"
      data-state={state.kind}
      data-item-count={visible.length}
      data-filter={filter}
      style={{
        height: "100%",
        width: "100%",
        overflow: "auto",
        padding: 24,
        boxSizing: "border-box",
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
          marginBottom: 16,
        }}
      >
        <h1 style={{ fontSize: 18, fontWeight: 700, margin: 0 }}>
          Project templates
        </h1>
        <div
          role="tablist"
          aria-label="Template viewer filter"
          data-testid="template-gallery-route-filters"
          style={{ display: "flex", gap: 8 }}
        >
          <button
            type="button"
            role="tab"
            aria-selected={filter === "all"}
            data-testid="template-gallery-route-filter-all"
            data-active={filter === "all"}
            onClick={() => setFilter("all")}
          >
            All
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={filter === "mine"}
            data-testid="template-gallery-route-filter-mine"
            data-active={filter === "mine"}
            onClick={() => setFilter("mine")}
          >
            Mine
          </button>
        </div>
      </header>

      {pendingError !== null && (
        <div
          role="status"
          data-testid="template-gallery-route-pending-error"
          style={{
            marginBottom: 16,
            padding: 12,
            border: "1px solid var(--color-border-strong)",
            borderRadius: 8,
            backgroundColor: "var(--color-surface)",
            fontSize: 13,
          }}
        >
          {pendingError}
        </div>
      )}

      {state.kind === "loading" ? (
        <div
          data-testid="template-gallery-route-loading"
          style={{ fontSize: 13 }}
        >
          Loading templates…
        </div>
      ) : visible.length === 0 ? (
        <div
          data-testid="template-gallery-route-empty"
          style={{ fontSize: 13, color: "var(--color-text-muted)" }}
        >
          {filter === "mine"
            ? "You haven't authored any templates yet."
            : "No templates yet."}
        </div>
      ) : (
        <ul
          data-testid="template-gallery-route-list"
          style={{
            listStyle: "none",
            margin: 0,
            padding: 0,
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
            gap: 16,
          }}
        >
          {visible.map((template) => (
            <li
              key={template.id}
              data-testid="template-gallery-route-card"
              data-template-id={template.id}
              data-template-owner={template.owner_user_id}
              style={{
                padding: 16,
                border: "1px solid var(--color-border)",
                borderRadius: 12,
                backgroundColor: "var(--color-surface)",
                display: "flex",
                flexDirection: "column",
                gap: 8,
              }}
            >
              <div style={{ fontSize: 14, fontWeight: 600 }}>
                {template.snapshot.icon_emoji !== null && (
                  <span aria-hidden="true" style={{ marginRight: 6 }}>
                    {template.snapshot.icon_emoji}
                  </span>
                )}
                {template.name}
              </div>
              <div
                style={{
                  fontSize: 12,
                  color: "var(--color-text-muted)",
                  display: "-webkit-box",
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: "vertical",
                  overflow: "hidden",
                }}
              >
                {template.description}
              </div>
              <div
                data-testid="template-gallery-route-card-counts"
                style={{ fontSize: 11, color: "var(--color-text-muted)" }}
              >
                {`seeded ${template.snapshot.seeded_todos.length} todos · ${template.snapshot.seeded_routines.length} routines`}
              </div>
              <div
                style={{
                  marginTop: "auto",
                  display: "flex",
                  gap: 8,
                  paddingTop: 8,
                }}
              >
                <button
                  type="button"
                  data-testid="template-gallery-route-fork"
                  data-template-id={template.id}
                  onClick={() => {
                    void handleFork(template, {
                      name: template.name,
                      description: template.description,
                      color_hue: template.snapshot.color_hue ?? undefined,
                      icon_emoji: template.snapshot.icon_emoji ?? undefined,
                    });
                  }}
                >
                  Fork
                </button>
                {template.owner_user_id === identity.userId && (
                  <button
                    type="button"
                    data-testid="template-gallery-route-delete"
                    data-template-id={template.id}
                    onClick={() => {
                      void handleDelete(template.id);
                    }}
                  >
                    Delete
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ===========================================================================
// State reducers — pure, testable.
// ===========================================================================

function removeById(prev: ViewState, id: ProjectTemplateId): ViewState {
  if (prev.kind !== "ready") return prev;
  const idx = prev.items.findIndex((t) => t.id === id);
  if (idx === -1) return prev;
  return {
    ...prev,
    items: prev.items.slice(0, idx).concat(prev.items.slice(idx + 1)),
  };
}
