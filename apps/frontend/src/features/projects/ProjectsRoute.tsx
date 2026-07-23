// ProjectsRoute — data binder for the Phase 6 Projects destination
// (the 13th destination per
// `docs/atlas-new-design/destinations/projects-prd.md`).
//
// Mirrors the P5-C RoutinesRoute pattern:
//   1. Fetches `GET /v1/projects` via `projectsApi` and owns
//      loading / error / ready states (sub-PRD §3.2 list view).
//   2. Opens the `/v1/projects/stream` SSE channel (sub-PRD §4.2 +
//      §3.8) with exponential-backoff reconnect, tracking the highest
//      `sequence_no` for `?after_sequence=N` resume (cross-audit §5.2).
//   3. On `project_member_added` for the current user, refetches the
//      full project and prepends it to the local list so the AppRail
//      auto-adds the new project without a hard navigate (sub-PRD §3.8
//      "auto-add to rail").
//   4. Proxies state changes (archive / activate / star / unstar /
//      delete) back to the backend, optimistically driving the
//      SSE-merged local list while the server confirms.
//   5. Renders a host-side scaffold list (real names + archive / activate
//      / star / delete affordances) for the un-focused view. The list
//      still uses the scaffold — not `<ProjectsDestination>`'s card grid —
//      because the card name is a `<ItemLink kind="project">` whose stub
//      resolver renders the literal label "Project" (real per-project
//      names await the resolver upgrade); the scaffold keeps names honest.
//   6. Detail binder (PR-4.4b / FR-4.11–4.13): a row's "Open" affordance
//      focuses a project and lazy-loads project + members + activity, then
//      mounts `<ProjectDetailView>` through `<ProjectsDestination>`'s own
//      `renderDetail` / `focusedProjectId` slot. The Files tab degrades to
//      "coming soon" (the `files` prop is omitted — there is no
//      `GET /v1/projects/{id}/files` endpoint yet, PRD §11). A chat row
//      opens the Run cockpit via the injected `onOpenRun` callback; a file
//      row (once wired) opens its artifact via `<ItemLink kind="library_file">`.
//
// Why a feature-level wrapper, not props on `<ProjectsDestination>`
// today: the package component reads through its own Transport hook and
// has no membership-stream behaviour. Owning the data flow + state
// mutation + SSE here lets the destination component reshape without
// forcing an App.tsx-level rewrite — same compromise the
// InboxRoute / TodosRoute / RoutinesRoute waves made.

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

import {
  ProjectDetailView,
  ProjectsDestination,
  type ProjectDetail,
  type ProjectDetailViewProps,
} from "@0x-copilot/chat-surface";
import type {
  ChatArchiveRow,
  ConversationId,
  ProjectFileRow,
  SectionResult,
} from "@0x-copilot/api-types";

import type { RequestIdentity } from "../../api/config";
import {
  activateProject,
  archiveProject,
  deleteProject,
  fetchProject,
  fetchProjectMembers,
  fetchProjects,
  starProject,
  streamProjectEvents,
  unstarProject,
} from "../../api/projectsApi";
// PRD-07 — the web binding of chat-surface's `ProjectDataPort`. Feeds the
// detail view's project-scoped Chats + Files sections; replaces the old
// project-activity read (which called a route that never existed).
import { createWebProjectDataPort } from "./ProjectDataPort";
import type {
  Project,
  ProjectActivity as ProjectActivityRecord,
  ProjectId,
  ProjectListResponse,
  ProjectMembership,
  ProjectStreamEnvelope,
  ProjectSummary,
} from "@0x-copilot/api-types";
import { errorMessage } from "../../utils/errors";

// The Members / Activity tab view-models aren't re-exported from the
// chat-surface barrel; derive them from the exported props contract so the
// adapters below stay type-checked against the component's expectations.
type ProjectMember = NonNullable<ProjectDetailViewProps["members"]>[number];
type ProjectActivityRow = NonNullable<
  ProjectDetailViewProps["activity"]
>[number];

// PRD-04 Seam B — the dead `library_file` resolver registration is removed.
// It was shadowed at import time by `destinations/library/index.ts` (first
// writer won), so this block never executed; cross-destination ROUTING now
// lives in the host route table (`src/app/itemRoutes.ts`), and a file row's
// display text is the caller's (`<ItemLink label={row.name}>`).

/** Reconnect backoff bounds (mirrors RoutinesRoute / sub-PRD §3.8 conventions). */
const RECONNECT_BACKOFF_MIN_MS = 1_000;
const RECONNECT_BACKOFF_MAX_MS = 30_000;

interface ProjectsRouteProps {
  readonly identity: RequestIdentity;
  /**
   * Open the Run cockpit for a conversation (FR-4.12). A chat row in the
   * project detail funnels through this injected callback rather than an
   * in-component navigation, keeping the route decoupled from the host's
   * `AppRoute` union — the same seam as `ActivityRoute.onOpenRun` /
   * `ChatsArchiveRoute.onOpenRun`. App-level dispatch wires it in PR-4.11;
   * until then it defaults to a no-op.
   */
  readonly onOpenRun?: (conversationId: ConversationId) => void;
}

type ViewState =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | {
      readonly kind: "ready";
      readonly items: ReadonlyArray<ProjectSummary>;
      readonly highestSequenceNo: number;
    };

/**
 * State for the focused-project detail pane. Loaded lazily when a row's
 * "Open" affordance sets `focusedProjectId` (FR-4.11). `fetchProject` is
 * fatal (its failure fails the pane); the members / activity reads are
 * members-only and degrade to empty on 403 so the header + files
 * "coming soon" still render for a non-member viewer.
 */
type DetailState =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | {
      readonly kind: "ready";
      readonly project: Project;
      readonly members: ReadonlyArray<ProjectMembership>;
      readonly activity: ReadonlyArray<ProjectActivityRecord>;
      // PRD-07 — the project-scoped Chats + Files sections, fed by the web
      // `ProjectDataPort`. Each is a `SectionResult` so the detail view's
      // 4-state machine drives itself; the port never throws.
      readonly chats: SectionResult<ReadonlyArray<ChatArchiveRow>>;
      readonly files: SectionResult<ReadonlyArray<ProjectFileRow>>;
    };

/**
 * Lift a `Project` (full row from `fetchProject`) into a
 * `ProjectSummary` (list-view row). The two share most fields; the
 * extra detail-only fields (`archived_at`, `created_at`) drop on lift.
 *
 * Used when an SSE membership event triggers a refetch — the rail / list
 * view only needs summary fields, so we converge here rather than
 * holding two parallel lists.
 */
function toSummary(p: Project): ProjectSummary {
  return {
    id: p.id,
    tenant_id: p.tenant_id,
    name: p.name,
    description: p.description,
    icon_emoji: p.icon_emoji,
    color_hue: p.color_hue,
    status: p.status,
    owner_user_id: p.owner_user_id,
    viewer_role: p.viewer_role,
    viewer_starred: p.viewer_starred,
    counts: p.counts,
    last_activity_at: p.last_activity_at,
    updated_at: p.updated_at,
  };
}

/**
 * Apply one durable SSE envelope to the local project list. Pure
 * function so a test can drive it without a mounted component.
 *
 * Semantics (sub-PRD §4.1 event types):
 * - `project_created`                  → prepend if payload is a summary; ignored otherwise (rail is for viewer's projects only).
 * - `project_updated` / `project_archived` / `project_activated`
 *                                       → in-place replace by id when payload is a summary.
 * - `project_deleted`                  → drop the matching id.
 * - `project_member_added`             → if current viewer is the target, leave a "needs refetch" marker for the caller (handled in the component via a refetch effect).
 * - `project_member_removed`           → if current viewer is the target, drop the row from the rail.
 * - Other events                       → no-op at the list level (membership / role / ownership / activity-append).
 *
 * The function intentionally does NOT call any side effects (fetch /
 * navigate); the component layer above is responsible for translating
 * a `project_member_added(viewer)` envelope into a `fetchProject` call.
 */
export function applyProjectEnvelope(
  items: ReadonlyArray<ProjectSummary>,
  envelope: ProjectStreamEnvelope,
  viewerUserId: string,
): ReadonlyArray<ProjectSummary> {
  const idx = items.findIndex((p) => p.id === envelope.project_id);

  if (envelope.event_type === "project_deleted") {
    if (idx === -1) return items;
    return items.slice(0, idx).concat(items.slice(idx + 1));
  }

  if (envelope.event_type === "project_member_removed") {
    // Drop the row from the viewer's rail ONLY when the removal targets
    // the viewer themselves. Removal of any other member is purely a
    // members-tab event; the project itself stays on the rail.
    const payload = envelope.payload as { readonly user_id?: string };
    if (payload.user_id === viewerUserId && idx !== -1) {
      return items.slice(0, idx).concat(items.slice(idx + 1));
    }
    return items;
  }

  if (
    envelope.event_type === "project_created" ||
    envelope.event_type === "project_updated" ||
    envelope.event_type === "project_archived" ||
    envelope.event_type === "project_activated"
  ) {
    // The server emits these envelopes with a `ProjectSummary` or full
    // `Project` payload (sub-PRD §4.1). Both expose enough fields to
    // narrow to a summary; ignore anything that doesn't look like one.
    if (!isSummaryShape(envelope.payload)) {
      return items;
    }
    const summary = envelope.payload as ProjectSummary;
    if (idx === -1) {
      // Treat a "new" envelope for an unseen project as a prepend; an
      // update envelope for an unseen project is also a prepend so the
      // list stays consistent if the initial fetch raced the stream.
      return [summary, ...items];
    }
    const next = items.slice();
    next[idx] = summary;
    return next;
  }

  // project_member_added / project_member_role_changed /
  // project_ownership_transferred / project_activity_appended:
  //   - The list-level reducer is a no-op for these because the
  //     summary fields they touch (members count, owner_user_id,
  //     last_activity_at) all require a refetch to project correctly
  //     against the viewer's permission scope. The component layer
  //     handles the refetch effect.
  return items;
}

/**
 * Loose structural check: does this payload look like a ProjectSummary?
 *
 * Membership / state-change envelopes carry the small descriptor object
 * (`{ project_id, user_id, ... }`); we only mutate the local list for
 * envelopes whose payload exposes the summary discriminator fields.
 */
function isSummaryShape(value: unknown): boolean {
  if (value === null || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.id === "string" &&
    typeof v.name === "string" &&
    typeof v.status === "string" &&
    typeof v.icon_emoji === "string"
  );
}

export function ProjectsRoute({
  identity,
  onOpenRun,
}: ProjectsRouteProps): ReactElement {
  const [state, setState] = useState<ViewState>({ kind: "loading" });
  const [reloadToken, setReloadToken] = useState(0);
  const [pendingError, setPendingError] = useState<string | null>(null);

  // ---- Detail pane (FR-4.11) ---------------------------------------
  //
  // A row's "Open" affordance focuses a project; the pane then lazy-loads
  // the project + members + activity and mounts `<ProjectDetailView>`
  // inside `<ProjectsDestination>`'s `renderDetail` / `focusedProjectId`
  // slot. `focusedProjectId === null` collapses back to the list.
  const [focusedProjectId, setFocusedProjectId] = useState<ProjectId | null>(
    null,
  );
  const [detail, setDetail] = useState<DetailState | null>(null);
  const [detailReloadToken, setDetailReloadToken] = useState(0);

  // PRD-03 Move 1: priming the cross-destination project-name cache is no
  // longer a host duty — `ProjectsDestination` primes it from `items` in an
  // effect, so both hosts get real `<ItemLink kind="project">` names without
  // each remembering a cache-priming call (desktop never did, so every desktop
  // project link read the literal "Project").

  // ---- Initial fetch ------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });

    fetchProjects(identity, { limit: 50 })
      .then((list: ProjectListResponse) => {
        if (cancelled) return;
        setState({
          kind: "ready",
          items: list.items,
          highestSequenceNo: 0,
        });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          kind: "error",
          message: errorMessage(error, "Could not load projects."),
        });
      });

    return () => {
      cancelled = true;
    };
  }, [identity, reloadToken]);

  // ---- SSE subscription with exponential-backoff reconnect ---------
  //
  // The SSE channel surfaces both list-mutating envelopes (handled
  // synchronously by `applyProjectEnvelope`) and viewer-membership
  // envelopes (`project_member_added` for the viewer → refetch + add to
  // rail; sub-PRD §3.8). Refetching is done here so the reducer stays
  // pure.
  const backoffRef = useRef(RECONNECT_BACKOFF_MIN_MS);
  useEffect(() => {
    if (state.kind !== "ready") {
      return;
    }
    let cancelled = false;
    let activeHandle: { close(): void } | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    backoffRef.current = RECONNECT_BACKOFF_MIN_MS;

    function open(): void {
      if (cancelled) return;
      // Read the highest seen seq from the latest state snapshot via
      // setState's updater so reconnect resumes from the right point
      // even after several deltas have landed since the last open.
      let afterSequence = 0;
      setState((prev) => {
        if (prev.kind === "ready") afterSequence = prev.highestSequenceNo;
        return prev;
      });

      activeHandle = streamProjectEvents({
        identity,
        afterSequence: afterSequence > 0 ? afterSequence : undefined,
        onOpen: () => {
          backoffRef.current = RECONNECT_BACKOFF_MIN_MS;
        },
        onEvent: (envelope) => {
          if (cancelled) return;
          setState((prev) => {
            if (prev.kind !== "ready") return prev;
            const items = applyProjectEnvelope(
              prev.items,
              envelope,
              identity.userId,
            );
            const highestSequenceNo = Math.max(
              prev.highestSequenceNo,
              envelope.sequence_no,
            );
            return { kind: "ready", items, highestSequenceNo };
          });

          // Auto-add-to-rail side effect — the reducer left this for us
          // because it needs a network refetch to project the project
          // correctly against the viewer's permission scope. Sub-PRD
          // §3.8: when the viewer is the newly added member, fetch the
          // full project and prepend it to the rail.
          if (envelope.event_type === "project_member_added") {
            const payload = envelope.payload as {
              readonly user_id?: string;
            };
            if (payload.user_id === identity.userId) {
              void fetchProject(identity, envelope.project_id)
                .then((project) => {
                  if (cancelled) return;
                  setState((prev) => {
                    if (prev.kind !== "ready") return prev;
                    const summary = toSummary(project);
                    const idx = prev.items.findIndex(
                      (p) => p.id === summary.id,
                    );
                    if (idx === -1) {
                      return { ...prev, items: [summary, ...prev.items] };
                    }
                    const next = prev.items.slice();
                    next[idx] = summary;
                    return { ...prev, items: next };
                  });
                })
                .catch(() => undefined);
            }
          }
        },
        onError: () => {
          if (cancelled) return;
          activeHandle?.close();
          activeHandle = null;
          const delay = backoffRef.current;
          backoffRef.current = Math.min(
            backoffRef.current * 2,
            RECONNECT_BACKOFF_MAX_MS,
          );
          reconnectTimer = setTimeout(open, delay);
        },
      });
    }

    open();

    return () => {
      cancelled = true;
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer);
      }
      activeHandle?.close();
    };
    // `state.kind` gates open(); we depend on it (not the full `state`
    // object) so an SSE-driven merge does NOT tear down + reopen the
    // stream.
  }, [identity, state.kind]);

  // ---- Detail fetch (project + members + chats + files) ------------
  //
  // Runs whenever a project is focused. `fetchProject` is the fatal read;
  // members are members-only and degrade to empty on failure (403 for a
  // non-member viewer). The project-scoped Chats + Files sections come from
  // the web `ProjectDataPort` (PRD-07): each resolves a `SectionResult`
  // (never throws), so an upstream error degrades that section to its own
  // error/empty state without failing the pane. The team-profile Activity tab
  // receives `[]` — `GET /v1/projects/{id}/activity` never existed (PRD §11
  // non-goals), so this PRD stops calling it rather than building it.
  useEffect(() => {
    if (focusedProjectId === null) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setDetail({ kind: "loading" });

    const port = createWebProjectDataPort(identity);

    Promise.all([
      fetchProject(identity, focusedProjectId),
      fetchProjectMembers(identity, focusedProjectId).catch(() => ({
        items: [] as ReadonlyArray<ProjectMembership>,
        next_cursor: null,
      })),
      port.listProjectChats(focusedProjectId),
      port.listProjectFiles(focusedProjectId),
    ])
      .then(([project, membersResp, chats, files]) => {
        if (cancelled) return;
        setDetail({
          kind: "ready",
          project,
          members: membersResp.items,
          activity: [],
          chats,
          files,
        });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setDetail({
          kind: "error",
          message: errorMessage(error, "Could not load project."),
        });
      });

    return () => {
      cancelled = true;
    };
  }, [identity, focusedProjectId, detailReloadToken]);

  // ---- Mutation helpers (archive / activate / star / unstar / delete)
  //
  // Each helper replaces the local row optimistically when the server
  // acknowledges, then lets the next SSE delta confirm. Errors surface
  // as a non-fatal pendingError banner — the list keeps rendering, the
  // user can retry. Mirrors the RoutinesRoute mutation pattern.

  const handleArchive = useCallback(
    async (id: ProjectId): Promise<void> => {
      setPendingError(null);
      try {
        const updated = await archiveProject(identity, id);
        setState((prev) => mergeUpdated(prev, toSummary(updated)));
      } catch (error: unknown) {
        setPendingError(errorMessage(error, "Could not archive project."));
      }
    },
    [identity],
  );

  const handleActivate = useCallback(
    async (id: ProjectId): Promise<void> => {
      setPendingError(null);
      try {
        const updated = await activateProject(identity, id);
        setState((prev) => mergeUpdated(prev, toSummary(updated)));
      } catch (error: unknown) {
        setPendingError(errorMessage(error, "Could not activate project."));
      }
    },
    [identity],
  );

  const handleStar = useCallback(
    async (id: ProjectId, starred: boolean): Promise<void> => {
      setPendingError(null);
      try {
        const updated = starred
          ? await unstarProject(identity, id)
          : await starProject(identity, id);
        setState((prev) => mergeUpdated(prev, toSummary(updated)));
      } catch (error: unknown) {
        setPendingError(errorMessage(error, "Could not update star."));
      }
    },
    [identity],
  );

  const handleDelete = useCallback(
    async (id: ProjectId): Promise<void> => {
      setPendingError(null);
      try {
        await deleteProject(identity, id);
        setState((prev) => removeById(prev, id));
      } catch (error: unknown) {
        setPendingError(errorMessage(error, "Could not delete project."));
      }
    },
    [identity],
  );

  // ---- Render -------------------------------------------------------
  if (state.kind === "error") {
    return (
      <section
        aria-label="Projects destination"
        data-testid="projects-route"
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
          data-testid="projects-route-error"
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
            Could not load projects
          </div>
          <div
            style={{ fontSize: 13, color: "var(--color-text-muted)" }}
            data-testid="projects-route-error-message"
          >
            {state.message}
          </div>
          <button
            type="button"
            data-testid="projects-route-retry"
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

  // Cross-destination tab slot (FR-4.12), used only by the team profile's
  // Todos / Inbox / Library / Routines tabs. PRD-07 deleted the hand-rolled
  // "chats" branch: the solo profile's project chats now flow through the
  // `chats` prop (fed by the web `ProjectDataPort`), not this slot, and the
  // old branch filtered project-activity rows from a route that never existed.
  // The remaining tabs get a placeholder until their filtered destination
  // views are wired app-side.
  const renderCrossDestinationTab = (
    tab: "chats" | "todos" | "inbox" | "library" | "routines",
  ): ReactNode => (
    <div
      data-testid={`projects-crosstab-${tab}`}
      style={{ fontSize: 13, color: "var(--color-text-muted)" }}
    >
      Opens in the {tab} destination filtered to this project.
    </div>
  );

  // Renders the focused project's detail into `<ProjectsDestination>`'s
  // `renderDetail` slot. `files` is intentionally OMITTED so the Files tab
  // degrades to its "coming soon" empty state (FR-4.11): there is no
  // `GET /v1/projects/{id}/files` endpoint yet (PRD §11 files gap), and
  // passing `files` at all — even `null` — would render a skeleton that
  // never resolves. Wire a `SectionResult<ProjectFileRow[]>` here once the
  // backend endpoint lands.
  const renderProjectDetail = (onClose: () => void): ReactNode => {
    const backButton = (
      <button
        type="button"
        data-testid="projects-detail-back"
        onClick={onClose}
        style={{
          background: "transparent",
          border: "none",
          color: "var(--color-accent)",
          cursor: "pointer",
          padding: "0 0 12px",
          fontSize: 13,
          fontWeight: 600,
        }}
      >
        ← All projects
      </button>
    );

    if (detail === null || detail.kind === "loading") {
      return (
        <div>
          {backButton}
          <div data-testid="projects-detail-loading" style={{ fontSize: 13 }}>
            Loading project…
          </div>
        </div>
      );
    }
    if (detail.kind === "error") {
      return (
        <div>
          {backButton}
          <div
            role="alert"
            data-testid="projects-detail-error"
            style={{ fontSize: 13, color: "var(--color-text-muted)" }}
          >
            {detail.message}
            <button
              type="button"
              data-testid="projects-detail-retry"
              onClick={() => setDetailReloadToken((t) => t + 1)}
              style={{
                marginLeft: 12,
                background: "transparent",
                border: "1px solid var(--color-border-strong)",
                borderRadius: 8,
                color: "var(--color-accent)",
                cursor: "pointer",
                padding: "2px 10px",
                fontSize: 13,
              }}
            >
              Retry
            </button>
          </div>
        </div>
      );
    }

    const { project, members, activity, chats, files } = detail;
    const projectDetail: ProjectDetail = {
      id: project.id,
      name: project.name,
      iconEmoji: project.icon_emoji,
      colorHue: project.color_hue,
      description: project.description,
      status: project.status,
      ownerUserId: project.owner_user_id,
      ownerName: ownerNameFor(project, members),
      memberCount: project.counts.members,
      // `counts.chats` is `null` when the facade could not fill it from
      // ai-backend; the solo Chats header prefers the rendered list length, so
      // `null` only surfaces during load. `fileCount` binds to `counts.files`
      // (library `kind='file'` only) — NOT `library_items`, which counts
      // file + page + dataset (PRD-07 fixes the wrong-field bind).
      chatCount: project.counts.chats ?? undefined,
      fileCount: project.counts.files,
    };
    // Only the owner can mutate membership / transfer ownership. Under the
    // solo profile `viewer_role` is null → no management affordances.
    const canManage = project.viewer_role === "owner";

    return (
      <div>
        {backButton}
        <ProjectDetailView
          project={projectDetail}
          members={members.map(toDetailMember)}
          activity={activity.map(toDetailActivity)}
          chats={chats}
          files={files}
          onRetryChats={() => setDetailReloadToken((t) => t + 1)}
          onRetryFiles={() => setDetailReloadToken((t) => t + 1)}
          onOpenChat={(conversationId: ConversationId) =>
            onOpenRun?.(conversationId)
          }
          canManage={canManage}
          renderCrossDestinationTab={(tab) => renderCrossDestinationTab(tab)}
        />
      </div>
    );
  };

  // The controlled list result handed to `<ProjectsDestination>` so its
  // ready-state detail slot renders (the loading / error branches never
  // reach `renderDetail`). We only mount the destination once a project
  // is focused; the un-focused list stays the host-side scaffold below.
  const detailSection: SectionResult<ReadonlyArray<ProjectSummary>> = {
    status: "ok",
    data: items,
  };

  return (
    <section
      aria-label="Projects destination"
      data-testid="projects-route"
      data-state={state.kind}
      data-item-count={items.length}
      data-focused-project-id={focusedProjectId ?? undefined}
      style={{
        height: "100%",
        width: "100%",
        overflow: "auto",
        padding: 24,
        boxSizing: "border-box",
      }}
    >
      {pendingError !== null && (
        <div
          role="status"
          data-testid="projects-route-pending-error"
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
      {focusedProjectId !== null ? (
        // Detail pane — mounted through the destination's own detail slot
        // (FR-4.11). PRD-03: the slot is now one total `detail` binding; web
        // declares `mode: "enabled"` and carries the focused id + slot together.
        <ProjectsDestination
          items={detailSection}
          detail={{
            mode: "enabled",
            focusedProjectId,
            onCloseDetail: () => setFocusedProjectId(null),
            renderDetail: ({ onClose }) => renderProjectDetail(onClose),
          }}
        />
      ) : state.kind === "loading" ? (
        <div data-testid="projects-route-loading" style={{ fontSize: 13 }}>
          Loading projects…
        </div>
      ) : items.length === 0 ? (
        <div
          data-testid="projects-route-empty"
          style={{ fontSize: 13, color: "var(--color-text-muted)" }}
        >
          No projects yet.
        </div>
      ) : (
        <>
          <style>{PROJECTS_GRID_CSS}</style>
          <div data-testid="projects-route-list" className="projects-grid3">
            {items.map((project) => {
              const initial = (project.name.trim()[0] ?? "?").toUpperCase();
              // PRD-07 — the design's "N files" is library `kind='file'` only
              // (`counts.files`), NOT `library_items` (file + page + dataset).
              // `counts.chats` is `null` when the facade could not fill it; the
              // chats segment is hidden then (never a fabricated "0 chats").
              const filesCount = project.counts.files;
              const chatsCount = project.counts.chats;
              return (
                <div
                  key={project.id}
                  data-testid="projects-route-row"
                  data-project-id={project.id}
                  data-project-status={project.status}
                  className="projects-card"
                >
                  {/* The card body is the primary "open" affordance — the
                      whole tile+name+meta block navigates into the detail
                      pane. The lifecycle actions live in the footer below
                      so no button nests inside another. */}
                  <button
                    type="button"
                    data-testid="projects-route-open"
                    data-project-id={project.id}
                    className="projects-card__open"
                    onClick={() => setFocusedProjectId(project.id)}
                  >
                    <span
                      className="proj-ic"
                      aria-hidden="true"
                      data-color-hue={project.color_hue}
                      style={{
                        backgroundColor: `hsl(${project.color_hue} 60% 28% / 0.45)`,
                        border: `1px solid hsl(${project.color_hue} 60% 50% / 0.55)`,
                        color: `hsl(${project.color_hue} 70% 82%)`,
                      }}
                    >
                      {initial}
                    </span>
                    <span className="projects-card__name">{project.name}</span>
                    {project.description.length > 0 ? (
                      <span className="projects-card__desc">
                        {project.description}
                      </span>
                    ) : null}
                    <span className="projects-card__meta">
                      {chatsCount !== null
                        ? `${chatsCount} chat${chatsCount === 1 ? "" : "s"} · ${filesCount} file${filesCount === 1 ? "" : "s"}`
                        : `${filesCount} file${filesCount === 1 ? "" : "s"}`}
                    </span>
                  </button>
                  {/* Member/role chip — FR-4.13: rendered ONLY when the
                      viewer is a member (`viewer_role !== null`). Under the
                      `single_user_desktop` profile the server returns a null
                      `viewer_role`, so the chip is absent (not an empty
                      strip). */}
                  {project.viewer_role !== null ? (
                    <span
                      data-testid="projects-route-role-chip"
                      data-role={project.viewer_role}
                      className="projects-card__role"
                    >
                      {project.viewer_role}
                    </span>
                  ) : null}
                  <div className="projects-card__actions">
                    <button
                      type="button"
                      data-testid="projects-route-star"
                      data-project-id={project.id}
                      onClick={() => {
                        void handleStar(project.id, project.viewer_starred);
                      }}
                    >
                      {project.viewer_starred ? "Unstar" : "Star"}
                    </button>
                    {project.status === "active" ? (
                      <button
                        type="button"
                        data-testid="projects-route-archive"
                        data-project-id={project.id}
                        onClick={() => {
                          void handleArchive(project.id);
                        }}
                      >
                        Archive
                      </button>
                    ) : (
                      <button
                        type="button"
                        data-testid="projects-route-activate"
                        data-project-id={project.id}
                        onClick={() => {
                          void handleActivate(project.id);
                        }}
                      >
                        Activate
                      </button>
                    )}
                    <button
                      type="button"
                      data-testid="projects-route-delete"
                      data-project-id={project.id}
                      onClick={() => {
                        void handleDelete(project.id);
                      }}
                    >
                      Delete
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}
    </section>
  );
}

// The design `.grid3` project-card grid (FR-G.4): a 3-column grid that
// collapses to a single column below 900px, one bordered card per project
// (colour tile + first letter + name + description + "N chats · M files").
// Kept as a scoped <style> string because the surrounding surface styles
// inline — this is the one rule that needs a media query.
const PROJECTS_GRID_CSS = `
.projects-grid3 {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin: 0;
  padding: 0;
  list-style: none;
}
@media (max-width: 900px) {
  .projects-grid3 { grid-template-columns: 1fr; }
}
.projects-card {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--color-border);
  border-radius: 12px;
  background: var(--color-surface);
  overflow: hidden;
  min-width: 0;
}
.projects-card__open {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
  width: 100%;
  padding: 14px 14px 10px;
  border: none;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}
.projects-card__open:hover { background: var(--color-surface-elevated); }
.proj-ic {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 700;
  flex-shrink: 0;
}
.projects-card__name {
  font-size: var(--font-size-md, 14px);
  font-weight: 600;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}
.projects-card__desc {
  font-size: 12px;
  color: var(--color-text-muted);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.projects-card__meta {
  font-size: 12px;
  color: var(--color-text-subtle);
  margin-top: 2px;
}
.projects-card__role {
  align-self: flex-start;
  margin: 0 14px 4px;
  padding: 1px 8px;
  border-radius: 999px;
  border: 1px solid var(--color-border-strong);
  font-size: 11px;
  font-weight: 600;
  text-transform: capitalize;
}
.projects-card__actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  padding: 8px 14px 12px;
  border-top: 1px solid var(--color-border);
  margin-top: auto;
}
`;

// ===========================================================================
// State reducers — extracted so they remain pure + testable.
// ===========================================================================

function mergeUpdated(prev: ViewState, updated: ProjectSummary): ViewState {
  if (prev.kind !== "ready") return prev;
  const idx = prev.items.findIndex((p) => p.id === updated.id);
  if (idx === -1) {
    return { ...prev, items: [updated, ...prev.items] };
  }
  const next = prev.items.slice();
  next[idx] = updated;
  return { ...prev, items: next };
}

function removeById(prev: ViewState, id: ProjectId): ViewState {
  if (prev.kind !== "ready") return prev;
  const idx = prev.items.findIndex((p) => p.id === id);
  if (idx === -1) return prev;
  return {
    ...prev,
    items: prev.items.slice(0, idx).concat(prev.items.slice(idx + 1)),
  };
}

// ===========================================================================
// Detail-view adapters — map wire records → the chat-surface view models.
// ===========================================================================

/**
 * Best-effort owner display name. `ProjectMembership` carries no profile
 * fields in the current contract (ids + role + timestamps only), so the
 * owner's user id is surfaced until the members endpoint returns richer
 * profiles.
 */
function ownerNameFor(
  project: Project,
  members: ReadonlyArray<ProjectMembership>,
): string {
  const owner = members.find((m) => m.user_id === project.owner_user_id);
  return owner?.user_id ?? project.owner_user_id;
}

/** `ProjectMembership` (wire) → `ProjectMember` (ProjectMembersTab view). */
function toDetailMember(m: ProjectMembership): ProjectMember {
  return {
    userId: m.user_id,
    // No display-name field on membership rows yet — fall back to the id.
    displayName: m.user_id,
    role: m.role,
    joinedAt: m.added_at,
  };
}

/** `ProjectActivity` (wire) → `ProjectActivity` (ProjectActivityTab view). */
function toDetailActivity(a: ProjectActivityRecord): ProjectActivityRow {
  return {
    id: a.id,
    ref: { kind: a.ref.kind, id: a.ref.id },
    label: a.action.length > 0 ? a.action : a.preview,
    summary: a.preview.length > 0 ? a.preview : undefined,
    at: a.occurred_at,
    actorName:
      a.actor_display_name.length > 0 ? a.actor_display_name : undefined,
  };
}
