import "@0x-copilot/design-system/styles.css";
// Composer parity: the shared AssistantComposer emits `aui-*` classes whose
// styles ship with the component here (previously stranded in the web app's
// private styles.css). Without this the desktop Run composer renders unstyled.
import "@0x-copilot/chat-surface/src/composer/composer.css";
// Workspace-rail chrome (tab strip + connectors trigger) — same stranded-CSS
// fix as composer.css: without it the [Chat · Sources · Agents · Approvals]
// tabs render as native gray buttons with no active state.
import "@0x-copilot/chat-surface/src/workspace/workspace.css";
// First-run (FTUE) gate surface — the shared `fr-*` classes (top bar, gate
// cards, inline key form, footer). Same stranded-CSS fix as composer.css:
// without it the onboarding gate renders unstyled.
import "@0x-copilot/chat-surface/src/onboarding/onboarding.css";
// Assistant/reasoning markdown prose — code-block card + language header + copy
// actions + scroll/max-height, blockquote accent bar, inline-code chip, links,
// headings, lists, hr, tables. Same stranded-CSS fix as composer.css: without
// it desktop renders raw <pre>/<code> with no card, no chip, and code bleeds
// off-screen. Shared single source of truth with the web app (App.tsx).
import "@0x-copilot/chat-surface/src/messages/markdown.css";
import "./desktop.css";

import {
  StrictMode,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactElement,
} from "react";
import { createRoot } from "react-dom/client";

import {
  ChatShell,
  DeploymentProfileProvider,
  DocumentPresenceSignal,
  HashRouter,
  LocalStorageKeyValueStore,
  NotificationCenterProvider,
  ToastStack,
  defaultDestinationForProfile,
  destinationsForProfile,
  createTier2WorkerFactory,
  registerGenericStructuredDiff,
  useAppearanceSettings,
  useShellShortcuts,
  type ArtifactRoute,
  type ConversationId,
  type DeploymentProfile,
  type SettingsSectionSlug,
  type ShellDestinationSlug,
  type ShellShortcutCallbacks,
} from "@0x-copilot/chat-surface";
import { IpcTransport, type RendererSession } from "@0x-copilot/chat-transport";
import { registerAll as registerSurfaceRenderers } from "@0x-copilot/surface-renderers";

import { applyAppearance } from "./appearance";
import { BootGate } from "./BootProgress";
import { DestinationOutlet } from "./DestinationOutlet";
import { registerDesktopItemRoutes } from "./itemRoutes";
import { buildDesktopShellBinding } from "./shellBinding";
import { FirstRunGate, FirstRunSurfaceMount } from "./FirstRunGate";
import { PaletteHost } from "./PaletteHost";
import { SettingsMount } from "./SettingsMount";
import { DEFAULT_WORKSPACE_ID, SignInGate } from "./SignInGate";
import { Tier2Bridge } from "./Tier2Bridge";

import "../preload/window-bridge-types";

registerGenericStructuredDiff();
registerSurfaceRenderers();
// PRD-04 Seam B — register the desktop cross-destination route table into the
// shared <ItemLink> registry at renderer boot (the only place desktop routes
// are registered; chat-surface registers none on import).
registerDesktopItemRoutes();

// Phase 6C tier-2 lifecycle: listen for install/uninstall/mark-broken
// pushes from main and forward live boundary errors back. The bridge is
// idempotent — re-mounts under StrictMode receive the same handlers.
let tier2BridgeAttached = false;
function attachTier2BridgeOnce(): void {
  if (tier2BridgeAttached) return;
  if (typeof window === "undefined") return;
  const win = window as unknown as { bridge?: unknown };
  if (!win.bridge) return;
  // PRD-10: construct WITH the production worker factory. Before this, the
  // bridge was built without one, so every tier-2 render hit Tier2Loader's
  // `defaultWorkerFactory` and boundary-errored to tier-3.
  new Tier2Bridge({
    bridge: window.bridge,
    workerFactory: createTier2WorkerFactory(),
  }).attach();
  tier2BridgeAttached = true;
}
attachTier2BridgeOnce();

const DESKTOP_CAPABILITIES = {
  substrate: "desktop-webview" as const,
  nativeSecretStorage: true,
  fileSystemAccess: false,
  clipboardWrite: false,
  openExternal: false,
};

// The deployment profile the desktop build ships with. Desktop is always the
// solo single-user product; `team` only ever renders on a hosted/web
// deployment. `ENTERPRISE_DEPLOYMENT_PROFILE` lives on the main process
// (service-env.ts) as env for the spawned Python services and is NOT bridged to
// the renderer — so Phase 2 seeds the profile provider with this static default
// (PRD FR-2.24). The value flows through the `DeploymentProfile` port, so when a
// `team` desktop build eventually needs a real value a preload bridge can
// supply it here without touching chat-surface.
const DESKTOP_DEPLOYMENT_PROFILE: DeploymentProfile = "single_user_desktop";

// The active conversation the Run cockpit binds to is DURABLE IDENTITY carried
// in the Router URL (desktop-run-identity §D1/FR-3). Both the canonical
// `conversation` route and the deprecated `chat` alias address a conversation;
// every other route kind (or an empty hash) means "no bound conversation" → a
// brand-new chat. `null` (not a placeholder id) is the honest empty binding.
function conversationIdFromRoute(
  route: ArtifactRoute | null,
): ConversationId | null {
  if (
    route !== null &&
    (route.kind === "conversation" || route.kind === "chat")
  ) {
    return route.conversationId as ConversationId;
  }
  return null;
}

export function App(): ReactElement {
  const router = useMemo(() => new HashRouter(), []);
  const keyValueStore = useMemo(() => new LocalStorageKeyValueStore(), []);
  const presenceSignal = useMemo(() => new DocumentPresenceSignal(), []);
  return (
    <NotificationCenterProvider>
      <DeploymentProfileProvider profile={DESKTOP_DEPLOYMENT_PROFILE}>
        <BootGate bridge={window.bridge}>
          <SignInGate bridge={window.bridge} workspaceId={DEFAULT_WORKSPACE_ID}>
            {(session, signOut) => (
              // First-run gate: a returning user (flag set) drops straight
              // through to the shell; a first-time user sees onboarding until
              // they finish or skip. P1 mounts the shared 3-state
              // FirstRunSurface (gate → composer → ack) via `renderFirstRun`;
              // the binder wires the BYOK / models ports to the facade.
              <FirstRunGate
                bridge={window.bridge}
                workspaceId={session.workspaceId}
                // The shell binds its active conversation from this router; the
                // gate navigates it to the created conversation at handoff so
                // the first run opens bound, not on an empty standby composer.
                router={router}
                renderFirstRun={(onComplete) => (
                  <FirstRunSurfaceMount
                    workspaceId={session.workspaceId}
                    onComplete={onComplete}
                  />
                )}
              >
                <ChatShellForSession
                  session={session}
                  onSignOut={signOut}
                  router={router}
                  keyValueStore={keyValueStore}
                  presenceSignal={presenceSignal}
                />
              </FirstRunGate>
            )}
          </SignInGate>
        </BootGate>
      </DeploymentProfileProvider>
      {/* One toast surface for the whole app; floats above full-bleed surfaces. */}
      <ToastStack />
    </NotificationCenterProvider>
  );
}

interface ChatShellForSessionProps {
  readonly session: RendererSession;
  /** Clears the persisted session and returns to the sign-in gate. */
  readonly onSignOut: () => void;
  readonly router: HashRouter;
  readonly keyValueStore: LocalStorageKeyValueStore;
  readonly presenceSignal: DocumentPresenceSignal;
}

function ChatShellForSession(props: ChatShellForSessionProps): ReactElement {
  const transport = useMemo(
    () =>
      new IpcTransport({
        bridge: window.bridge,
        bootstrapSession: { bearer: null },
        bootstrapCapabilities: DESKTOP_CAPABILITIES,
      }),
    // The Transport contract's session is bearer-shaped. The actual bearer
    // is attached in main on every outbound HTTP request (PRD §6.7 / D24).
    // The renderer holds an opaque "session for workspace X" handle only.
    [props.session.workspaceId],
  );
  // The shell never derives the destination itself — the host owns the
  // slug ↔ route mapping (see ChatShellProps). The web host (App.tsx)
  // maps rail clicks onto its route type; the desktop has no route type
  // yet, so the minimal correct wiring is controlled local state. The solo
  // profile lands on Run — the flagship cockpit is the front door, not an
  // archive list (PRD US-2.3 / FR-2.21).
  const [activeDestination, setActiveDestination] =
    useState<ShellDestinationSlug>(() =>
      defaultDestinationForProfile(DESKTOP_DEPLOYMENT_PROFILE),
    );
  // Settings is not a rail destination — it opens from the rail foot and owns
  // full height (ChatShell suppresses the topbar/context/right-rail while it's
  // active). Navigating to any destination closes it.
  const [settingsActive, setSettingsActive] = useState(false);
  // PR-6.4: the Settings section the surface is focused on. `null` = the profile
  // default. The ⌘K palette can deep-link a section (FR-6.6/6.8); the surface
  // stays mounted (no remount) and switches in place. `onSectionChange` reflects
  // the user's in-surface tab clicks back here.
  const [settingsSection, setSettingsSection] =
    useState<SettingsSectionSlug | null>(null);
  // PR-6.6: the ⌘K command palette open state is lifted here so ⌘K flows through
  // a SINGLE listener (bootstrap's `useShellShortcuts`, FR-6.14). PaletteHost is
  // now controlled (`open`/`onOpenChange`) and no longer mounts its own
  // `useCommandPaletteHotkey` — exactly one ⌘K listener remains.
  const [paletteOpen, setPaletteOpen] = useState(false);
  // desktop-run-identity Phase 5b (FR-3/FR-4): the active conversation the Run
  // cockpit binds to, seeded from the Router URL (deep-link / relaunch) and kept
  // in sync with it below. `null` = a brand-new chat (no conversation yet — the
  // cockpit shows its empty composer; the first send creates one). This is the
  // ONLY conversation-identity state; there is no mount-time self-create.
  const [activeConversationId, setActiveConversationId] =
    useState<ConversationId | null>(() =>
      conversationIdFromRoute(props.router.current()),
    );

  const destinations = useMemo(
    () => destinationsForProfile(DESKTOP_DEPLOYMENT_PROFILE),
    [],
  );

  // PRD-12 D9 (README G7) — boot-load + persist appearance at the RENDERER ROOT,
  // not inside Settings, so `:root[data-theme|accent|density]` is correct on
  // EVERY screen at launch (desktop mounts no design-system ThemeProvider, so
  // these attributes are the only theming mechanism). The controller reads
  // `GET /v1/me/preferences` on mount and paints via `applyAppearance`; Settings
  // becomes a pass-through over `value`/`change`. This is what makes PRD-01's
  // nine accents survive a relaunch on the primary substrate.
  const appearance = useAppearanceSettings({
    transport,
    keyValueStore: props.keyValueStore,
    onApply: applyAppearance,
  });

  // Keep the bound conversation in sync with the Router URL — a `conversation`/
  // `chat` route (deep-link, back/forward, or our own `openConversation`
  // navigate) binds that conversation and lands the shell on Run.
  useEffect(() => {
    const unsubscribe = props.router.subscribe((route) => {
      if (
        route !== null &&
        (route.kind === "conversation" || route.kind === "chat")
      ) {
        setActiveConversationId(route.conversationId as ConversationId);
        setActiveDestination("run");
        setSettingsActive(false);
      }
    });
    return unsubscribe;
  }, [props.router]);

  const handleNavigate = (slug: ShellDestinationSlug): void => {
    setSettingsActive(false);
    setActiveDestination(slug);
  };

  // Reopen a specific conversation (Chats → Run, or a new chat's first send that
  // resolved to a real id): bind it, land on Run, and write the durable identity
  // to the URL. The Router subscription mirrors the same state (idempotent).
  const openConversation = useCallback(
    (id: ConversationId): void => {
      setActiveConversationId(id);
      setActiveDestination("run");
      setSettingsActive(false);
      props.router.navigate({ kind: "conversation", conversationId: id });
    },
    [props.router],
  );

  // Start a NEW chat: clear the bound conversation (so the cockpit shows its
  // empty composer) and land on Run. No conversation is created here — the first
  // send does that lazily (idempotency-keyed), then navigates via
  // `onConversationCreated`.
  const openNewRun = useCallback((): void => {
    setActiveConversationId(null);
    setActiveDestination("run");
    setSettingsActive(false);
  }, []);

  // PR-6.4: open Settings, optionally focused on a section (undefined → default).
  const handleOpenSettings = (section?: SettingsSectionSlug): void => {
    setSettingsSection(section ?? null);
    setSettingsActive(true);
  };

  // PR-6.6: wire the DESIGN-SPEC §6 GLOBAL chords through the single SSOT hook
  // (`useShellShortcuts`). Only the five global intents are provided here; every
  // callback closes over React setState functions (all stable), so the options
  // object is memoized with no deps — the hook attaches its listener once.
  //
  // Run-scoped chords (⌘M switch-mode, ⌘←/⌘→ rewind/step, ⌘L jump-live,
  // ⌘. pause, ⌘↵ approve, ⌘⌫ reject) are DELIBERATELY omitted: the Run cockpit
  // owns them internally (useRunMode / TcMiniTimeline / TcSwimlanes / approvals),
  // each with its own keydown listener scoped to the live run. Providing them
  // here too would double-wire — two listeners firing per press. Left undefined,
  // the hook no-ops them at the shell level and the cockpit stays the single
  // owner (FR-6.13 is satisfied by the cockpit's own handlers, not by bootstrap).
  const shortcutCallbacks = useMemo<ShellShortcutCallbacks>(
    () => ({
      // ⌘N — start a NEW chat: clears the bound conversation and lands on the
      // Run cockpit (the front door for starting a run), so the first send
      // creates a fresh conversation rather than appending to the current one.
      onNewRun: openNewRun,
      // ⌘K — toggle the palette. A single toggle per press proves single
      // sourcing; a duplicate listener would toggle twice (net no-op).
      onOpenPalette: () => setPaletteOpen((prev) => !prev),
      // ⌘, — open Settings at the profile-default section.
      onOpenSettings: () => {
        setSettingsSection(null);
        setSettingsActive(true);
      },
      // ⌘⇧M — open Settings focused on the local-models section (the model
      // picker lives there today).
      onOpenLocalModelPicker: () => {
        setSettingsSection("local-models");
        setSettingsActive(true);
      },
      // ⌘⇧F — search activity. Honest interim: navigate to the Activity
      // destination (its in-surface search lands with the real surface).
      onSearchActivity: () => {
        setSettingsActive(false);
        setActiveDestination("activity");
      },
    }),
    [openNewRun],
  );
  useShellShortcuts(shortcutCallbacks);

  // PRD-03: the shell's four host-owned capabilities as ONE total binding,
  // built in a single place (`buildDesktopShellBinding`) the conformance test
  // reuses. `railIdentity` carries the signed-in display name (previously
  // unbound — the rail always fell through to the generic person glyph).
  const shellBinding = buildDesktopShellBinding(props.session, settingsActive);

  return (
    <>
      <ChatShell
        transport={transport}
        router={props.router}
        keyValueStore={props.keyValueStore}
        presenceSignal={props.presenceSignal}
        activeDestination={activeDestination}
        destinations={destinations}
        onNavigate={handleNavigate}
        // PR-6.4: rail-foot Settings opens at the default section.
        onOpenSettings={() => handleOpenSettings()}
        // The shell topbar's single ⌘K trigger opens the (controlled) palette.
        onOpenCommandPalette={() => setPaletteOpen(true)}
        binding={shellBinding}
      >
        {settingsActive ? (
          // Phase 5 (PR-5.9): the real Settings surface — the profile-gated nav
          // plus every section body wired through `renderSection`. The team
          // sections stay gated off on the solo desktop profile and the solo
          // footer shows (DESIGN-SPEC §4 / FR-5.3).
          <SettingsMount
            transport={transport}
            session={props.session}
            // Real sign-out: clears the persisted session in main, then the gate
            // returns to the sign-in screen (SignInGate owns the auth phase).
            onSignOut={props.onSignOut}
            // PR-6.4: controlled section so the palette can deep-link Settings.
            activeSection={settingsSection}
            onSectionChange={setSettingsSection}
            // PRD-12 D9 — Appearance is a pass-through over the boot controller
            // mounted at the renderer root; Settings no longer owns the state.
            appearanceValue={appearance.value}
            onAppearanceChange={appearance.change}
          />
        ) : (
          <DestinationOutlet
            destination={activeDestination}
            // The active conversation the cockpit binds to (durable identity
            // from the Router URL). `null` → a brand-new chat's empty composer.
            conversationId={activeConversationId}
            // PRD-04 Seam C: Activity's open-run carries the row's
            // { conversationId, runId }. Bind the cockpit onto the CONVERSATION
            // (the cockpit binds by conversation id, not run id). Stops the old
            // silent argument discard (`() => handleNavigate("run")`).
            onOpenRun={(target) => openConversation(target.conversationId)}
            // Chats' "New chat" + Skills' "Run" — start a FRESH chat: clear the
            // bound conversation and land on Run (same as ⌘N / the palette).
            // `handleNavigate("run")` alone kept the stale conversation id bound,
            // so "New chat" re-opened the current run instead of a blank one.
            onNewChat={openNewRun}
            // Chats → reopen threads the REAL conversation id (navigate to its
            // Run route); a new chat's first send that resolved to a real id
            // navigates the same way. Both bind the cockpit onto that id.
            onOpenConversation={openConversation}
            onConversationCreated={openConversation}
            // Activity's retention link + Tools' approval-policy note deep-link
            // into the real Settings sections (reachable today, PR-5.9 / 6.4).
            onOpenRetentionSettings={() => handleOpenSettings("privacy")}
            onOpenApprovalSettings={() => handleOpenSettings("model-behavior")}
            // Run cockpit readiness / config-error CTAs → Settings → Provider
            // keys (Issues 1 + 2). handleOpenSettings + the slug already exist.
            onOpenModelSettings={() => handleOpenSettings("provider-keys")}
            // Model popover footer "Get local models →" → Settings → Local
            // models. Same deep-link the ⌘K `download-local-model` action uses.
            onOpenLocalModelSettings={() => handleOpenSettings("local-models")}
            // Run composer connections view → the Tools surface (MCP + non-MCP);
            // skills link → the Skills surface (slug `tools`).
            onOpenConnectors={() => handleNavigate("connectors")}
            onOpenSkills={() => handleNavigate("tools")}
          />
        )}
      </ChatShell>
      {/* PR-6.4: the global ⌘K palette + its topbar trigger. Mounted once at the
          shell root so ⌘K is global and the trigger overlays the topbar band.
          Dispatch: destination hits → the shell's `handleNavigate`; Settings
          hits → `handleOpenSettings(section)`; action hits → the flow launchers
          below. `add-provider-key` / `download-local-model` open the real
          Settings sections and `connect-tool` opens the Tools destination — all
          reachable today. `new-chat` routes to the Run cockpit (the front door
          for starting a run) as an honest interim; the dedicated new-run trigger
          (⌘N → onNewRun) is wired in PR-6.6 via `useShellShortcuts` above.
          PR-6.6: `open`/`onOpenChange` make the palette CONTROLLED so ⌘K is
          single-sourced through that hook (FR-6.14) — one ⌘K listener. */}
      <PaletteHost
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        onNavigateDestination={handleNavigate}
        onOpenSettings={handleOpenSettings}
        actions={{
          onNewChat: openNewRun,
          onAddProviderKey: () => handleOpenSettings("provider-keys"),
          onDownloadLocalModel: () => handleOpenSettings("local-models"),
          onConnectTool: () => handleNavigate("connectors"),
        }}
      />
    </>
  );
}

export function mountApp(container: HTMLElement): () => void {
  const root = createRoot(container);
  root.render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
  return () => {
    root.unmount();
  };
}

const autoMountTarget =
  typeof document === "undefined" ? null : document.getElementById("root");
if (autoMountTarget !== null) {
  mountApp(autoMountTarget);
}
