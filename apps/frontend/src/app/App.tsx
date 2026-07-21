import { ThemeProvider } from "@0x-copilot/design-system";
import type { McpServer } from "@0x-copilot/api-types";
import type { ReactElement } from "react";
import { Suspense, lazy, useEffect, useState } from "react";
import "@0x-copilot/design-system/styles.css";
import "streamdown/styles.css";
import "../styles.css";
import { decideApproval } from "../api/agentApi";
import type { RequestIdentity } from "../api/config";
import { completeMcpOAuth } from "../api/mcpApi";
import { AuthProvider, useAuth } from "../features/auth/AuthContext";
import { GOOGLE_LINK_CALLBACK_PATH } from "../features/auth/googleLinkLanding";
import {
  clearPendingMcpAuthAction,
  readPendingMcpAuthAction,
  type CompletedMcpAuthAction,
} from "../features/chat/mcpAuthAction";
import { useConnectors } from "../features/connectors/useConnectors";
// PR-4.11 (IA fold) — the six-destination solo shell mounts these host-side
// data binders at the destination dispatch. Chats / Activity / Skills are the
// Phase-4 binders; Projects / Connectors predate the fold. All are eager (vs
// the lazy screen chunks below) because they sit on the shell's hot
// destination-dispatch path — the single source of truth for "destination
// owned by a host-side binder vs the chat-surface placeholder".
//
// The seven folded destinations (home / library / inbox / todos / routines /
// agents / memory) are no longer mounted here; deep-links to them redirect
// (`foldedRedirectFor`, FR-4.31). Their feature routes stay in-tree for the
// Phase-6C dead-code sweep.
import { ChatsArchiveRoute } from "../features/chats/ChatsArchiveRoute";
import { ProjectsRoute } from "../features/projects/ProjectsRoute";
import { ActivityRoute } from "../features/activity/ActivityRoute";
import { SkillsRoute } from "../features/skills/SkillsRoute";
// `ConnectorsGateway` (the "Tools" destination) owns the in-destination
// routing between the list (/connectors), the detail (/connectors/<id>), and
// the webhooks sub-route. `TeamGateway` backs the team-profile `/team`
// surface. Both ride on local state because HashRouter only models top-level
// `/<destination>` slugs today.
import { ConnectorsGateway } from "../features/connectors/ConnectorsGateway";
import { TeamGateway } from "../features/team/TeamGateway";
// P12-C — ⌘K palette host adapter (sub-PRD §7.3 / §7.5). Mounted once
// at the App root so the ⌘K hotkey is global and there is a single
// CommandPalette instance across every page.
import { PaletteHost } from "../features/palette/PaletteHost";
// P12-C — new Phase 12 settings pages (`/settings/notification-defaults`,
// `/settings/security/webhooks`). Lives in its own screen kind so the
// legacy `SettingsScreen` shell is untouched.
import { SettingsGateway } from "../features/settings/SettingsGateway";
// PR 4.1 — hydrate user profile + preferences once at the shell so the
// Appearance attributes (data-density, data-reduce-motion, theme/accent)
// apply on chat too, not only when Settings is open.
import { AppearanceProvider } from "../features/appearance/AppearanceContext";
import { UserPreferencesProvider } from "../features/me/UserPreferencesContext";
import { useUserProfile } from "../features/me/useUserProfile";
import { UserProfileProvider } from "../features/me/UserProfileContext";
import { DEFAULT_SETTINGS_SECTION } from "../features/settings/sections";
import { useSkills } from "../features/skills/useSkills";

// Route-level code splitting. Each screen is its own Vite chunk so the
// main bundle only carries the chrome + auth-gate decision tree; the
// specific screen for the current URL loads on demand.
//
// Common cases:
//   - Signed-in user on /            → ChatScreen chunk only.
//   - Signed-out user on /login      → LoginScreen chunk only.
//   - Settings deep link             → SettingsScreen + ChatScreen chunks
//     (ChatScreen is the back-target so we let the user warm it as they
//     decide what to do; if cold load times start to hurt, we can flip
//     this to "fetch ChatScreen only on first navigate away from settings").
//
// `.then(m => ({ default: m.X }))` adapts named exports to React.lazy's
// default-export contract. Touching this list = adding/removing screens
// only; do not import the named symbol directly anywhere else here, or
// the chunking falls back to the main bundle.
const LoginScreen = lazy(() =>
  import("../features/auth/LoginScreen").then((m) => ({
    default: m.LoginScreen,
  })),
);
const MfaPrompt = lazy(() =>
  import("../features/auth/MfaPrompt").then((m) => ({ default: m.MfaPrompt })),
);
const GoogleLinkLanding = lazy(() =>
  import("../features/auth/GoogleLinkLandingScreen").then((m) => ({
    default: m.GoogleLinkLanding,
  })),
);
const ChatScreen = lazy(() =>
  import("../features/chat/ChatScreen").then((m) => ({
    default: m.ChatScreen,
  })),
);
const ShareScreen = lazy(() =>
  import("../features/share/ShareScreen").then((m) => ({
    default: m.ShareScreen,
  })),
);
const AdapterReviewScreen = lazy(() =>
  import("../admin/adapter-review").then((m) => ({
    default: m.AdapterReviewScreen,
  })),
);
const SettingsScreen = lazy(() =>
  import("../features/settings/SettingsScreen").then((m) => ({
    default: m.SettingsScreen,
  })),
);
// PRD-E convergence — the web app now mounts the chat-surface `SettingsSurface`
// (the SSOT settings shell + nav + icons) via this binder for every settings
// section EXCEPT `connectors`/`skills`, which have no SSOT nav slot and stay on
// the legacy `SettingsScreen` (MCP-server management + skill editor). Code-split
// like `SettingsScreen` so it costs the main bundle nothing until navigated to.
const SettingsBinder = lazy(() =>
  import("../features/settings/SettingsBinder").then((m) => ({
    default: m.SettingsBinder,
  })),
);
// P6.5-C2 — Project Templates gallery (sub-PRD §7.6). Top-level screen
// (not a destination slug — §7.6 + §12 Q1: not on the rail), code-split
// so the templates UI costs the main bundle nothing until the user
// navigates to it via the Projects destination's [Manage templates] CTA.
const TemplateGalleryRoute = lazy(() =>
  import("../features/project-templates/TemplateGalleryRoute").then((m) => ({
    default: m.TemplateGalleryRoute,
  })),
);
// PRD-05 — the real Run cockpit binder, code-split so the RunDestination /
// ThreadCanvas chunk costs the main bundle nothing while the `runCockpitWeb`
// flag is OFF (its default). Loaded only when the flag gates the `run` slug
// onto it (dispatch below), under the same `<Suspense>` as `body`.
const RunRoute = lazy(() =>
  import("../features/run/RunRoute").then((m) => ({
    default: m.RunRoute,
  })),
);

// Single fallback used whenever a route chunk is in flight. Matches the
// existing AuthGate "Loading session…" spinner shape so the user does
// not see two different placeholders during sign-in + first-route load.
function RouteLoadingFallback(): ReactElement {
  return (
    <main className="app-loading">
      <p>Loading…</p>
    </main>
  );
}
import {
  ChatShell,
  DeploymentProfileProvider,
  DocumentPresenceSignal,
  KeyValueStoreProvider,
  LocalStorageKeyValueStore,
  NotificationCenterProvider,
  SecretStorageProvider,
  ToastStack,
  WebSecretStorage,
  registerItemRefResolver,
  hasItemRefResolver,
  useKeyValueStore,
  type ArtifactRoute,
  type DeploymentProfile,
  type ShellDestinationSlug,
  type ShellCommandIntent,
} from "@0x-copilot/chat-surface";
import { getAppTransport } from "../api/transport";
import { HashRouter, migrateLegacySettingsPath } from "./HashRouter";
import { ROOT_DESTINATION, foldedRedirectFor, type AppRoute } from "./routes";
import type { SettingsSection } from "../features/settings/SettingsScreen";
import { errorMessage } from "../utils/errors";
import {
  PortProvider,
  WebBadgePort,
  WebClipboardPort,
  WebFilePickerPort,
  WebNotificationPort,
  type PortBundle,
} from "../ports";
// PRD-05 — register the web host's surface-renderer stack (tier-3 generic +
// tier-1 SaaS + PRD-03 archetypes) once at module init, mirroring the desktop
// bootstrap. Idempotent (registry replace semantics); no Tier2Bridge on web.
import { registerSurfaces } from "./registerSurfaces";
// PRD-05 — the `runCockpitWeb` flag (default OFF) gates the real RunDestination
// cockpit vs the legacy ChatScreen under the `run` slug.
import { isRunCockpitWebEnabled } from "./featureFlags";

registerSurfaces();

// Placeholder for a destination that exists in the shared slug union but has
// no web surface yet. Renders an inert, unavailable section rather than
// crashing the dispatch.
function DesktopOnlyDestination(): ReactElement {
  return <section data-destination-unavailable style={{ height: "100%" }} />;
}

// PR-4.11 — the only slugs that reach the placeholder path are the
// team-profile-only Members and Billing surfaces (Phase 6 / Settings work);
// they are never on the solo rail, but a deep-link must resolve to a harmless
// placeholder instead of an undefined outlet. Every other slug is either a
// real binder branch in the dispatch below (run / chats / projects / activity
// / connectors / tools / team) or a folded slug that redirects (FR-4.31).
const PLACEHOLDER_DESTINATIONS: Partial<
  Record<ShellDestinationSlug, () => ReactElement>
> = {
  members: DesktopOnlyDestination,
  billing: DesktopOnlyDestination,
};

// ItemRef resolver registration (cross-audit §3.3) for the `"todo"`
// kind. The Todos destination owns this kind — when a chat / agent
// activity entry surfaces an `<ItemLink kind="todo" id={...} />`, the
// link resolves to the Todos destination's detail surface. Today the
// destination has no per-todo detail route, so we return `route: null`
// — `<ItemLink>` falls back to the breadcrumb. Phase 3 Impl-B replaces
// this with a richer resolver via `{ replace: true }` once the
// detail surface lands.
//
// Guarded with `hasItemRefResolver` so test environments that import
// the module across multiple vitest realms don't throw
// `ItemRefResolverAlreadyRegistered`.
if (!hasItemRefResolver("todo")) {
  registerItemRefResolver("todo", async (_id) => ({
    label: "Todo",
    icon: null,
    route: null,
    breadcrumb: "Todos",
  }));
}

// ItemRef resolver registration (cross-audit §3.3) for the
// `"inbox_item"` kind. The Inbox destination owns this kind — when a
// chat / agent activity / notification surfaces an `<ItemLink
// kind="inbox_item" id={...} />`, the link resolves to the Inbox
// destination. Today the destination has no per-item detail route
// (`/inbox/<id>` lands in Phase 4 Impl-B), so we return `route: null` —
// `<ItemLink>` falls back to the breadcrumb. Same `hasItemRefResolver`
// guard pattern as the `"todo"` registration above so cross-realm
// vitest imports don't throw `ItemRefResolverAlreadyRegistered`.
if (!hasItemRefResolver("inbox_item")) {
  registerItemRefResolver("inbox_item", async (_id) => ({
    label: "Inbox item",
    icon: null,
    route: null,
    breadcrumb: "Inbox",
  }));
}

// ItemRef resolver registration (cross-audit §3.3) for the `"project"`
// kind. P6-C — the Projects destination owns this kind; when a
// cross-destination activity surface (chat / inbox / todos / routines
// activity) surfaces an `<ItemLink kind="project" id={...} />`, the
// link resolves to the Projects workspace. The route here uses the
// chat-surface `ArtifactRoute` `workspace` shape so navigating an
// ItemLink lands inside the project's workspace pane (sub-PRD §3.4 —
// the `/projects/<id>` detail surface). Same `hasItemRefResolver`
// guard pattern as the `"todo"` / `"inbox_item"` registrations above
// so cross-realm vitest imports don't throw
// `ItemRefResolverAlreadyRegistered`.
if (!hasItemRefResolver("project")) {
  registerItemRefResolver("project", async (id) => ({
    label: "Project",
    icon: null,
    route: { kind: "workspace", workspaceId: id as unknown as string },
    breadcrumb: "Projects",
  }));
}

// ItemRef resolver registration (cross-audit §3.3) for Library + Agents
// kinds. Each destination's route lands in a follow-up wave; today the
// resolvers return route:null so `<ItemLink>` falls back to breadcrumb.
if (!hasItemRefResolver("library_file")) {
  registerItemRefResolver("library_file", async (_id) => ({
    label: "File",
    icon: null,
    route: null,
    breadcrumb: "Library",
  }));
}
if (!hasItemRefResolver("library_page")) {
  registerItemRefResolver("library_page", async (_id) => ({
    label: "Page",
    icon: null,
    route: null,
    breadcrumb: "Library",
  }));
}
if (!hasItemRefResolver("library_dataset")) {
  registerItemRefResolver("library_dataset", async (_id) => ({
    label: "Dataset",
    icon: null,
    route: null,
    breadcrumb: "Library",
  }));
}
if (!hasItemRefResolver("agent")) {
  registerItemRefResolver("agent", async (_id) => ({
    label: "Agent",
    icon: null,
    route: null,
    breadcrumb: "Agents",
  }));
}

/**
 * The org slug LoginScreen falls back to when the URL doesn't carry one.
 * SaaS deploys eventually parse from the subdomain; single-tenant deploys
 * hardcode it via build-time env.
 */
const DEFAULT_ORG_ID =
  (typeof import.meta !== "undefined" &&
    import.meta.env?.VITE_DEFAULT_ORG_ID) ||
  "org_123";

/**
 * PR-4.11 — the web shell renders the six-destination `single_user_desktop`
 * solo rail by default (Run / Chats / Projects / Activity / Tools / Skills).
 * A `team` deployment opts into the nine-destination rail via
 * `VITE_DEPLOYMENT_PROFILE=team`. The value flows into
 * `DeploymentProfileProvider`, which gates the rail (`destinationsForProfile`)
 * — and the profile-aware Settings surfaces — instead of the frozen legacy
 * 12-destination `SHELL_DESTINATIONS` fallback.
 */
const DEPLOYMENT_PROFILE: DeploymentProfile =
  (typeof import.meta !== "undefined" &&
    import.meta.env?.VITE_DEPLOYMENT_PROFILE) === "team"
    ? "team"
    : "single_user_desktop";

const mcpOAuthCompletions = new Map<string, Promise<McpServer>>();

export default function App(): ReactElement {
  // Construct the substrate-side KeyValueStore here (not inside
  // CopilotApp) so AuthProvider's `useKeyValueStore()` resolves
  // to the real store. AuthProvider sits above CopilotApp; if
  // the provider were only mounted inside ChatShell, AuthProvider would
  // pull the context's no-op default and the dev IdP would always mint
  // for the DEFAULT persona instead of the one the user picked in
  // DevPersonaSwitcher. Same instance flows down to ChatShell so the
  // entire tree shares one substrate-bound store.
  const [keyValueStore] = useState(() => new LocalStorageKeyValueStore());
  // SecretStorage is hoisted alongside KeyValueStore (above AuthProvider)
  // for the same reason — AuthContext consumes useSecretStorage() during
  // the initial bearer load, well before ChatShell mounts. Same instance,
  // single substrate-bound source of truth for secret values.
  const [secretStorage] = useState(() => new WebSecretStorage());
  return (
    <ThemeProvider defaultScheme="dark">
      <NotificationCenterProvider>
        <KeyValueStoreProvider store={keyValueStore}>
          <SecretStorageProvider store={secretStorage}>
            <AuthProvider>
              <AuthGate />
            </AuthProvider>
          </SecretStorageProvider>
        </KeyValueStoreProvider>
        {/* One toast surface for the whole app; floats above full-bleed surfaces. */}
        <ToastStack />
      </NotificationCenterProvider>
    </ThemeProvider>
  );
}

/**
 * Gates the app behind the AuthContext state machine. ``initial`` /
 * ``loading`` show the boot spinner; ``anonymous`` / ``error`` route to
 * the login screen; ``mfa_pending`` to the MFA prompt; only
 * ``authenticated`` renders the actual app shell.
 *
 * Lives here (rather than inside ``CopilotApp``) so the rest of
 * the app continues to assume identity is non-null — same invariant the
 * pre-A9 code relied on.
 */
function AuthGate(): ReactElement {
  const auth = useAuth();

  // Account-linking (PRD FR-L2): the Google LINK callback lands here after
  // the facade redirects the outcome into product UI. Show the result screen
  // regardless of auth-rehydration state — the sensitive link already
  // happened server-side; this only communicates it, then routes the user
  // back into the (authenticated) app.
  if (
    typeof window !== "undefined" &&
    window.location.pathname === GOOGLE_LINK_CALLBACK_PATH
  ) {
    return (
      <Suspense fallback={<RouteLoadingFallback />}>
        <GoogleLinkLanding />
      </Suspense>
    );
  }

  if (auth.status === "initial" || auth.status === "loading") {
    return (
      <main className="app-loading">
        <p>Loading session…</p>
      </main>
    );
  }

  if (auth.status === "mfa_pending") {
    return (
      <Suspense fallback={<RouteLoadingFallback />}>
        <MfaPrompt rpId={window.location.hostname} />
      </Suspense>
    );
  }

  // PR 5.1 — magic-link callback URL routes through LoginScreen even
  // before AuthContext has flipped to a real status. The screen reads
  // ?token= itself and calls auth.consumeMagicLink on mount.
  const onMagicLinkCallback =
    typeof window !== "undefined" &&
    window.location.pathname === "/auth/magic-link/callback";

  if (
    auth.status === "anonymous" ||
    auth.status === "error" ||
    auth.status === "workspace_pick" ||
    onMagicLinkCallback
  ) {
    return (
      <>
        {auth.status === "error" && auth.error && (
          <p className="app-loading" role="alert" data-testid="app-auth-error">
            {auth.error}
          </p>
        )}
        <Suspense fallback={<RouteLoadingFallback />}>
          <LoginScreen
            defaultOrgId={DEFAULT_ORG_ID}
            returnTo={
              window.location.pathname === "/login" ||
              window.location.pathname === "/auth/magic-link/callback"
                ? undefined
                : window.location.pathname + window.location.search
            }
          />
        </Suspense>
      </>
    );
  }

  if (auth.identity === null) {
    // ``authenticated`` always carries a non-null identity (set in the
    // AuthContext reducer). Render the boot spinner as a defensive
    // fallback rather than crashing.
    return (
      <main className="app-loading">
        <p>Loading session…</p>
      </main>
    );
  }

  return (
    <UserProfileProvider>
      <UserPreferencesProvider>
        <AppearanceProvider>
          <CopilotApp
            identity={{
              orgId: auth.identity.org_id,
              userId: auth.identity.user_id,
            }}
            roles={auth.identity.roles}
          />
        </AppearanceProvider>
      </UserPreferencesProvider>
    </UserProfileProvider>
  );
}

function completeMcpOAuthOnce(
  state: string,
  code: string | null,
  error: string | null,
  errorDescription: string | null,
): Promise<McpServer> {
  const key = JSON.stringify([state, code, error, errorDescription]);
  const existing = mcpOAuthCompletions.get(key);
  if (existing) {
    return existing;
  }
  const completion = completeMcpOAuth(
    state,
    code,
    error,
    errorDescription,
  ).catch((err: unknown) => {
    mcpOAuthCompletions.delete(key);
    throw err;
  });
  mcpOAuthCompletions.set(key, completion);
  return completion;
}

export function CopilotApp({
  identity,
  roles,
}: {
  identity: RequestIdentity;
  roles: readonly string[];
}): ReactElement {
  const connectors = useConnectors(identity);
  const skills = useSkills(identity);
  // PR 4.1 — server-side profile snapshot for the sidebar greeting +
  // Settings forms. Preferences (theme, accent, density, reduce_motion,
  // notifications, shortcuts) come from `UserPreferencesProvider` one
  // layer up — `AppearanceProvider` owns the appearance write path
  // (PRD 04: design-system ThemeProvider + document.documentElement
  // attrs + debounced server save).
  const profile = useUserProfile();
  // Routing goes through the Router port (packages/chat-surface). HashRouter
  // owns every window.history / popstate / hashchange interaction on web;
  // the desktop substrate will swap in its own implementation without any
  // App.tsx changes.
  const [router] = useState(() => new HashRouter());
  // The KeyValueStore is constructed at the top-level App component
  // (so AuthProvider can see it). Pull it from context here to pass
  // through to ChatShell — same instance, single source of truth.
  const keyValueStore = useKeyValueStore();
  // PresenceSignal is local to CopilotApp — AuthProvider doesn't
  // need it (nothing in auth listens for tab visibility), so we don't have
  // to hoist it the way KeyValueStore was hoisted. Constructed once via
  // useState; reads through globalThis.document each call so jsdom and
  // the real DOM both work.
  const [presenceSignal] = useState(() => new DocumentPresenceSignal());
  // PortProvider — substrate-agnostic injection point for the four
  // Phase 0.5 substrate ports (Badge, Notification, FilePicker,
  // Clipboard). The web implementations are deliberately thin wrappers
  // around the browser API; the desktop substrate will swap in native
  // implementations at this same provider without any consumer change
  // (cross-audit §5.4). NotificationPort's click-target navigation goes
  // through the Router constructed above — same instance shared between
  // ChatShell and the port.
  const [ports] = useState<PortBundle>(() => ({
    badge: new WebBadgePort(),
    notification: new WebNotificationPort({
      navigate: (artifactRoute: ArtifactRoute) => {
        // Lift the substrate-portable ArtifactRoute into the host's
        // wider AppRoute union. Today only `chat` and `conversation`
        // map cleanly — the rest are no-ops until the destinations
        // they reference register their own resolvers + routes.
        if (
          artifactRoute.kind === "chat" ||
          artifactRoute.kind === "conversation"
        ) {
          router.navigate({ screen: "chat", destination: ROOT_DESTINATION });
        }
      },
    }),
    filePicker: new WebFilePickerPort(),
    clipboard: new WebClipboardPort(),
  }));
  const [route, setRoute] = useState<AppRoute>(() => {
    // PR 4.3 — One-shot migration of legacy ``/settings/<section>`` URLs
    // into the hashed form. Runs at most once per session because the
    // migrator's own ``replaceState`` removes the legacy path. Done in
    // the lazy initialiser so the first paint already shows the right
    // section.
    migrateLegacySettingsPath();
    return router.current();
  });
  const [oauthStatus, setOauthStatus] = useState<string | null>(null);
  const [completedMcpAuthAction, setCompletedMcpAuthAction] =
    useState<CompletedMcpAuthAction | null>(null);
  // ⌘K palette open-state, lifted here so the shell topbar's single trigger
  // (ChatShell.onOpenCommandPalette) and the palette's ⌘K hotkey share one state.
  const [paletteOpen, setPaletteOpen] = useState(false);
  // PRD-05 — read the `runCockpitWeb` flag once per mount (not a module const,
  // so a devtools toggle / test seed takes effect on the next mount). OFF
  // (default) keeps the legacy ChatScreen under `run`; ON mounts RunRoute.
  const [runCockpitWebEnabled] = useState(() => isRunCockpitWebEnabled());

  useEffect(() => router.subscribe(setRoute), [router]);

  // FR-4.31 — redirect a deep-link that lands on a folded destination slug
  // (home / library / inbox / todos / routines / agents / memory) to the
  // destination that absorbed it. `foldedRedirect` is a stable object
  // reference per slug (it comes straight from `FOLDED_DESTINATION_REDIRECTS`),
  // so this effect fires once per folded landing rather than on every render.
  // `replace` keeps the folded URL out of the back stack. The render body
  // shows the loading fallback for the transient frame before the redirect
  // lands (see the dispatch below), so a folded slug never renders a dead
  // outlet.
  const foldedRedirect = foldedRedirectFor(route);
  useEffect(() => {
    if (foldedRedirect !== null) {
      router.navigate(foldedRedirect, { replace: true });
    }
  }, [foldedRedirect, router]);

  useEffect(() => {
    if (window.location.pathname !== "/mcp/oauth/callback") {
      return;
    }
    const currentIdentity = identity;
    const params = new URLSearchParams(window.location.search);
    const state = params.get("state");
    const code = params.get("code");
    const oauthError = params.get("error");
    const oauthErrorDescription = params.get("error_description");
    if (!state || (!code && !oauthError)) {
      setOauthStatus(
        "Connector authentication callback was missing state, code, or error.",
      );
      router.navigate(
        { screen: "chat", destination: ROOT_DESTINATION },
        { replace: true },
      );
      return;
    }
    const callbackState = state;
    const callbackCode = code;
    const callbackError = oauthError;
    const callbackErrorDescription = oauthErrorDescription;

    let cancelled = false;
    async function finishOAuth(): Promise<void> {
      try {
        const server = await completeMcpOAuthOnce(
          callbackState,
          callbackCode,
          callbackError,
          callbackErrorDescription,
        );
        if (!cancelled) {
          const pendingAction = readPendingMcpAuthAction(server.server_id);
          if (pendingAction !== null) {
            // Discovery cards (`mcp_discovery:<run_id>:<server_id>`)
            // aren't persisted as ApprovalRequest rows by the runtime
            // — the backend never has anything to decide against, and
            // the POST 404s. Skip the decideApproval call for those
            // ids; the OAuth completion itself is the resolution.
            const isDiscoveryApproval =
              pendingAction.approvalId.startsWith("mcp_discovery:");
            if (!isDiscoveryApproval) {
              try {
                await decideApproval(
                  pendingAction.approvalId,
                  "approved",
                  currentIdentity,
                  "mcp_auth_completed",
                );
              } catch {
                // The connector can still be authenticated if the approval record
                // was lost during a backend restart.
              }
            }
            if (cancelled) {
              return;
            }
            clearPendingMcpAuthAction();
            setCompletedMcpAuthAction({
              ...pendingAction,
              completedAt: new Date().toISOString(),
            });
            setOauthStatus(`${server.display_name} is connected.`);
            router.navigate(
              { screen: "chat", destination: ROOT_DESTINATION },
              { replace: true },
            );
          } else {
            setCompletedMcpAuthAction(null);
            setOauthStatus(`${server.display_name} is connected.`);
            router.navigate(
              { screen: "settings", section: "connectors" },
              { replace: true },
            );
          }
          await connectors.refresh().catch(() => undefined);
        }
      } catch (err) {
        if (!cancelled) {
          setOauthStatus(errorMessage(err, "Connector authentication failed."));
          router.navigate(
            { screen: "chat", destination: ROOT_DESTINATION },
            { replace: true },
          );
        }
      }
    }

    void finishOAuth();
    return () => {
      cancelled = true;
    };
  }, [connectors.refresh, identity]);

  const isAdmin = roles.includes("admin");

  // Compute the active destination ONCE so AppRail / ContextPanel /
  // Topbar all agree on which destination is "live". Non-chat screens
  // (settings, share, admin) collapse the rail's active state to the
  // legacy chats destination — the rail itself is hidden visually for
  // those screens anyway via ChatShell receiving no leaf, but keeping
  // the value valid avoids a stale highlight if the user navigates back.
  const activeDestination: ShellDestinationSlug =
    route.screen === "chat" ? route.destination : ROOT_DESTINATION;

  // P12-C — round-trip a destination sub-path through the URL when the
  // gateway switches in-destination panes. Post-IA-fold (PR-4.11) only the
  // team-profile Team destination consumes sub-paths (Memory folded into
  // Settings → Privacy); other gateways still pass `null`. Replace history
  // (not push) so the back button skips over intra-destination
  // transitions — mirrors the legacy settings hash migration.
  function handleSubPathChange(
    destination: ShellDestinationSlug,
    subPath: string | null,
  ): void {
    if (route.screen !== "chat" || route.destination !== destination) return;
    if ((route.subPath ?? null) === subPath) return;
    router.navigate(
      { screen: "chat", destination, subPath },
      { replace: true },
    );
  }

  const handleRailNavigate = (slug: ShellDestinationSlug): void => {
    router.navigate({ screen: "chat", destination: slug });
  };

  // ⌘K command launcher (PRD-D): map a command intent to real web navigation.
  // Navigate intents route through the rail handler; settings intents map the
  // chat-surface section slug to the web `SettingsSection`. PRD-E's convergence
  // mounts the SSOT `SettingsSurface` on web (via `SettingsBinder`) but keeps
  // the web router's legacy section spellings for URL/back-compat, so the one
  // remaining mismatch — `model-behavior` → `model-and-behavior` — stays; the
  // binder maps it back to the SSOT slug. The other palette sections
  // (`provider-keys`, `local-models`, `appearance`, `profile`) are identical.
  const handlePaletteCommand = (intent: ShellCommandIntent): void => {
    if (intent.type === "navigate") {
      handleRailNavigate(intent.slug);
      return;
    }
    const section: SettingsSection =
      intent.section === "model-behavior"
        ? "model-and-behavior"
        : (intent.section as SettingsSection);
    router.navigate({ screen: "settings", section });
  };

  // PR-4.11 — host navigation seams the Phase-4 binders defer to the App
  // (each binder takes these as props so it stays decoupled from the
  // `AppRoute` union). The Run cockpit on web is the working conversation
  // surface (`ChatScreen`) mounted under the `run` slug.
  //
  // `openRun` funnels reopen (Chats) / new-chat / skill-run (Skills) / live-run
  // (Activity) / project-chat (Projects) into the Run destination. The
  // conversation/run id is accepted because the binders pass it, but selecting
  // that specific thread inside the cockpit is a Phase-3 Run-screen concern —
  // `ChatScreen` opens its most-recent thread today; threading the id through
  // is a one-line follow-up once the cockpit accepts an initial conversation.
  const openRun = (_idOrRunId?: string): void => {
    router.navigate({ screen: "chat", destination: "run" });
  };
  // Activity's retention/export/delete link → Settings → Privacy & data
  // (FR-4.17).
  const openRetentionSettings = (): void => {
    router.navigate({ screen: "settings", section: "privacy-data" });
  };
  // Tools' approval-policy note → Settings → Model & behavior (FR-4.25).
  const openApprovalSettings = (): void => {
    router.navigate({ screen: "settings", section: "model-and-behavior" });
  };
  // Skills Edit / New open the existing skill editor, which lives in
  // Settings → Skills. `skillId` is accepted for forward-compat (deep-link to
  // a specific skill) but the Settings section does not pre-select a skill yet
  // — a Phase-5 Settings enhancement. `null` = create a new skill (FR-4.27 /
  // FR-4.28).
  const openSkillEditor = (_skillId?: string | null): void => {
    router.navigate({ screen: "settings", section: "skills" });
  };
  // PRD-05 — the Run cockpit's empty-state "Set up your model" CTA + the
  // `configuration_error` "Add a provider key" CTA open Settings → Provider
  // keys. Only reached when the `runCockpitWeb` flag mounts `RunRoute`.
  const openModelSettings = (): void => {
    router.navigate({ screen: "settings", section: "provider-keys" });
  };

  let body: ReactElement;
  if (
    route.screen === "admin-adapter-review-queue" ||
    route.screen === "admin-adapter-review-detail"
  ) {
    // Phase 7C — admin tier-2 adapter review queue. The role gate here
    // is defence-in-depth; the backend's
    // ``admin:adapter_registry_review`` scope is the real boundary. If
    // a non-admin lands on the route (bookmark, copy-paste), we bounce
    // back to chat rather than crashing — the API would 403 anyway.
    if (!isAdmin) {
      router.navigate(
        { screen: "chat", destination: ROOT_DESTINATION },
        { replace: true },
      );
      body = (
        <ChatScreen
          connectors={connectors}
          skills={skills}
          identity={identity}
          onOpenSettings={(section = "profile") => {
            router.navigate({ screen: "settings", section });
          }}
          oauthStatus={oauthStatus}
          completedMcpAuthAction={completedMcpAuthAction}
        />
      );
    } else {
      const adminRoute =
        route.screen === "admin-adapter-review-detail"
          ? { screen: "detail" as const, candidateId: route.candidateId }
          : { screen: "queue" as const };
      body = (
        <AdapterReviewScreen
          identity={identity}
          route={adminRoute}
          onOpenCandidate={(candidateId) =>
            router.navigate({
              screen: "admin-adapter-review-detail",
              candidateId,
            })
          }
          onBackToQueue={() =>
            router.navigate({ screen: "admin-adapter-review-queue" })
          }
        />
      );
    }
  } else if (route.screen === "settings") {
    // PRD-E — `connectors`/`skills` have no SSOT nav slot (they are rail
    // destinations; MCP-server management + the skill editor still live in the
    // legacy screen), so they keep rendering `SettingsScreen`. Every other
    // section mounts the converged chat-surface `SettingsSurface` via the binder.
    if (route.section === "connectors" || route.section === "skills") {
      body = (
        <SettingsScreen
          connectors={connectors}
          skills={skills}
          identity={identity}
          profile={profile}
          initialSection={route.section}
          onBackToChat={() =>
            router.navigate({ screen: "chat", destination: ROOT_DESTINATION })
          }
          onSectionChange={(section) =>
            router.navigate({ screen: "settings", section })
          }
        />
      );
    } else {
      body = (
        <SettingsBinder
          transport={getAppTransport()}
          profile={profile}
          identity={identity}
          isAdmin={isAdmin}
          section={route.section}
          onNavigate={(section) =>
            router.navigate({ screen: "settings", section })
          }
        />
      );
    }
  } else if (route.screen === "settings-p12") {
    // P12-C — Phase 12 settings pages
    // (`/settings/notification-defaults`, `/settings/security/webhooks`).
    body = (
      <SettingsGateway
        identity={identity}
        isAdmin={isAdmin}
        subPath={route.subPath}
        onBackToChat={() =>
          router.navigate({ screen: "chat", destination: ROOT_DESTINATION })
        }
      />
    );
  } else if (
    route.screen === "project-templates-gallery" ||
    route.screen === "project-templates-editor"
  ) {
    // P6.5-C2 — Project Templates gallery. The editor route shape lands
    // in a follow-up wave; today the editor URL renders the gallery so
    // the deep link resolves rather than 404ing on a copy-paste / share.
    // Fork success on the gallery navigates to the new project's chat
    // surface (the project detail view).
    body = (
      <section
        data-testid="destination-outlet"
        data-destination="project-templates"
        style={{ height: "100%", overflow: "auto" }}
        aria-label="project-templates screen"
      >
        <TemplateGalleryRoute
          identity={identity}
          onForked={(projectId) => {
            // Navigate the user to the newly-forked project. v1 routes
            // by destination (Projects rail) — when project-detail
            // routing lands, replace this with a per-project deep link.
            router.navigate(
              { screen: "chat", destination: "projects" },
              { replace: true },
            );
            // Stash the new id so the Projects route can highlight it
            // on next paint without crashing pre-detail-surface clients.
            try {
              window.sessionStorage.setItem(
                "enterprise.project-templates.lastForkedId",
                projectId,
              );
            } catch {
              // sessionStorage may be unavailable (privacy mode); the
              // navigation is the user-observable behaviour — the hint
              // is best-effort only.
            }
          }}
        />
      </section>
    );
  } else if (route.screen === "share") {
    body = (
      <ShareScreen
        token={route.token}
        identity={identity}
        onForked={(conversationId) => {
          // After fork, navigate to the chat surface with the new
          // conversation pre-selected. The chat screen reads
          // ?conversationId= and opens that thread.
          window.location.href = `/?conversationId=${encodeURIComponent(conversationId)}`;
        }}
        onBackToChat={() =>
          router.navigate({ screen: "chat", destination: ROOT_DESTINATION })
        }
      />
    );
  } else if (foldedRedirect !== null) {
    // FR-4.31 — a deep-link landed on a folded destination slug. The effect
    // above navigates to the absorbing destination; render the loading
    // fallback for the transient frame before that redirect lands so we never
    // dispatch a dead/undefined outlet for `home`/`library`/`inbox`/`todos`/
    // `routines`/`agents`/`memory`.
    body = <RouteLoadingFallback />;
  } else if (route.destination === "run") {
    // PR-4.11 / PRD-05 — the Run cockpit. Two mounts, one gated by the
    // `runCockpitWeb` flag:
    //   - flag ON  → the real `RunDestination` cockpit (chat-surface), bound by
    //     the web `RunRoute` binder. It owns the Studio/Focus canvas, the
    //     surface-tab center pane (archetype renderers), the workspace rail, and
    //     the empty-state goal composer.
    //   - flag OFF (default) → the legacy `ChatScreen`, BYTE-IDENTICAL to the
    //     pre-PRD-05 path (no regression while the flag stays off).
    // `run` is full-bleed in ChatShell (no ContextPanel / Topbar); `/` maps to
    // `run` (ROOT_DESTINATION), so the legacy `/` bookmark keeps working.
    body = runCockpitWebEnabled ? (
      <RunRoute onOpenModelSettings={openModelSettings} />
    ) : (
      <ChatScreen
        connectors={connectors}
        skills={skills}
        identity={identity}
        onOpenSettings={(section = "profile") => {
          router.navigate({ screen: "settings", section });
        }}
        oauthStatus={oauthStatus}
        completedMcpAuthAction={completedMcpAuthAction}
      />
    );
  } else if (route.destination === "chats") {
    // PR-4.11 (FR-4.5..4.9) — Chats is now the conversation ARCHIVE
    // (pinned/recent/archived), not the live cockpit. `ChatsArchiveRoute`
    // buckets `/v1/agent/conversations`; reopen + new-chat funnel through
    // `openRun` → the Run destination.
    body = (
      <section
        data-testid="destination-outlet"
        data-destination="chats"
        style={{ height: "100%", overflow: "auto" }}
        aria-label="chats destination"
      >
        <ChatsArchiveRoute identity={identity} onOpenRun={openRun} />
      </section>
    );
  } else if (route.destination === "projects") {
    // P6-C — Projects destination dispatch. The route owns its own
    // fetch + SSE membership stream (sub-PRD §3.8); the chat-surface
    // `<ProjectsDestination>` placeholder is no longer mounted here. A chat
    // row in the project detail opens the Run cockpit via `openRun` (FR-4.12).
    body = (
      <section
        data-testid="destination-outlet"
        data-destination="projects"
        style={{ height: "100%", overflow: "auto" }}
        aria-label="projects destination"
      >
        <ProjectsRoute identity={identity} onOpenRun={openRun} />
      </section>
    );
  } else if (route.destination === "activity") {
    // PR-4.11 (FR-4.14..4.19) — Activity is the recast run/audit/agents/inbox
    // feed. A live-run row opens the Run cockpit (`openRun`); the retention
    // link opens Settings → Privacy & data (`openRetentionSettings`).
    body = (
      <section
        data-testid="destination-outlet"
        data-destination="activity"
        style={{ height: "100%", overflow: "auto" }}
        aria-label="activity destination"
      >
        <ActivityRoute
          identity={identity}
          onOpenRun={openRun}
          onOpenRetentionSettings={openRetentionSettings}
        />
      </section>
    );
  } else if (route.destination === "connectors") {
    // PR-4.11 — the "Tools" destination (slug `connectors`, relabeled by the
    // solo profile). ConnectorsGateway owns the in-destination routing between
    // the list, the detail, and the webhooks sub-route; the approval-policy
    // note links to Settings → Model & behavior (`openApprovalSettings`,
    // FR-4.25).
    body = (
      <section
        data-testid="destination-outlet"
        data-destination="connectors"
        style={{ height: "100%", overflow: "auto" }}
        aria-label="connectors destination"
      >
        <ConnectorsGateway
          identity={identity}
          isAdmin={isAdmin}
          onOpenApprovalSettings={openApprovalSettings}
        />
      </section>
    );
  } else if (route.destination === "tools") {
    // PR-4.11 — the "Skills" destination (slug `tools`, relabeled by the solo
    // profile). `SkillsRoute` is the saved-workflow catalog backed by
    // `/v1/skills`; Run starts a run + opens the Run cockpit (`openRun`),
    // Edit/New open the skill editor (`openSkillEditor`, Settings → Skills).
    body = (
      <section
        data-testid="destination-outlet"
        data-destination="tools"
        style={{ height: "100%", overflow: "auto" }}
        aria-label="skills destination"
      >
        <SkillsRoute
          identity={identity}
          onOpenRun={openRun}
          onOpenSkillEditor={openSkillEditor}
        />
      </section>
    );
  } else if (route.destination === "team") {
    // P12-C — Team destination dispatch. TeamGateway owns the list
    // (`/team`) ↔ detail (`/team/<id>`) routing; the chat-surface
    // Wave-0 `<TeamDestination>` placeholder is no longer mounted.
    body = (
      <section
        data-testid="destination-outlet"
        data-destination="team"
        style={{ height: "100%", overflow: "auto" }}
        aria-label="team destination"
      >
        <TeamGateway
          identity={identity}
          initialPersonId={route.subPath ?? null}
          onSubPathChange={(sub) => handleSubPathChange("team", sub)}
        />
      </section>
    );
  } else {
    // Only the team-profile-only Members / Billing slugs reach here (every
    // other slug is a live binder branch above, a folded redirect, or `memory`
    // which redirects to Settings → Privacy). They have no web surface yet, so
    // fall back to the inert placeholder rather than an undefined outlet.
    const Destination =
      PLACEHOLDER_DESTINATIONS[route.destination] ?? DesktopOnlyDestination;
    body = (
      <section
        data-testid="destination-outlet"
        data-destination={route.destination}
        style={{ height: "100%", overflow: "auto" }}
        aria-label={`${route.destination} destination`}
      >
        <Destination />
      </section>
    );
  }

  // Every screen mounts inside ChatShell so any descendant — including
  // future components migrated into chat-surface — can reach the active
  // Transport, Router, and KeyValueStore via hooks instead of singletons
  // or window globals. The transport is a stable module singleton; the
  // router and the KV store are local instances stable across renders.
  //
  // Suspense wraps `body` (not ChatShell) so the rails/topbar chrome
  // stay visible while a route chunk is in flight — only the centre
  // pane shows the fallback, matching how users expect a route transition
  // to behave in a shell-style app.
  return (
    // PR-4.11 — the DeploymentProfile port drives the profile-gated shell rail
    // (`destinationsForProfile`): the six-destination solo set by default, the
    // nine-destination team set under `VITE_DEPLOYMENT_PROFILE=team`. Without
    // this provider ChatShell falls back to the frozen legacy 12-destination
    // `SHELL_DESTINATIONS` rail.
    <DeploymentProfileProvider profile={DEPLOYMENT_PROFILE}>
      <PortProvider ports={ports}>
        <ChatShell
          transport={getAppTransport()}
          router={router}
          keyValueStore={keyValueStore}
          presenceSignal={presenceSignal}
          activeDestination={activeDestination}
          onNavigate={handleRailNavigate}
          onOpenSettings={() =>
            router.navigate({
              screen: "settings",
              section: DEFAULT_SETTINGS_SECTION,
            })
          }
          onOpenCommandPalette={() => setPaletteOpen(true)}
          // PRD-C.2 / PRD-H.5 — feed the rail foot avatar the user's initial from
          // the profile the shell already loads. The Run badge (activeRunCount)
          // still needs a run-list source and is a documented follow-up.
          railIdentity={
            profile?.data?.display_name?.trim()
              ? { initial: profile.data.display_name.trim().charAt(0) }
              : undefined
          }
        >
          <Suspense fallback={<RouteLoadingFallback />}>{body}</Suspense>
          {/*
            P12-C — ⌘K palette host. Mounted once at the App root so the
            hotkey is global and every page renders one CommandPalette
            modal. The host owns the PaletteSearchPort that calls
            `/v1/palette/search` through the facade (sub-PRD §7.3).
          */}
          <PaletteHost
            identity={identity}
            open={paletteOpen}
            onOpenChange={setPaletteOpen}
            onCommand={handlePaletteCommand}
          />
        </ChatShell>
      </PortProvider>
    </DeploymentProfileProvider>
  );
}
