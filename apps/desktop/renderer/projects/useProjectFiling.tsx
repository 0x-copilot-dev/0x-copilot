// Project filing — the desktop host half of "which project is this chat in".
//
// The shared surface owns the CONTROLS (`ProjectFilingChip` in the composer,
// `ThreadSwitcher`'s scope picker, the Chats row's "Move to project…") and
// nothing else: it is substrate-agnostic, so it can neither fetch the project
// list nor perform the write. Both are the host's, and before this module the
// desktop did neither — every `project_id` in this renderer was a READ FILTER
// (`filter[project_id]` on the project detail's chats/files), which is why a
// project could only ever read "0 chats". Nothing on desktop wrote the field.
//
// One module rather than two binders' worth of duplicated fetch/write, because
// the two halves have to agree:
//
//   * the OPTIONS are one list from one endpoint (`GET /v1/projects`, the same
//     `loadProjects` the Projects grid uses — moved here so there is a single
//     fetch path, not a second one with a second response type). The composer
//     chip and the Chats picker render the same projects, and the Threads scope
//     picker renders them with the chat counts the same payload already carries;
//   * the WRITE is one PATCH (`/v1/agent/conversations/{id}` with
//     `{project_id}`) wherever it is triggered from;
//   * the SCOPE is process-wide state, and that is the subtle one. The
//     destination outlet keys `RunBinder` by conversation id, so picking a
//     project in the Threads panel and then pressing "New run" REMOUNTS the
//     binder — component state would be gone by the time the new chat is
//     created, and the run would file nowhere. The scope therefore lives in a
//     module-level observable (the `workspaceDefaultsStore` pattern: publish +
//     subscribe, no provider, because producer and consumer are the same
//     unmount-remount cycle rather than two subtrees).
//
// Creation lives here too (`useProjectCreate`), which reverses an earlier call.
// The argument for leaving it out was that the create sheet belongs to the
// Projects destination and the cockpit has no navigation seam to reach it. True,
// and it produced a worse outcome than the duplication it avoided: with no
// projects yet there was nothing to file into, so the chip was hidden entirely
// and a first-run user never met filing at all — the one moment the affordance
// most needed to exist. The sheet is a portal, not a route, so no navigation
// seam is required; both binders mount the SAME flow rather than owning one
// each.

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import {
  ProjectEditor,
  ProjectFilingChip,
  useTransport,
  type ProjectColorHue,
  type ProjectEditorSavePayload,
  type ProjectFilingOption,
  type ProjectIconEmoji,
  type ProjectSummary,
  type ThreadScopeOption,
} from "@0x-copilot/chat-surface";
import type { Transport } from "@0x-copilot/chat-transport";
import type {
  ConversationId,
  ProjectId,
  SectionResult,
} from "@0x-copilot/api-types";

// ---------------------------------------------------------------------------
// The project list — ONE fetch path (moved from destinationBinders.tsx, which
// now imports it back for the Projects grid).
// ---------------------------------------------------------------------------

export interface ProjectListResponse {
  readonly items?: ReadonlyArray<ProjectSummary>;
  readonly next_cursor?: string | null;
}

export async function loadProjects(
  transport: Transport,
): Promise<SectionResult<ReadonlyArray<ProjectSummary>>> {
  const response = await transport.request<ProjectListResponse>({
    method: "GET",
    path: "/v1/projects",
    query: { limit: 50 },
  });
  return { status: "ok", data: response?.items ?? [] };
}

/**
 * `ProjectSummary` → the chip's option. `icon_emoji` is deliberately dropped:
 * the server defaults it to 📁 for every project, so the shared control renders
 * the name's monogram on the project's hue instead — see `ProjectFilingChip`.
 */
export function toProjectFilingOption(
  summary: ProjectSummary,
): ProjectFilingOption {
  return {
    id: summary.id,
    name: summary.name,
    colorHue: summary.color_hue,
  };
}

/**
 * The same project as a Threads-panel scope. The extra field is `count`, which
 * the panel shows only when the host knows it — `counts.chats` is `null`
 * whenever the facade could not fill it from ai-backend, and a fabricated `0`
 * there would read as "this project is empty" rather than "we don't know".
 */
export function toThreadScopeOption(
  summary: ProjectSummary,
): ThreadScopeOption {
  return {
    id: summary.id,
    name: summary.name,
    colorHue: summary.color_hue,
    ...(summary.counts.chats !== null ? { count: summary.counts.chats } : {}),
  };
}

export interface ProjectFilingOptions {
  /** Projects the chat can be filed under (composer chip + Chats picker). */
  readonly options: ReadonlyArray<ProjectFilingOption>;
  /** The same projects as Threads scopes (adds the per-project chat count). */
  readonly scopeOptions: ReadonlyArray<ThreadScopeOption>;
  /** Refetch — after a create, or when a caller knows the list moved. */
  readonly reload: () => void;
}

/**
 * The last list anyone loaded, so a REMOUNT renders the chip immediately.
 *
 * The Run cockpit's binder is keyed by conversation id, so it remounts on every
 * chat switch — without this, each switch would open on a composer with no
 * filing zone and then grow one when the fetch landed, shifting the composer
 * under the user's cursor. `null` = nobody has read the endpoint yet, which is
 * deliberately distinct from a read that returned no projects.
 */
let cachedProjects: ReadonlyArray<ProjectSummary> | null = null;

/**
 * Load the projects a chat can be filed under.
 *
 * A failure leaves whatever was last known (usually nothing), never an error
 * state: filing is chrome on a surface whose job is something else, and a binder
 * that cannot list projects must render the composer it always rendered rather
 * than an error where a pill belongs. Callers gate the control on
 * `options.length > 0` for the same reason — a picker with nothing to pick is
 * worse than no picker.
 */
export function useProjectFilingOptions(): ProjectFilingOptions {
  const transport = useTransport();
  const [projects, setProjects] = useState<ReadonlyArray<ProjectSummary>>(
    () => cachedProjects ?? [],
  );
  const [token, setToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    void loadProjects(transport)
      .then((result) => {
        if (result.status !== "ok") return;
        const items = result.data ?? [];
        cachedProjects = items;
        if (!cancelled) setProjects(items);
      })
      .catch(() => {
        // Keep what we have. A transient list failure that emptied the picker
        // would read as "you have no projects" — a lie the user would answer by
        // creating a duplicate one.
      });
    return () => {
      cancelled = true;
    };
  }, [transport, token]);

  const options = useMemo(
    () => projects.map(toProjectFilingOption),
    [projects],
  );
  const scopeOptions = useMemo(
    () => projects.map(toThreadScopeOption),
    [projects],
  );
  const reload = useCallback(() => setToken((t) => t + 1), []);

  return { options, scopeOptions, reload };
}

// ---------------------------------------------------------------------------
// The writes
// ---------------------------------------------------------------------------

/**
 * File (or unfile, with `null`) an EXISTING conversation.
 *
 * RFC 7396 merge-patch: the facade forwards the body verbatim and ai-backend's
 * `UpdateConversationRequest` reads `project_id` by presence, so `null` means
 * "unfile" and an omitted key would mean "leave alone" — which is why the field
 * is always sent, never conditionally spread.
 *
 * Rejects on failure. Callers own the user-visible consequence (revert the
 * optimistic value, tell the user); this function does not swallow.
 */
export async function fileConversationUnderProject(
  transport: Transport,
  conversationId: ConversationId,
  projectId: ProjectId | null,
): Promise<void> {
  await transport.request<unknown>({
    method: "PATCH",
    path: `/v1/agent/conversations/${encodeURIComponent(conversationId)}`,
    body: { project_id: projectId },
  });
}

/**
 * Create a conversation ALREADY filed under a project, for the flow the whole
 * design is built around: a fresh chat started inside a project.
 *
 * The normal desktop new-chat path never creates a conversation from the client
 * — it posts one run with a `conversation_idempotency_key` and lets the server
 * ensure the conversation (desktop-run-identity §D3). That path cannot carry a
 * project: neither `CreateRunRequest` nor the facade's run body has the field,
 * and the server's ensure-conversation helper builds its `CreateConversationRequest`
 * from the run payload alone. So a PENDING filing — and only a pending filing —
 * makes the conversation exist first, with `project_id` on the create.
 *
 * The caller passes its already-minted new-chat idempotency key, so this create
 * keeps the double-tap guarantee the run path had: two concurrent first sends
 * resolve to ONE conversation row.
 *
 * Returns `null` when the response carried no id — the caller then falls back
 * to the ordinary ensure-on-run path rather than sending a run with no target.
 */
export async function createFiledConversation(
  transport: Transport,
  input: {
    readonly projectId: ProjectId;
    readonly idempotencyKey: string;
  },
): Promise<ConversationId | null> {
  const created = await transport.request<{
    readonly conversation_id?: string;
  }>({
    method: "POST",
    path: "/v1/agent/conversations",
    body: {
      project_id: input.projectId,
      idempotency_key: input.idempotencyKey,
    },
  });
  const id = created?.conversation_id;
  return typeof id === "string" && id !== "" ? (id as ConversationId) : null;
}

/**
 * Read the project an existing conversation is filed under.
 *
 * A dedicated `GET`, because nothing else on this screen knows: the Run cockpit
 * resolves runs, messages and events, never the conversation row itself, so the
 * chip would otherwise open on "No project" for a chat that IS filed — a
 * control that lies about the state it is there to show, and whose next click
 * would silently unfile the chat.
 *
 * Resolves `null` on failure: unknown filing reads as unfiled, which is the
 * same thing the pre-filing composer showed.
 */
export async function readConversationProject(
  transport: Transport,
  conversationId: ConversationId,
): Promise<ProjectId | null> {
  try {
    const conversation = await transport.request<{
      readonly project_id?: string | null;
    }>({
      method: "GET",
      path: `/v1/agent/conversations/${encodeURIComponent(conversationId)}`,
    });
    const id = conversation?.project_id;
    return typeof id === "string" && id !== "" ? (id as ProjectId) : null;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Thread scope — process-wide, because the binder that reads it is remounted
// by the very action that consumes it ("New run").
// ---------------------------------------------------------------------------

type ScopeListener = (scope: ProjectId | null) => void;

const scopeListeners = new Set<ScopeListener>();

/** `null` = All threads. Not persisted: a scope is a session posture, and a
 *  relaunch that silently filed new chats into a project chosen days ago would
 *  be a worse surprise than starting from All. */
let threadScope: ProjectId | null = null;

export function currentThreadScope(): ProjectId | null {
  return threadScope;
}

export function setThreadScope(next: ProjectId | null): void {
  if (next === threadScope) return;
  threadScope = next;
  // Copy before iterating: a listener may unsubscribe during dispatch (a
  // component unmounting in response to the very change being announced), and
  // mutating the live Set mid-iteration would silently skip its neighbour.
  for (const listener of [...scopeListeners]) {
    try {
      listener(next);
    } catch {
      // One bad subscriber must not deny the rest their update.
    }
  }
}

export function subscribeThreadScope(listener: ScopeListener): () => void {
  scopeListeners.add(listener);
  return () => {
    scopeListeners.delete(listener);
  };
}

/** Test seam: drop ALL module state (scope + the project cache) so one test
 *  cannot leak into the next. */
export function resetProjectFilingState(): void {
  scopeListeners.clear();
  threadScope = null;
  cachedProjects = null;
}

/**
 * Subscribe to the active thread scope. Returns the current value and the
 * setter — the setter is the module's, not a local `useState`, so every mounted
 * reader (and the next mount of a remounted one) sees the same scope.
 */
export function useThreadScope(): readonly [
  ProjectId | null,
  (next: ProjectId | null) => void,
] {
  const [scope, setScope] = useState<ProjectId | null>(currentThreadScope);
  useEffect(() => {
    // Re-read after subscribing, not only at mount: the seed above is computed
    // during render, and a change published between that render and this effect
    // would otherwise be missed for the lifetime of the component.
    setScope(currentThreadScope());
    return subscribeThreadScope(setScope);
  }, []);
  return [scope, setThreadScope] as const;
}

// ---------------------------------------------------------------------------
// ProjectFilingSheet — the Chats row's "Move to project…" picker
// ---------------------------------------------------------------------------
//
// `ChatsArchive`'s ⋯ emits an INTENT (a conversation id) and names no project,
// because choosing one needs the list the pure-presentation surface cannot
// fetch. This is the host's answer to that intent.
//
// It mounts `ProjectFilingChip` — the SAME control the composer files with —
// rather than a second list of project rows. One menu, one monogram rule, one
// "No project" row: a hand-rolled picker here is how the same choice ends up
// looking like two different features on two surfaces. The cost is one extra
// click (open the sheet, then open the pill), which is the honest price of not
// duplicating the menu; the pill also carries information a bare list would not
// — where the chat is filed TODAY.
//
// The chip's own inline popover is used (no `renderMenu`): it opens upward with
// a list capped at `.ui-pop__list`'s 264px, and the sheet below sets
// `overflow: visible` so a taller-than-the-sheet menu is never clipped.

const sheetScrimStyle: CSSProperties = {
  position: "fixed",
  inset: 0,
  zIndex: 40,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  background: "var(--color-scrim, rgba(0,0,0,0.5))",
  padding: 24,
  boxSizing: "border-box",
};

const sheetStyle: CSSProperties = {
  width: "100%",
  maxWidth: 380,
  // `overflow: visible`, deliberately, where the sibling create sheet uses
  // `auto`: the chip's menu is absolutely positioned against the pill and opens
  // UPWARD, so a scroll box here would clip the very list this sheet exists to
  // show. Nothing inside can grow unbounded — the list caps itself at 264px.
  overflow: "visible",
  display: "flex",
  flexDirection: "column",
  gap: 12,
  padding: 16,
  background: "var(--color-surface)",
  border: "1px solid var(--color-border)",
  borderRadius: 12,
};

const sheetTitleStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  color: "var(--color-text)",
};

const sheetCancelStyle: CSSProperties = {
  alignSelf: "flex-end",
  background: "transparent",
  border: "1px solid var(--color-border)",
  borderRadius: 8,
  padding: "6px 14px",
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-subtle)",
  cursor: "pointer",
  fontFamily: "inherit",
};

export interface ProjectFilingSheetProps {
  /** What the chat is filed under today — seeds the pill and the checkmark. */
  readonly value: ProjectId | null;
  readonly options: ReadonlyArray<ProjectFilingOption>;
  /** Fires with the picked project, or `null` for "No project". */
  readonly onPick: (next: ProjectId | null) => void;
  readonly onCancel: () => void;
}

export function ProjectFilingSheet({
  value,
  options,
  onPick,
  onCancel,
}: ProjectFilingSheetProps): ReactElement {
  return (
    <div
      data-testid="desktop-project-filing-sheet"
      style={sheetScrimStyle}
      // Dismiss on the scrim only — a click that started inside the sheet must
      // not close it just because it landed on the padding.
      onClick={(event) => {
        if (event.target === event.currentTarget) onCancel();
      }}
    >
      <div style={sheetStyle}>
        <div style={sheetTitleStyle}>Move to project</div>
        <ProjectFilingChip value={value} options={options} onChange={onPick} />
        <button type="button" style={sheetCancelStyle} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Creation — one flow, mounted by whichever binder needs it.
// ---------------------------------------------------------------------------

/**
 * A blank editor value for the CREATE sheet. `id` is a throwaway placeholder:
 * the server mints the real one on `POST /v1/projects`, and the editor only
 * reads `id` for its own keying.
 */
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

export interface ProjectCreateFlow {
  /** Open the create sheet. Wire to the chip's "New project…" row. */
  readonly openCreate: () => void;
  /** Mount this next to your surface. `null` while closed. */
  readonly sheet: ReactElement | null;
}

export interface UseProjectCreateOptions {
  /**
   * The new project's id, as soon as the server mints it. The composer uses
   * this to file the chat into the project it just created — without it,
   * "New project…" would create one and leave the chat unfiled, which is never
   * what the click meant.
   */
  readonly onCreated?: (id: ProjectId) => void;
  /** Refresh the caller's option list so the new project appears at once. */
  readonly reload?: () => void;
}

export function useProjectCreate(
  options: UseProjectCreateOptions = {},
): ProjectCreateFlow {
  const { onCreated, reload } = options;
  const transport = useTransport();
  const [open, setOpen] = useState(false);

  const openCreate = useCallback((): void => setOpen(true), []);

  const save = useCallback(
    async (payload: ProjectEditorSavePayload): Promise<void> => {
      const created = await transport.request<{ readonly id?: string }>({
        method: "POST",
        path: "/v1/projects",
        body: {
          name: payload.name,
          description: payload.description,
          icon_emoji: payload.iconEmoji,
          color_hue: payload.colorHue,
        },
      });
      setOpen(false);
      reload?.();
      const id = created?.id;
      if (typeof id === "string" && id.length > 0) {
        onCreated?.(id as ProjectId);
      }
    },
    [transport, reload, onCreated],
  );

  const sheet = open ? (
    <div
      data-testid="desktop-project-create-sheet"
      style={sheetScrimStyle}
      onClick={(event) => {
        if (event.target === event.currentTarget) setOpen(false);
      }}
    >
      <div style={{ width: "min(560px, 100%)" }}>
        <ProjectEditor
          mode="create"
          value={blankProjectEditorValue()}
          availableConnectors={[]}
          onSave={save}
          onCancel={() => setOpen(false)}
        />
      </div>
    </div>
  ) : null;

  return { openCreate, sheet };
}
