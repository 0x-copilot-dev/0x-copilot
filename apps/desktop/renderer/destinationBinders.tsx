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
  ConnectorsDestination,
  ProjectsDestination,
  RunDestination,
  SkillsDestination,
  buildRunCreateBody,
  createProviderKeysPort,
  messageFromError,
  useNotify,
  useTransport,
  type ConnectorsFilterSlug,
  type ProjectSummary,
  type RunEmptyComposerCtx,
  type RunStartRequest,
} from "@0x-copilot/chat-surface";
import type { Transport } from "@0x-copilot/chat-transport";
import type {
  ActivityRunRow,
  AgentRunStatus,
  AuditEvent,
  ChatArchiveRow,
  ChatArchiveStatus,
  ChatsArchive as ChatsArchiveData,
  Connector,
  ConnectorCatalogEntry,
  ConnectorListResponse,
  ConnectorSlug,
  Conversation,
  ConversationId,
  ConversationListResponse,
  DesktopConnectorCatalogResponse,
  ListAuditEventsResponse,
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
  /** Switch the active destination to Run (open-run / run-skill / new chat). */
  readonly onOpenRun?: () => void;
  /**
   * Reopen a specific conversation from Chats — navigate to its Run route with
   * the real conversation id (so the cockpit resolves that conversation's
   * transcript + latest run, not a placeholder). Distinct from `onOpenRun`,
   * which only lands on the cockpit front door without an id.
   */
  readonly onOpenConversation?: (id: ConversationId) => void;
  /** Open Settings → Privacy & retention (Activity's retention link). */
  readonly onOpenRetentionSettings?: () => void;
  /** Open Settings → Model & behavior (Tools' approval-policy note). */
  readonly onOpenApprovalSettings?: () => void;
}

// ===========================================================================
// Chats — GET /v1/agent/conversations (incl. archived) → bucketed archive.
// Mirrors apps/frontend chatsApi.bucketConversations (FR-4.5/4.9).
// ===========================================================================

function chatStatus(conversation: Conversation): ChatArchiveStatus {
  if (conversation.status === "archived" || conversation.archived_at != null) {
    return "archived";
  }
  switch (conversation.latest_run_status) {
    case "running":
    case "queued":
    case "cancelling":
      return "running";
    case "waiting_for_approval":
      return "paused";
    default:
      return "done";
  }
}

function metaString(conversation: Conversation, key: string): string {
  const metadata = conversation.metadata as Record<string, unknown> | undefined;
  const value = metadata?.[key];
  return typeof value === "string" ? value : "";
}

function toArchiveRow(conversation: Conversation): ChatArchiveRow {
  const title = conversation.title?.trim();
  const metadata = conversation.metadata as
    | { readonly pinned?: unknown }
    | undefined;
  return {
    id: conversation.conversation_id as ConversationId,
    title: title !== undefined && title.length > 0 ? title : "New chat",
    status: chatStatus(conversation),
    preview: metaString(conversation, "preview"),
    model: metaString(conversation, "model"),
    updated_at: conversation.updated_at,
    pinned: metadata?.pinned === true,
  };
}

export function bucketConversations(
  conversations: ReadonlyArray<Conversation>,
): ChatsArchiveData {
  const pinned: ChatArchiveRow[] = [];
  const recent: ChatArchiveRow[] = [];
  const archived: ChatArchiveRow[] = [];
  for (const conversation of conversations) {
    if (conversation.deleted_at != null) continue; // tombstone — never shown
    const row = toArchiveRow(conversation);
    if (row.status === "archived") archived.push(row);
    else if (row.pinned) pinned.push(row);
    else recent.push(row);
  }
  return { pinned, recent, archived };
}

async function loadChats(
  transport: Transport,
): Promise<SectionResult<ChatsArchiveData>> {
  const response = await transport.request<ConversationListResponse>({
    method: "GET",
    path: "/v1/agent/conversations",
    query: { limit: 100, include_archived: true },
  });
  return {
    status: "ok",
    data: bucketConversations(response?.conversations ?? []),
  };
}

export function ChatsBinder({
  onOpenRun,
  onOpenConversation,
}: DestinationBinderCallbacks): ReactElement {
  const transport = useTransport();
  const load = useCallback(() => loadChats(transport), [transport]);
  const { result, retry } = useSectionLoad(load);
  return (
    <ChatsArchive
      archive={result}
      // Reopen threads the row's REAL conversation id into the cockpit (the
      // Chats surface hands it to `onReopen`), so the cockpit resolves that
      // conversation's transcript + latest run instead of dropping to the
      // empty "NO ACTIVE RUN" state. New chat lands on the cockpit front door.
      onReopen={(id) => onOpenConversation?.(id)}
      onNewChat={() => onOpenRun?.()}
      onRetry={retry}
    />
  );
}

// ===========================================================================
// Activity — compose /v1/agent/conversations + /v1/audit → run-history feed.
// Mirrors apps/frontend activityApi (FR-4.15/4.19). Audit is meta-only and
// degrades to conversations-without-meta on failure.
// ===========================================================================

function mapRunStatus(
  status: AgentRunStatus,
): "running" | "done" | "paused" | "stopped" | "needs_input" {
  switch (status) {
    case "running":
    case "queued":
    case "cancelling":
      return "running";
    case "waiting_for_approval":
      return "needs_input";
    case "completed":
      return "done";
    default:
      // cancelled / failed / timed_out → terminal without clean completion.
      return "stopped";
  }
}

function auditLabel(row: AuditEvent): string | null {
  const meta: Record<string, unknown> = row.metadata;
  const candidates = [
    meta.connector_id,
    meta.server_id,
    meta.display_name,
    meta.tool_name,
  ];
  for (const candidate of candidates) {
    if (typeof candidate === "string" && candidate.trim().length > 0) {
      return candidate.trim();
    }
  }
  return null;
}

function buildMetaIndex(
  auditRows: readonly AuditEvent[],
): Map<string, Set<string>> {
  const index = new Map<string, Set<string>>();
  for (const row of auditRows) {
    const label = auditLabel(row);
    if (label === null) continue;
    const key = row.resource_id;
    if (typeof key !== "string" || key.length === 0) continue;
    const set = index.get(key) ?? new Set<string>();
    set.add(label);
    index.set(key, set);
  }
  return index;
}

function projectActivityRows(
  conversations: readonly Conversation[],
  auditRows: readonly AuditEvent[],
): ReadonlyArray<ActivityRunRow> {
  const metaIndex = buildMetaIndex(auditRows);
  const rows: ActivityRunRow[] = [];
  for (const conversation of conversations) {
    const runId = conversation.latest_run_id;
    const status = conversation.latest_run_status;
    if (
      runId === null ||
      runId === undefined ||
      runId === "" ||
      status === null ||
      status === undefined
    ) {
      continue; // never-ran conversation is a chat, not a run
    }
    const labels = new Set<string>();
    for (const label of metaIndex.get(runId) ?? []) labels.add(label);
    for (const label of metaIndex.get(conversation.conversation_id) ?? [])
      labels.add(label);
    const title = conversation.title?.trim();
    rows.push({
      run_id: runId as RunId,
      title: title !== undefined && title.length > 0 ? title : "Untitled run",
      status: mapRunStatus(status),
      meta: [...labels].sort((a, b) => a.localeCompare(b)).join(" · "),
      started_at: conversation.updated_at,
    });
  }
  rows.sort((a, b) => Date.parse(b.started_at) - Date.parse(a.started_at));
  return rows;
}

async function loadActivity(
  transport: Transport,
): Promise<SectionResult<ReadonlyArray<ActivityRunRow>>> {
  const [conversationList, auditRows] = await Promise.all([
    transport.request<ConversationListResponse>({
      method: "GET",
      path: "/v1/agent/conversations",
      query: { limit: 50, include_archived: true },
    }),
    // Audit is enrichment-only; a failed read degrades to conversations-only.
    transport
      .request<ListAuditEventsResponse>({
        method: "GET",
        path: "/v1/audit",
        query: { limit: 200 },
      })
      .then((response) => response?.rows ?? [])
      .catch(() => [] as AuditEvent[]),
  ]);
  return {
    status: "ok",
    data: projectActivityRows(conversationList?.conversations ?? [], auditRows),
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
      onOpenRun={() => onOpenRun?.()}
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
  const [filter, setFilter] = useState<ConnectorsFilterSlug>("connected");

  // The connect flow is owned by Electron MAIN: the renderer hands main a
  // stable slug and main binds the loopback + opens the system browser. On
  // success we refetch so the newly-connected row appears. No token ever
  // crosses the bridge — the invoke resolves with safe connection metadata.
  const connect = useCallback(
    (slug: ConnectorSlug): void => {
      const win = window as unknown as { bridge?: Window["bridge"] };
      if (win.bridge === undefined) return;
      win.bridge.ipc
        .invoke(CONNECTOR_CHANNELS.connect, { slug })
        .then(() => {
          setFilter("connected");
          retry();
        })
        .catch((error: unknown) => {
          // Surface the failure instead of silently leaving the row in Available.
          const raw = error instanceof Error ? error.message : String(error);
          const body = raw.includes("connector_oauth_setup_required")
            ? "This connector isn’t set up for sign-in yet."
            : messageFromError(error);
          notify({ tone: "error", title: `Couldn’t connect ${slug}`, body });
        });
    },
    [notify, retry],
  );

  return (
    <ConnectorsDestination
      items={result}
      filter={filter}
      onFilterChange={setFilter}
      onConnect={() => setFilter("available")}
      onOpenCatalogEntry={connect}
      onOpenApprovalSettings={onOpenApprovalSettings}
      onRetry={retry}
    />
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
  onOpenRun,
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
      onRunSkill={() => onOpenRun?.()}
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

export function ProjectsBinder(): ReactElement {
  const transport = useTransport();
  const load = useCallback(() => loadProjects(transport), [transport]);
  const { result, retry } = useSectionLoad(load);
  return <ProjectsDestination items={result} onRetry={retry} />;
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
    />
  );
}
