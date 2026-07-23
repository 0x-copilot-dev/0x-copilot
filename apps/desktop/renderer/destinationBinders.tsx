// Desktop destination binders (PR-6.7).
//
// The Phase-4 solo surfaces (Chats / Projects / Activity / Tools / Skills) ship
// as pure-presentation components in `@0x-copilot/chat-surface`; each takes a
// server-projected `SectionResult` plus callbacks and owns no fetch. The web
// app binds them through its `features/*Route.tsx` binders (which fetch via
// `apps/frontend`'s HTTP clients). The desktop can't import those — `apps/* →
// apps/*` is a hard boundary — so this module is the desktop-native binder: it
// fetches through the shell's `Transport` port (the IPC → facade proxy the Run
// cockpit already uses via `useTransport`) and mirrors each web binder's
// transport calls + projection, wiring callbacks to the desktop shell's
// navigation.
//
// Boundary: components + `useTransport` from `@0x-copilot/chat-surface`, wire
// types from `@0x-copilot/api-types`; no `apps/*` import. The projections here
// intentionally duplicate the web binders' pure logic rather than share it —
// the shared home for these is the package component's own contract, and the
// projections operate only on `@0x-copilot/api-types` shapes.

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
} from "react";

import {
  ActivityDestination,
  ChatsArchive,
  ConnectModal,
  ConnectorsDestination,
  ProjectDetailView,
  ProjectEditor,
  ProjectsDestination,
  RunDestination,
  SkillsDestination,
  buildRunCreateBody,
  createProviderKeysPort,
  messageFromError,
  projectActivityRows,
  toChatArchiveRow,
  useChatsArchive,
  useConnectFlow,
  useNotify,
  useTransport,
  type ConnectorAccessPort,
  type CustomServerInput,
  type ProjectDataPort,
  type ProjectDetail,
  type ProjectDetailProfile,
  type ProjectSummary,
  type RunEmptyComposerCtx,
  type RunStartRequest,
} from "@0x-copilot/chat-surface";
import type { Transport } from "@0x-copilot/chat-transport";
import type {
  ActivityRunRow,
  Connector,
  ConnectorAccessMode,
  ConnectorCatalogEntry,
  ConnectorId,
  ConnectorListResponse,
  ConnectorSlug,
  SetConnectorAccessModeResponse,
  ConversationId,
  ConversationListResponse,
  DesktopConnectorCatalogResponse,
  ChatArchiveRow,
  LibraryFile,
  LibraryListResponse,
  ProjectColorHue,
  ProjectFileRow,
  ProjectIconEmoji,
  ProjectId,
  RunHistoryResponse,
  RunId,
  SectionResult,
  Skill,
  SkillId,
  SkillListResponse,
  SkillSummary,
} from "@0x-copilot/api-types";

// AC9 — connector IPC channel names (dependency-free constants module; safe to
// bundle into the renderer). The connect flow is owned by Electron MAIN
// (loopback binding + system browser); the renderer only asks by slug.
import { CONNECTOR_CHANNELS } from "../main/connectors/channels";
// Composer parity: the desktop Run cockpit's in-chat composer (steer an active
// run) + empty-state composer (the design's "What should we run first?" surface
// — start the first run). Both share `AssistantComposer` bound to desktop
// substrate ports. Same-app imports, allowed.
import { RunComposer } from "./composer/RunComposer";
import { RunEmptyComposer } from "./composer/RunEmptyComposer";
import { createComposerConnectorsPort } from "./composer/composerConnectorsPort";
import { isSurfacesV2Enabled } from "./featureFlags";

// ---------------------------------------------------------------------------
// Shared load hook — drives the 4-state machine (loading / ok / empty / error)
// every destination consumes. `null` = first load in flight (the component
// renders its loading skeleton); a resolved `SectionResult` drives the rest.
// The `load` callback closes over the (stable) transport, so it is memoized
// per-binder and safe to depend on. `retry` bumps a token to refetch.
// ---------------------------------------------------------------------------

function useSectionLoad<T>(load: () => Promise<SectionResult<T>>): {
  readonly result: SectionResult<T> | null;
  readonly retry: () => void;
} {
  const [result, setResult] = useState<SectionResult<T> | null>(null);
  const [token, setToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setResult(null);
    load()
      .then((next) => {
        if (!cancelled) setResult(next);
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setResult({ status: "error", error: errorText(error) });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [load, token]);

  const retry = useCallback(() => setToken((t) => t + 1), []);
  return { result, retry };
}

function errorText(error: unknown): string {
  if (error instanceof Error && error.message.length > 0) return error.message;
  return "Something went wrong. Try again.";
}

// Shared nav callbacks the shell threads down from bootstrap. Each surface
// picks the subset it needs.
export interface DestinationBinderCallbacks {
  /**
   * Open the Run cockpit for an Activity row (PRD-04 Seam C). Carries the row's
   * CONVERSATION id (the cockpit's bind target) and its run id. Fired for every
   * row. A 0-arity callback that discarded the argument (the old bug) no longer
   * type-satisfies this; bootstrap calls `openConversation(target.conversationId)`.
   */
  readonly onOpenRun?: (target: {
    readonly conversationId: ConversationId;
    readonly runId: RunId;
  }) => void;
  /**
   * Start / navigate to a NEW chat on the Run cockpit front door (no id).
   * Chats' "New chat" and Skills' "Run" use it. Split out from `onOpenRun`
   * (which now carries a run id) so the two intents can't be conflated.
   */
  readonly onNewChat?: () => void;
  /**
   * Reopen a specific conversation from Chats — navigate to its Run route with
   * the real conversation id (so the cockpit resolves that conversation's
   * transcript + latest run, not a placeholder). Distinct from `onNewChat`,
   * which only lands on the cockpit front door without an id.
   */
  readonly onOpenConversation?: (id: ConversationId) => void;
  /** Open Settings → Privacy & retention (Activity's retention link). */
  readonly onOpenRetentionSettings?: () => void;
  /** Open Settings → Model & behavior (Tools' approval-policy note). */
  readonly onOpenApprovalSettings?: () => void;
}

// ===========================================================================
// Chats — the shared `useChatsArchive` controller (PRD-09 D1) owns the whole
// read/write model: three bucket-scoped cursored fetches, the SSE live tail,
// setPinned / setArchived, and loadMore(bucket). The binder collapses to
// navigation callbacks — the old local bucketConversations / loadChats /
// useSectionLoad wiring (which drifted from web and could never live-refresh,
// paginate, pin, or archive) is deleted. Per-row projection stays the shared
// `toChatArchiveRow` (PRD-03), now consumed inside the hook.
// ===========================================================================

export function ChatsBinder({
  onNewChat,
  onOpenConversation,
}: DestinationBinderCallbacks): ReactElement {
  const { archive, hasMore, onLoadMore, onTogglePin, onToggleArchive, retry } =
    useChatsArchive();
  return (
    <ChatsArchive
      archive={archive}
      // Reopen threads the row's REAL conversation id into the cockpit (the
      // Chats surface hands it to `onReopen`), so the cockpit resolves that
      // conversation's transcript + latest run instead of dropping to the
      // empty "NO ACTIVE RUN" state. New chat lands on the cockpit front door.
      onReopen={(id) => onOpenConversation?.(id)}
      onNewChat={() => onNewChat?.()}
      onRetry={retry}
      onTogglePin={onTogglePin}
      onToggleArchive={onToggleArchive}
      onLoadMore={onLoadMore}
      hasMore={hasMore}
    />
  );
}

// ===========================================================================
// Activity — GET /v1/agent/runs (PRD-05 run-history spine) → run-history feed.
// PRD-08 D1/D1c: reads the one-row-per-RUN, all-status history whose entries
// carry the meta counters, and projects it through the SHARED
// `projectActivityRows` from @0x-copilot/chat-surface (byte-identical rows +
// meta on both hosts). The legacy `/v1/agent/conversations` + `/v1/audit`
// compose — and its swallowed 401/403 (`.catch(() => [])`) — is deleted: one
// request now, and a failure surfaces as `status:"error"` + Retry.
// ===========================================================================

async function loadActivity(
  transport: Transport,
): Promise<SectionResult<ReadonlyArray<ActivityRunRow>>> {
  const history = await transport.request<RunHistoryResponse>({
    method: "GET",
    path: "/v1/agent/runs",
    query: { limit: 50 },
  });
  return {
    status: "ok",
    data: projectActivityRows(history?.runs ?? []),
  };
}

export function ActivityBinder({
  onOpenRun,
  onOpenRetentionSettings,
}: DestinationBinderCallbacks): ReactElement {
  const transport = useTransport();
  const [now, setNow] = useState(() => Date.now());
  const load = useCallback(() => {
    setNow(Date.now());
    return loadActivity(transport);
  }, [transport]);
  const { result, retry } = useSectionLoad(load);
  return (
    <ActivityDestination
      items={result}
      now={now}
      // PRD-04 Seam C — forward the row's { conversationId, runId } to the host.
      // The old `() => onOpenRun?.()` discarded the argument; the widened object
      // signature makes a 0-arity drop a type error, and the binder test asserts
      // the conversation id reaches openConversation.
      onOpenRun={onOpenRun}
      onOpenRetentionSettings={onOpenRetentionSettings}
      onRetry={retry}
    />
  );
}

// ===========================================================================
// Tools (slug `connectors`) — GET /v1/connectors → connected + catalog.
// Mirrors apps/frontend ConnectorsRoute's fetch (FR-4.20/4.25).
// ===========================================================================

type ConnectorsData = {
  readonly connectors: ReadonlyArray<Connector>;
  readonly available: ReadonlyArray<ConnectorCatalogEntry>;
};

// Project a reconciled desktop catalog row into the shared (additive) catalog
// wire shape so the Available tab renders the pinned profiles with their real
// availability / release-stage badges. The desktop catalog is the reconciled,
// installable set — it supersedes the generic web `available` list on desktop.
function desktopEntryToCatalog(
  entry: DesktopConnectorCatalogResponse["entries"][number],
): ConnectorCatalogEntry {
  return {
    slug: entry.slug as ConnectorSlug,
    display_name: entry.display_name,
    description: entry.description,
    icon_hint: entry.slug,
    display_group: entry.display_group,
    release_stage: entry.release_stage,
    availability: entry.availability,
    capabilities: entry.capabilities,
  };
}

// Best-effort fetch of the reconciled catalog through Electron main. Returns
// null when the bridge is absent (web preview / tests) so the caller falls back
// to the generic web `available` list.
async function loadDesktopCatalog(): Promise<ReadonlyArray<ConnectorCatalogEntry> | null> {
  const win = window as unknown as { bridge?: Window["bridge"] };
  if (win.bridge === undefined) return null;
  try {
    const response =
      await win.bridge.ipc.invoke<DesktopConnectorCatalogResponse>(
        CONNECTOR_CHANNELS.listCatalog,
        {},
      );
    return (response?.entries ?? []).map(desktopEntryToCatalog);
  } catch {
    return null;
  }
}

async function loadConnectors(
  transport: Transport,
): Promise<SectionResult<ConnectorsData>> {
  const [response, desktopCatalog] = await Promise.all([
    transport.request<ConnectorListResponse>({
      method: "GET",
      path: "/v1/connectors",
      query: { limit: 50 },
    }),
    loadDesktopCatalog(),
  ]);
  const connected = response?.connectors ?? [];
  const connectedSlugs = new Set(connected.map((c) => c.slug));
  // Prefer the reconciled desktop catalog when available; drop slugs already
  // connected so the Available tab only shows what can still be installed.
  const available = (desktopCatalog ?? response?.available ?? []).filter(
    (entry) => !connectedSlugs.has(entry.slug),
  );
  return {
    status: "ok",
    data: { connectors: connected, available },
  };
}

export function ConnectorsBinder({
  onOpenApprovalSettings,
}: DestinationBinderCallbacks): ReactElement {
  const transport = useTransport();
  const notify = useNotify();
  const load = useCallback(() => loadConnectors(transport), [transport]);
  const { result, retry } = useSectionLoad(load);

  // Custom-server add lives on the injected FirstRunConnectorsPort (SSOT — the
  // same `/v1/mcp/servers` create the FTUE popover uses). This retires the
  // "desktop custom-MCP add" follow-up: desktop gains custom add over the same
  // port it already ships.
  const port = useMemo(
    () => createComposerConnectorsPort(transport),
    [transport],
  );

  // Mirror the loaded connectors into a ref so the terminal Connect can resolve
  // a freshly-connected connector by slug across the modal's await boundary.
  const connectorsRef = useRef<ReadonlyArray<Connector>>([]);
  useEffect(() => {
    connectorsRef.current =
      result?.status === "ok" ? (result.data?.connectors ?? []) : [];
  }, [result]);

  // PRD-06 D4 — the access-mode writer, over the shell Transport (IPC → facade
  // PATCH). The destination owns the optimistic apply / revert / error banner;
  // the binder supplies only this one method. No token crosses the bridge.
  const accessPort = useMemo<ConnectorAccessPort>(
    () => ({
      setAccessMode: async (
        id: ConnectorId,
        mode: ConnectorAccessMode,
      ): Promise<Connector> => {
        const res = await transport.request<SetConnectorAccessModeResponse>({
          method: "PATCH",
          path: `/v1/connectors/${encodeURIComponent(id)}/access-mode`,
          body: { access_mode: mode },
        });
        return res.connector;
      },
    }),
    [transport],
  );

  // PRD-11 D4 — the connect flow is host-neutral; only "open the authorization
  // surface" is desktop-specific. The renderer is DENIED window.open, so a
  // catalog pick routes through the main-brokered, slug-scoped connect IPC
  // (loopback bind + system browser). A url-only request (a custom server's
  // OAuth) is rejected — desktop cannot open it from the renderer.
  const markConnectedRef = useRef<(slug?: ConnectorSlug) => void>(() => {});
  const authorize = useCallback(
    async (request: { slug?: ConnectorSlug; url?: string }): Promise<void> => {
      if (request.slug === undefined) {
        throw new Error(
          "This server needs a browser sign-in that isn’t available on desktop yet.",
        );
      }
      const win = window as unknown as { bridge?: Window["bridge"] };
      if (win.bridge === undefined) {
        throw new Error("Connect is unavailable in this environment.");
      }
      const slug = request.slug;
      try {
        await win.bridge.ipc.invoke(CONNECTOR_CHANNELS.connect, { slug });
      } catch (error: unknown) {
        const raw = error instanceof Error ? error.message : String(error);
        throw new Error(
          raw.includes("connector_oauth_setup_required")
            ? "This connector isn’t set up for sign-in yet."
            : messageFromError(error),
        );
      }
      // The main-brokered connect resolves on completion → refetch so the row
      // lands, and signal the flow so the modal advances to the permission step.
      retry();
      markConnectedRef.current(slug);
    },
    [retry],
  );

  // Custom-server add: create the MCP server over the port. Desktop cannot
  // complete a browser OAuth from the renderer, so the flow completes on create
  // (a non-auth server is fully registered; an OAuth server is registered and
  // its sign-in is a documented desktop follow-up).
  const addCustomServer = useCallback(
    async (input: CustomServerInput): Promise<{ authorizeUrl?: string }> => {
      await port.addCustomServer(input.url, input.oauthClient);
      retry();
      return {};
    },
    [port, retry],
  );

  const persistConnect = useCallback(
    async (
      slug: ConnectorSlug,
      permission: ConnectorAccessMode,
    ): Promise<void> => {
      const connector = connectorsRef.current.find((c) => c.slug === slug);
      if (connector === undefined) return;
      await accessPort.setAccessMode(connector.id, permission);
    },
    [accessPort],
  );

  const flow = useConnectFlow({
    authorize,
    addCustomServer,
    onConnect: persistConnect,
  });
  markConnectedRef.current = flow.markConnected;

  // Reconnect a broken connector by re-authorizing its slug through the same
  // main-brokered path.
  const handleReconnect = useCallback(
    (id: ConnectorId): void => {
      const connector = connectorsRef.current.find((c) => c.id === id);
      if (connector === undefined) return;
      void authorize({ slug: connector.slug }).catch((error: unknown) => {
        notify({
          tone: "error",
          title: `Couldn’t reconnect ${connector.display_name}`,
          body: messageFromError(error),
        });
      });
    },
    [authorize, notify],
  );

  const catalog = useMemo<ReadonlyArray<ConnectorCatalogEntry>>(
    () => (result?.status === "ok" ? (result.data?.available ?? []) : []),
    [result],
  );

  return (
    <>
      <ConnectorsDestination
        items={result}
        onConnect={flow.openConnect}
        onReconnect={handleReconnect}
        accessPort={accessPort}
        onOpenApprovalSettings={onOpenApprovalSettings}
        onRetry={retry}
      />
      <ConnectModal
        open={flow.open}
        onClose={flow.closeConnect}
        catalog={catalog}
        onSelectEntry={flow.onSelectEntry}
        onConnect={flow.onConnect}
        onAddCustomServer={flow.onAddCustomServer}
        pending={flow.pending}
        error={flow.error}
      />
    </>
  );
}

// ===========================================================================
// Skills (slug `tools`) — GET /v1/skills → skill cards.
// Mirrors apps/frontend SkillsRoute projection (FR-4.26/4.27).
// ===========================================================================

function toSkillSummary(skill: Skill): SkillSummary {
  return {
    id: skill.skill_id as SkillId,
    name: skill.display_name || skill.name,
    description: skill.description,
    // Per-skill run counts aren't projected by the backend yet (same gap the
    // web binder notes); default to 0 until they land.
    run_count: 0,
    updated_at: skill.updated_at,
  };
}

async function loadSkills(
  transport: Transport,
): Promise<SectionResult<ReadonlyArray<SkillSummary>>> {
  const response = await transport.request<SkillListResponse>({
    method: "GET",
    path: "/v1/skills",
  });
  return {
    status: "ok",
    data: (response?.skills ?? []).map(toSkillSummary),
  };
}

export function SkillsBinder({
  onNewChat,
}: DestinationBinderCallbacks): ReactElement {
  const transport = useTransport();
  const load = useCallback(() => loadSkills(transport), [transport]);
  const { result, retry } = useSectionLoad(load);
  // Run → start/open a run (honest interim: navigate to the Run cockpit, the
  // front door for a run). The skill editor route isn't built on desktop yet,
  // so Edit / New are omitted rather than faked.
  return (
    <SkillsDestination
      items={result}
      onRunSkill={() => onNewChat?.()}
      onRetry={retry}
    />
  );
}

// ===========================================================================
// Projects — GET /v1/projects → project cards.
// Mirrors apps/frontend ProjectsRoute's list fetch. Creation / mutation /
// detail flows aren't wired on desktop yet, so the grid renders read-only.
// ===========================================================================

interface ProjectListResponse {
  readonly items?: ReadonlyArray<ProjectSummary>;
  readonly next_cursor?: string | null;
}

async function loadProjects(
  transport: Transport,
): Promise<SectionResult<ReadonlyArray<ProjectSummary>>> {
  const response = await transport.request<ProjectListResponse>({
    method: "GET",
    path: "/v1/projects",
    query: { limit: 50 },
  });
  return { status: "ok", data: response?.items ?? [] };
}

// A blank editor value for the CREATE sheet (D4/D9). The design ships no create
// control, but a desktop product needs a way to make its first project (the
// deliberate live-only divergence recorded in PRD-10 D4). `ProjectEditor` is the
// create/edit sheet `onCreateProject` was always meant to open (D9 ledger); for
// create we seed a blank value whose `id` is a throwaway placeholder — the server
// mints the real id on `POST /v1/projects` and the list refetches.
function blankProjectEditorValue(): {
  readonly id: ProjectId;
  readonly name: string;
  readonly description: string;
  readonly iconEmoji: ProjectIconEmoji;
  readonly colorHue: ProjectColorHue;
  readonly defaultConnectorAllowlist: null;
} {
  return {
    id: "" as unknown as ProjectId,
    name: "",
    description: "",
    iconEmoji: "📁" as unknown as ProjectIconEmoji,
    colorHue: 210 as unknown as ProjectColorHue,
    defaultConnectorAllowlist: null,
  };
}

export function ProjectsBinder({
  onOpenConversation,
}: Pick<DestinationBinderCallbacks, "onOpenConversation"> = {}): ReactElement {
  const transport = useTransport();
  const load = useCallback(() => loadProjects(transport), [transport]);
  const { result, retry } = useSectionLoad(load);

  // Detail reachability (PRD-10 DoD 9): desktop owns the focus state + mounts the
  // shared `ProjectDetailView` through `ProjectsDestination`'s `renderDetail`
  // slot. Before this, `ProjectsBinder` passed `{ mode: "disabled" }`, so
  // clicking a card did nothing and the whole detail branch was dead code even
  // though the component + PRD-07's `ProjectDataPort` existed. The name cache is
  // primed by `ProjectsDestination` from `items`, so cross-destination project
  // links resolve to the real name.
  const [focusedProjectId, setFocusedProjectId] = useState<ProjectId | null>(
    null,
  );
  const [createOpen, setCreateOpen] = useState(false);
  const dataPort = useMemo(
    () => createDesktopProjectDataPort(transport),
    [transport],
  );

  const summaries: ReadonlyArray<ProjectSummary> =
    result !== null && result.status === "ok" ? (result.data ?? []) : [];

  const createProject = useCallback(
    async (payload: {
      readonly name: string;
      readonly description: string;
      readonly iconEmoji: ProjectIconEmoji;
      readonly colorHue: ProjectColorHue;
    }): Promise<void> => {
      await transport.request({
        method: "POST",
        path: "/v1/projects",
        body: {
          name: payload.name,
          description: payload.description,
          icon_emoji: payload.iconEmoji,
          color_hue: payload.colorHue,
        },
      });
      setCreateOpen(false);
      retry();
    },
    [transport, retry],
  );

  return (
    <>
      <ProjectsDestination
        items={result}
        detail={{
          mode: "enabled",
          focusedProjectId,
          onCloseDetail: () => setFocusedProjectId(null),
          renderDetail: ({ projectId, onClose }) => (
            <DesktopProjectDetail
              key={projectId}
              projectId={projectId}
              summary={summaries.find((p) => p.id === projectId)}
              dataPort={dataPort}
              onBack={onClose}
              onOpenConversation={onOpenConversation}
            />
          ),
        }}
        onOpenProject={setFocusedProjectId}
        onCreateProject={() => setCreateOpen(true)}
        onRetry={retry}
      />
      {createOpen ? (
        <div
          data-testid="desktop-project-create-overlay"
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 40,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: "var(--color-scrim, rgba(0,0,0,0.5))",
            padding: 24,
            boxSizing: "border-box",
          }}
        >
          <div
            style={{
              width: "100%",
              maxWidth: 560,
              maxHeight: "90vh",
              overflow: "auto",
              background: "var(--color-surface)",
              border: "1px solid var(--color-border)",
              borderRadius: 12,
            }}
          >
            <ProjectEditor
              value={blankProjectEditorValue()}
              availableConnectors={[]}
              onCancel={() => setCreateOpen(false)}
              onSave={async (payload) => {
                await createProject({
                  name: payload.name,
                  description: payload.description,
                  iconEmoji: payload.iconEmoji,
                  colorHue: payload.colorHue,
                });
              }}
            />
          </div>
        </div>
      ) : null}
    </>
  );
}

// ---------------------------------------------------------------------------
// DesktopProjectDetail — the desktop host's binding of the shared
// `ProjectDetailView` (PRD-10 DoD 9). Builds the `ProjectDetail` header model
// from the already-loaded list `ProjectSummary` (no second `GET /v1/projects/
// {id}` round-trip — the design's detail header is the same data as the card),
// and loads the project-scoped Chats + Files sections through PRD-07's
// `ProjectDataPort`. Desktop runs the `single_user_desktop` profile, so the view
// renders the v3 SOLO sections (Chats · N / Files · M), never the team tab bar.
// ---------------------------------------------------------------------------

function DesktopProjectDetail({
  projectId,
  summary,
  dataPort,
  onBack,
  onOpenConversation,
}: {
  readonly projectId: ProjectId;
  readonly summary: ProjectSummary | undefined;
  readonly dataPort: ProjectDataPort;
  readonly onBack: () => void;
  readonly onOpenConversation?: (id: ConversationId) => void;
}): ReactElement {
  const [chats, setChats] = useState<SectionResult<
    ReadonlyArray<ChatArchiveRow>
  > | null>(null);
  const [files, setFiles] = useState<SectionResult<
    ReadonlyArray<ProjectFileRow>
  > | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setChats(null);
    setFiles(null);
    void dataPort.listProjectChats(projectId).then((r) => {
      if (!cancelled) setChats(r);
    });
    void dataPort.listProjectFiles(projectId).then((r) => {
      if (!cancelled) setFiles(r);
    });
    return () => {
      cancelled = true;
    };
  }, [dataPort, projectId, reloadToken]);

  // No source loaded yet / project not in the list (a focus race): render a
  // minimal solo header so the back-link is always reachable.
  const profile: ProjectDetailProfile = "solo";
  const detail: ProjectDetail = {
    id: projectId,
    name: summary?.name ?? "Project",
    iconEmoji: summary?.icon_emoji,
    colorHue: summary?.color_hue,
    description: summary?.description,
    status: summary?.status ?? "active",
    ownerUserId: summary?.owner_user_id ?? "",
    ownerName: summary?.owner_display_name ?? summary?.owner_user_id ?? "",
    memberCount: summary?.counts.members ?? 1,
    chatCount: summary?.counts.chats ?? undefined,
    fileCount: summary?.counts.files,
  };

  return (
    <ProjectDetailView
      project={detail}
      profile={profile}
      onBack={onBack}
      members={null}
      activity={null}
      chats={chats}
      files={files}
      onRetryChats={() => setReloadToken((t) => t + 1)}
      onRetryFiles={() => setReloadToken((t) => t + 1)}
      onOpenChat={(conversationId) => onOpenConversation?.(conversationId)}
      canManage={false}
      renderCrossDestinationTab={() => null}
    />
  );
}

// ===========================================================================
// Project data — PRD-07 `ProjectDataPort` (the detail view's Chats + Files
// seam). The desktop-native implementation over the shell `Transport`, the
// twin of apps/frontend's web implementation, so the shared `ProjectDetailView`
// renders identical project-scoped chats + files on both hosts. Neither host
// invents an endpoint (Seam 3):
//
//   * chats → `GET /v1/agent/conversations?filter[project_id]=<id>
//     &include_archived=true`, mapped by PRD-03's shared per-row projector
//     `toChatArchiveRow` (so PRD-02's status chip + PRD-10's row apply for free
//     — no third row projection). The query lives in the PATH, not a `query`
//     object, so the facade's `filter[project_id]` alias survives verbatim
//     (the facade translates it to ai-backend's plain `project_id`).
//   * files → `GET /v1/library?filter[project_id]=<id>&filter[kind]=file`,
//     mapping each `LibraryFile` → `ProjectFileRow`. A project file IS a library
//     item with `project_id` set — no second `/v1/projects/{id}/files` source.
//
// Each method resolves a `SectionResult` (never throws) so the detail view's
// uniform 4-state machine (error / unavailable / empty / ready) drives itself.
// ===========================================================================

function projectChatsPath(projectId: ProjectId): string {
  const id = encodeURIComponent(projectId);
  return `/v1/agent/conversations?filter[project_id]=${id}&include_archived=true`;
}

function projectFilesPath(projectId: ProjectId): string {
  const id = encodeURIComponent(projectId);
  return `/v1/library?filter[project_id]=${id}&filter[kind]=file&limit=50`;
}

// Human-readable file size from raw bytes (display-only sub-line). `undefined`
// for missing / zero so the row omits the segment rather than showing "0 B".
export function fileSizeLabel(bytes: number): string | undefined {
  if (!Number.isFinite(bytes) || bytes <= 0) return undefined;
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const rounded =
    unit === 0 || value >= 10 ? Math.round(value) : Math.round(value * 10) / 10;
  return `${rounded} ${units[unit]}`;
}

export function toProjectFileRow(file: LibraryFile): ProjectFileRow {
  return {
    id: file.id,
    name: file.name,
    fileKind: file.file_kind,
    updatedAt: file.updated_at,
    sizeLabel: fileSizeLabel(file.size_bytes),
  };
}

export function createDesktopProjectDataPort(
  transport: Transport,
): ProjectDataPort {
  return {
    async listProjectChats(projectId: ProjectId) {
      try {
        const response = await transport.request<ConversationListResponse>({
          method: "GET",
          path: projectChatsPath(projectId),
        });
        const rows = (response?.conversations ?? [])
          .filter((conversation) => conversation.deleted_at == null)
          .map(toChatArchiveRow);
        return { status: "ok", data: rows };
      } catch (error) {
        return { status: "error", error: errorText(error) };
      }
    },
    async listProjectFiles(projectId: ProjectId) {
      try {
        const response = await transport.request<LibraryListResponse>({
          method: "GET",
          path: projectFilesPath(projectId),
        });
        const rows = (response?.items ?? [])
          .filter((item): item is LibraryFile => item.kind === "file")
          .map(toProjectFileRow);
        return { status: "ok", data: rows };
      } catch (error) {
        return { status: "error", error: errorText(error) };
      }
    },
  };
}

// A stable idempotency key for a NEW chat's first send. Uniqueness per new-chat
// intent is all the server's partial-unique conversation index needs to collapse
// a concurrent/double-tap create into a single conversation row.
function mintNewChatIdempotencyKey(): string {
  const c = globalThis.crypto;
  if (c !== undefined && typeof c.randomUUID === "function") {
    return c.randomUUID();
  }
  // Fallback (test/JS env without Web Crypto). Uniqueness within one session is
  // sufficient for the server-side idempotency collapse.
  return `new-chat-${Date.now().toString(36)}-${Math.floor(Math.random() * 1e6).toString(36)}`;
}

// The Run cockpit. Conversation identity is THREADED FROM THE NAV (Router URL →
// bootstrap → outlet), not self-resolved here. The old racy mount effect (a
// `GET conversations?limit=1`-else-`POST {title:"Desktop session"}` heuristic)
// created duplicate conversations on concurrent mounts and is gone
// (desktop-run-identity §D3). A brand-new chat carries a `null` conversationId;
// the conversation is created LAZILY on the first send via the server-authoritative
// atomic ensure-conversation-on-run path — one `POST /v1/agent/runs` that omits
// `conversation_id` and carries a stable `conversation_idempotency_key`. When the
// server returns the created id we hand it back through `onConversationCreated`;
// the host navigates, the outlet re-keys this binder by the real id, and the
// cockpit remounts + head-resolves the just-created run so it streams.
export function RunBinder({
  conversationId,
  onConversationCreated,
  onOpenModelSettings,
  onOpenLocalModelSettings,
  onOpenConnectors,
  onOpenSkills,
}: {
  /** The active conversation from the nav; `null` = a brand-new chat. */
  readonly conversationId: ConversationId | null;
  /**
   * The first send of a NEW chat created this conversation server-side — the
   * host navigates to it (the outlet then re-keys + remounts this binder).
   */
  readonly onConversationCreated?: (id: ConversationId) => void;
  /** Open Settings → Provider keys (readiness setup CTA / config-error CTA). */
  readonly onOpenModelSettings?: () => void;
  /** Open Settings → Local models (model popover's "Get local models →"). */
  readonly onOpenLocalModelSettings?: () => void;
  /** Navigate to the Tools (connectors) surface — composer connections view. */
  readonly onOpenConnectors?: () => void;
  /** Navigate to the Skills surface — composer skills settings. */
  readonly onOpenSkills?: () => void;
}): ReactElement {
  const transport = useTransport();
  // Composer chrome ports: the inline Tools popover's MCP surface (the shared
  // `/v1/mcp/*` adapter) + the model pill's inline "Add a provider key" form
  // surface. Both are stable per transport, so memoize.
  const connectorsPort = useMemo(
    () => createComposerConnectorsPort(transport),
    [transport],
  );
  const providerKeysPort = useMemo(
    () => createProviderKeysPort(transport),
    [transport],
  );
  // Idempotency key for a new chat's first send — minted once per new-chat
  // intent (below) and cleared once a conversation exists. The outlet keys this
  // binder by conversationId, so a new chat gets a fresh binder (and a fresh
  // ref); the reset effect is a belt-and-braces guard for an in-place change.
  const newChatIdempotencyKeyRef = useRef<string | null>(null);
  // Readiness gate (Issue 1): does the user have a usable model — a BYOK
  // provider key OR a running local model? Default true (fail-open) so we never
  // flash the setup CTA on load for a user who IS configured; flip to false only
  // once the probe CONFIRMS neither exists. A probe error also fails open — the
  // run-start error surfacing is the backstop that shows the actionable config
  // message if a key really is missing.
  const [modelReady, setModelReady] = useState(true);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      let hasProviderKey = false;
      try {
        const res = await transport.request<{
          readonly keys?: readonly unknown[];
        }>({ method: "GET", path: "/v1/settings/provider-keys" });
        hasProviderKey = (res.keys?.length ?? 0) > 0;
      } catch {
        // Can't tell → don't hard-block. Leave ready=true.
        return;
      }
      if (hasProviderKey) {
        if (!cancelled) setModelReady(true);
        return;
      }
      // No cloud key — a running local model with at least one pulled model
      // counts as ready. Local models are optional/server-gated, so any error
      // (disabled → 404) simply means "no local option", not "not ready".
      let hasLocalModel = false;
      try {
        const status = await transport.request<{
          readonly enabled?: boolean;
          readonly ollama_running?: boolean;
        }>({ method: "GET", path: "/v1/local-models/status" });
        if (status.enabled === true && status.ollama_running === true) {
          const list = await transport.request<{
            readonly models?: readonly unknown[];
          }>({ method: "GET", path: "/v1/local-models" });
          hasLocalModel = (list.models?.length ?? 0) > 0;
        }
      } catch {
        /* local models unavailable — not a readiness signal */
      }
      if (!cancelled) setModelReady(hasProviderKey || hasLocalModel);
    })();
    return () => {
      cancelled = true;
    };
  }, [transport]);

  // Belt-and-braces: once a conversation exists, drop any minted new-chat key.
  // (The outlet re-keys this binder on the id change so it normally remounts
  // with a fresh ref anyway; this covers an in-place prop change.)
  useEffect(() => {
    if (conversationId !== null) {
      newChatIdempotencyKeyRef.current = null;
    }
  }, [conversationId]);

  // A brand-new chat has no conversation yet, but the cockpit still needs a
  // stable id to bind its head/transcript GETs against — pass a sentinel. Those
  // GETs 404 harmlessly (head resolution is best-effort/silent) and the cockpit
  // shows its empty composer until the first send creates the real conversation.
  const boundConversationId: ConversationId =
    conversationId ?? ("new" as ConversationId);

  const handleStartRun = useCallback(
    async (request: RunStartRequest): Promise<string | null> => {
      // Existing conversation → the historical path: POST a run against it.
      // One body builder (shared with the shell default + the web binder): a
      // bare `{ goal }` stays "conversation + goal only"; the rich composer adds
      // model / attachments / web-search / connector scopes. Identity is derived
      // server-side from the verified session, never sent by the client.
      if (conversationId !== null) {
        const run = await transport.request<{ readonly run_id: string }>({
          method: "POST",
          path: "/v1/agent/runs",
          body: buildRunCreateBody(conversationId, request),
        });
        return run.run_id ?? null;
      }
      // New chat → create the conversation AND start the run in one server-side
      // transaction (ensure-conversation-on-run). Build the shared run body, then
      // drop `conversation_id` and carry a stable idempotency key so a double-tap
      // collapses to a single conversation row. Read back both ids and surface
      // the created conversation so the host navigates (→ re-key → remount).
      if (newChatIdempotencyKeyRef.current === null) {
        newChatIdempotencyKeyRef.current = mintNewChatIdempotencyKey();
      }
      const body = buildRunCreateBody(boundConversationId, request);
      delete body.conversation_id;
      body.conversation_idempotency_key = newChatIdempotencyKeyRef.current;
      const run = await transport.request<{
        readonly run_id: string;
        readonly conversation_id?: string;
      }>({
        method: "POST",
        path: "/v1/agent/runs",
        body,
      });
      const createdId = run.conversation_id;
      if (typeof createdId === "string" && createdId !== "") {
        onConversationCreated?.(createdId as ConversationId);
      }
      return run.run_id ?? null;
    },
    [transport, conversationId, boundConversationId, onConversationCreated],
  );

  // Empty-state composer (FR-3.25): the design's "What should we run first?"
  // rich composer, mounted when there is no active run. Shares the in-chat
  // composer's model/skill/tool bindings; send binds the fresh run live.
  const renderEmptyComposer = useCallback(
    (ctx: RunEmptyComposerCtx) => (
      <RunEmptyComposer
        ctx={ctx}
        onShowConnectors={onOpenConnectors}
        onOpenSkills={onOpenSkills}
        connectorsPort={connectorsPort}
        providerKeysPort={providerKeysPort}
        onGetLocalModels={onOpenLocalModelSettings}
      />
    ),
    [
      onOpenConnectors,
      onOpenSkills,
      onOpenLocalModelSettings,
      connectorsPort,
      providerKeysPort,
    ],
  );

  // Composer parity (PRD: desktop-composer-parity): mount the shared
  // AssistantComposer in the cockpit's in-chat composer slot. The cockpit hands
  // us the ghost/scrub `disabled` + placeholder; RunComposer owns the substrate
  // ports (attachments, `/`-menu, connectors, model picker) and run dispatch.
  const renderComposer = useCallback(
    (ctx: {
      readonly disabled: boolean;
      readonly placeholder: string;
      // §D3 — the cockpit injects its ONE dispatch into the composer ctx; the
      // in-chat send routes through it so it binds the live session.
      readonly dispatch: (request: RunStartRequest) => Promise<void>;
      // WC-P3 — cockpit-owned run state + cancel; the composer swaps send↔Stop.
      readonly running: boolean;
      readonly onCancel: () => void;
    }) => (
      <RunComposer
        dispatch={ctx.dispatch}
        disabled={ctx.disabled}
        placeholder={ctx.placeholder}
        running={ctx.running}
        onCancel={ctx.onCancel}
        onShowConnectors={onOpenConnectors}
        onOpenSkillsSettings={onOpenSkills}
        onOpenModelSettings={onOpenModelSettings}
        onGetLocalModels={onOpenLocalModelSettings}
        connectorsPort={connectorsPort}
        providerKeysPort={providerKeysPort}
      />
    ),
    [
      onOpenConnectors,
      onOpenSkills,
      onOpenModelSettings,
      onOpenLocalModelSettings,
      connectorsPort,
      providerKeysPort,
    ],
  );

  return (
    <RunDestination
      conversationId={boundConversationId}
      onStartRun={handleStartRun}
      modelReady={modelReady}
      onOpenModelSettings={onOpenModelSettings}
      renderComposer={renderComposer}
      renderEmptyComposer={renderEmptyComposer}
      // PRD-B1: Generative Surfaces v2 canvas — opt-in client flag (default
      // OFF), paired with the runtime SURFACES_V2 flag.
      surfacesV2={isSurfacesV2Enabled()}
      // PRD-B2: raw-fallback Copy / Download. Renderer-side (the Electron
      // renderer has the DOM); the package stays substrate-agnostic.
      onCopyText={copyTextToClipboard}
      onSaveFile={saveTextToFile}
    />
  );
}

// PRD-B2 raw-fallback host callbacks. Renderer-side so no new IPC channel is
// required; the substrate-agnostic package hands us the full serialized payload.
async function copyTextToClipboard(text: string): Promise<void> {
  if (typeof navigator === "undefined" || navigator.clipboard === undefined) {
    throw new Error("clipboard unavailable");
  }
  await navigator.clipboard.writeText(text);
}

async function saveTextToFile(text: string, filename: string): Promise<void> {
  if (typeof document === "undefined") {
    throw new Error("download unavailable: no document");
  }
  const blob = new Blob([text], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.rel = "noopener";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}
