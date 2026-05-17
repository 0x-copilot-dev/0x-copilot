import { ThemeProvider } from "@enterprise-search/design-system";
import type { McpServer } from "@enterprise-search/api-types";
import type { ReactElement } from "react";
import { Suspense, lazy, useEffect, useState } from "react";
import "@enterprise-search/design-system/styles.css";
import "streamdown/styles.css";
import "../styles.css";
import { decideApproval } from "../api/agentApi";
import type { RequestIdentity } from "../api/config";
import { completeMcpOAuth } from "../api/mcpApi";
import { AuthProvider, useAuth } from "../features/auth/AuthContext";
import {
  clearPendingMcpAuthAction,
  readPendingMcpAuthAction,
  type CompletedMcpAuthAction,
} from "../features/chat/mcpAuthAction";
import { useConnectors } from "../features/connectors/useConnectors";
import { HomeRoute } from "../features/home/HomeRoute";
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
  AgentsDestination,
  ChatShell,
  ConnectorsDestination,
  DocumentPresenceSignal,
  InboxDestination,
  KeyValueStoreProvider,
  LibraryDestination,
  LocalStorageKeyValueStore,
  MemoryDestination,
  ProjectsDestination,
  SecretStorageProvider,
  TeamDestination,
  TodosDestination,
  ToolsDestination,
  WebSecretStorage,
  useKeyValueStore,
  type ArtifactRoute,
  type ShellDestinationSlug,
} from "@enterprise-search/chat-surface";
import { getAppTransport } from "../api/transport";
import { HashRouter, migrateLegacySettingsPath } from "./HashRouter";
import { ROOT_DESTINATION, type AppRoute } from "./routes";
import { errorMessage } from "../utils/errors";
import {
  PortProvider,
  WebBadgePort,
  WebClipboardPort,
  WebFilePickerPort,
  WebNotificationPort,
  type PortBundle,
} from "../ports";

// Map every non-chats, non-home destination slug to the placeholder
// component shipped with the chat-surface package. Chats has a
// dedicated host component (`ChatScreen`) below; Home has a
// feature-level data binder (`HomeRoute`) that mounts the package
// component itself — adding either here would only confuse the
// renderer.
const NON_CHATS_DESTINATIONS: Readonly<
  Record<Exclude<ShellDestinationSlug, "chats" | "home">, () => ReactElement>
> = {
  agents: AgentsDestination,
  library: LibraryDestination,
  inbox: InboxDestination,
  tools: ToolsDestination,
  projects: ProjectsDestination,
  todos: TodosDestination,
  connectors: ConnectorsDestination,
  team: TeamDestination,
  memory: MemoryDestination,
};

/**
 * The org slug LoginScreen falls back to when the URL doesn't carry one.
 * SaaS deploys eventually parse from the subdomain; single-tenant deploys
 * hardcode it via build-time env.
 */
const DEFAULT_ORG_ID =
  (typeof import.meta !== "undefined" &&
    import.meta.env?.VITE_DEFAULT_ORG_ID) ||
  "org_123";

const mcpOAuthCompletions = new Map<string, Promise<McpServer>>();

export default function App(): ReactElement {
  // Construct the substrate-side KeyValueStore here (not inside
  // EnterpriseSearchApp) so AuthProvider's `useKeyValueStore()` resolves
  // to the real store. AuthProvider sits above EnterpriseSearchApp; if
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
      <KeyValueStoreProvider store={keyValueStore}>
        <SecretStorageProvider store={secretStorage}>
          <AuthProvider>
            <AuthGate />
          </AuthProvider>
        </SecretStorageProvider>
      </KeyValueStoreProvider>
    </ThemeProvider>
  );
}

/**
 * Gates the app behind the AuthContext state machine. ``initial`` /
 * ``loading`` show the boot spinner; ``anonymous`` / ``error`` route to
 * the login screen; ``mfa_pending`` to the MFA prompt; only
 * ``authenticated`` renders the actual app shell.
 *
 * Lives here (rather than inside ``EnterpriseSearchApp``) so the rest of
 * the app continues to assume identity is non-null — same invariant the
 * pre-A9 code relied on.
 */
function AuthGate(): ReactElement {
  const auth = useAuth();

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
          <EnterpriseSearchApp
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

function EnterpriseSearchApp({
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
  // PresenceSignal is local to EnterpriseSearchApp — AuthProvider doesn't
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

  useEffect(() => router.subscribe(setRoute), [router]);

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

  const handleRailNavigate = (slug: ShellDestinationSlug): void => {
    router.navigate({ screen: "chat", destination: slug });
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
  } else if (route.destination === "chats") {
    // Chats keeps the legacy host-side ChatScreen — it owns its own
    // thread sidebar + composer, and the chat-surface package's
    // ChatsDestination is only a placeholder. ChatShell hides the
    // ContextPanel column for chats (full-bleed), so there is exactly
    // one rail + one thread sidebar + one main pane (+ right rail).
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
  } else if (route.destination === "home") {
    // Phase 2 P2-C — Home gets a feature-level data binder
    // (`HomeRoute`) that owns the `/v1/home` fetch, the
    // per-user `home.activity_window_hours` KV setting, and
    // the `/v1/home/stream` SSE subscription. The presentational
    // `<HomeDestination>` (and its sibling `<HomePanel>` in the
    // ContextPanel slot — landed by P2-B1) renders inside.
    // TODO(merge): once P2-B1's <HomePanel> is exported from
    // `@enterprise-search/chat-surface`, mount it via ChatShell's
    // ContextPanel slot.
    body = (
      <section
        data-testid="destination-outlet"
        data-destination="home"
        style={{ height: "100%", overflow: "auto" }}
        aria-label="home destination"
      >
        <HomeRoute identity={identity} />
      </section>
    );
  } else {
    // Every other destination renders the package-shipped component.
    // These are placeholder surfaces today; wiring real data fetchers
    // is the next agent's job. The mapping itself is `route.destination`
    // → `NON_CHATS_DESTINATIONS[destination]` — single source of truth.
    const Destination = NON_CHATS_DESTINATIONS[route.destination];
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
      >
        <Suspense fallback={<RouteLoadingFallback />}>{body}</Suspense>
      </ChatShell>
    </PortProvider>
  );
}
