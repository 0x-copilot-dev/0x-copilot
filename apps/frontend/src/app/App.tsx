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
// PR 4.1 — hydrate user profile + preferences once at the shell so the
// Appearance attributes (data-density, data-reduce-motion, theme/accent)
// apply on chat too, not only when Settings is open.
import { AppearanceProvider } from "../features/appearance/AppearanceContext";
import { UserPreferencesProvider } from "../features/me/UserPreferencesContext";
import { useUserProfile } from "../features/me/useUserProfile";
import { UserProfileProvider } from "../features/me/UserProfileContext";
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
  ChatShell,
  DocumentPresenceSignal,
  KeyValueStoreProvider,
  LocalStorageKeyValueStore,
  SecretStorageProvider,
  WebSecretStorage,
  useKeyValueStore,
} from "@enterprise-search/chat-surface";
import { getAppTransport } from "../api/transport";
import { HashRouter, migrateLegacySettingsPath } from "./HashRouter";
import type { AppRoute } from "./routes";
import { errorMessage } from "../utils/errors";

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
}: {
  identity: RequestIdentity;
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
      router.navigate({ screen: "chat" }, { replace: true });
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
            router.navigate({ screen: "chat" }, { replace: true });
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
          router.navigate({ screen: "chat" }, { replace: true });
        }
      }
    }

    void finishOAuth();
    return () => {
      cancelled = true;
    };
  }, [connectors.refresh, identity]);

  let body: ReactElement;
  if (route.screen === "settings") {
    body = (
      <SettingsScreen
        connectors={connectors}
        skills={skills}
        identity={identity}
        profile={profile}
        initialSection={route.section}
        onBackToChat={() => router.navigate({ screen: "chat" })}
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
        onBackToChat={() => router.navigate({ screen: "chat" })}
      />
    );
  } else {
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
    <ChatShell
      transport={getAppTransport()}
      router={router}
      keyValueStore={keyValueStore}
      presenceSignal={presenceSignal}
    >
      <Suspense fallback={<RouteLoadingFallback />}>{body}</Suspense>
    </ChatShell>
  );
}
