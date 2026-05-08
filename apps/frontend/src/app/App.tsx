import { ThemeProvider } from "@enterprise-search/design-system";
import type { McpServer } from "@enterprise-search/api-types";
import type { ReactElement } from "react";
import { useEffect, useState } from "react";
import "@enterprise-search/design-system/styles.css";
import "streamdown/styles.css";
import "../styles.css";
import { decideApproval } from "../api/agentApi";
import type { RequestIdentity } from "../api/config";
import { completeMcpOAuth } from "../api/mcpApi";
import { AuthProvider, useAuth } from "../features/auth/AuthContext";
import { LoginScreen } from "../features/auth/LoginScreen";
import { MfaPrompt } from "../features/auth/MfaPrompt";
import { ChatScreen } from "../features/chat/ChatScreen";
import { ShareScreen } from "../features/share/ShareScreen";
import {
  clearPendingMcpAuthAction,
  readPendingMcpAuthAction,
  type CompletedMcpAuthAction,
} from "../features/chat/mcpAuthAction";
import { useConnectors } from "../features/connectors/useConnectors";
// PR 4.1 — hydrate user profile + preferences once at the shell so the
// Appearance attributes (data-density, data-reduce-motion, theme/accent)
// apply on chat too, not only when Settings is open.
import { useThemeSync } from "../features/me/useThemeSync";
import { useUserPreferences } from "../features/me/useUserPreferences";
import { useUserProfile } from "../features/me/useUserProfile";
import {
  SettingsScreen,
  type SettingsSection,
} from "../features/settings/SettingsScreen";
import {
  DEFAULT_SETTINGS_SECTION,
  SETTINGS_SECTIONS,
  migrateLegacySettingsPath,
} from "../features/settings/useSettingsSection";
import { useSkills } from "../features/skills/useSkills";

/**
 * The org slug LoginScreen falls back to when the URL doesn't carry one.
 * SaaS deploys eventually parse from the subdomain; single-tenant deploys
 * hardcode it via build-time env.
 */
const DEFAULT_ORG_ID =
  (typeof import.meta !== "undefined" &&
    import.meta.env?.VITE_DEFAULT_ORG_ID) ||
  "org_123";

type AppRoute =
  | { screen: "chat" }
  | { screen: "settings"; section: SettingsSection }
  // PR 6.1/6.2 — recipient view of a shared conversation. The token in
  // the URL is the access grant; the AuthGate still requires a logged-in
  // session because v1 keeps shares same-org-only.
  | { screen: "share"; token: string };

const mcpOAuthCompletions = new Map<string, Promise<McpServer>>();

export default function App(): ReactElement {
  return (
    <ThemeProvider defaultScheme="dark">
      <AuthProvider>
        <AuthGate />
      </AuthProvider>
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
    return <MfaPrompt rpId={window.location.hostname} />;
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
        <LoginScreen
          defaultOrgId={DEFAULT_ORG_ID}
          returnTo={
            window.location.pathname === "/login" ||
            window.location.pathname === "/auth/magic-link/callback"
              ? undefined
              : window.location.pathname + window.location.search
          }
        />
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
    <EnterpriseSearchApp
      identity={{
        orgId: auth.identity.org_id,
        userId: auth.identity.user_id,
      }}
    />
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

// PR 4.3 — Settings is path + hash: ``/settings#<section>``. The legacy
// ``/settings/<section>`` form is migrated once on mount via
// ``migrateLegacySettingsPath``; old bookmarks survive without a 404.
// PR 6.1 — ``/share/:token`` deep-links straight to the recipient view.
function routeFromLocation(): AppRoute {
  const path = window.location.pathname.replace(/\/+$/, "") || "/";
  if (path === "/settings") {
    const hash = window.location.hash.replace(/^#/, "");
    if (hash && isSettingsSection(hash)) {
      return { screen: "settings", section: hash };
    }
    return { screen: "settings", section: DEFAULT_SETTINGS_SECTION };
  }
  // Legacy `/settings/<section>` falls through here only briefly between
  // the migrator's ``replaceState`` call and React's first paint. Treat
  // it the same as the modern hash form so the first paint is correct
  // even if the migrator hasn't run yet (e.g. SSR-style hydration).
  if (path.startsWith("/settings/")) {
    const section = decodeURIComponent(path.slice("/settings/".length));
    return {
      screen: "settings",
      section: isSettingsSection(section) ? section : DEFAULT_SETTINGS_SECTION,
    };
  }
  if (path.startsWith("/share/")) {
    // Token in the URL is the access grant; we pass it through verbatim.
    // The recipient endpoint validates it server-side. Empty path
    // segment falls through to ``chat`` (defensive — should not happen
    // because we always emit ``/share/<token>`` from the share popover).
    const token = decodeURIComponent(path.slice("/share/".length));
    if (token) {
      return { screen: "share", token };
    }
  }
  return { screen: "chat" };
}

function pathForRoute(route: AppRoute): { path: string; hash: string } {
  if (route.screen === "chat") {
    return { path: "/", hash: "" };
  }
  if (route.screen === "share") {
    return { path: `/share/${encodeURIComponent(route.token)}`, hash: "" };
  }
  return {
    path: "/settings",
    hash: route.section === DEFAULT_SETTINGS_SECTION ? "" : `#${route.section}`,
  };
}

function applyAppRoute(
  route: AppRoute,
  setRoute: (route: AppRoute) => void,
  mode: "push" | "replace" = "push",
): void {
  const { path, hash } = pathForRoute(route);
  const target = `${path}${hash}`;
  const current = `${window.location.pathname}${window.location.hash}`;
  if (current !== target || window.location.search) {
    const method = mode === "replace" ? "replaceState" : "pushState";
    window.history[method]({}, "", target);
  }
  setRoute(route);
}

function isSettingsSection(value: string): value is SettingsSection {
  return (SETTINGS_SECTIONS as readonly string[]).includes(value);
}

function EnterpriseSearchApp({
  identity,
}: {
  identity: RequestIdentity;
}): ReactElement {
  const connectors = useConnectors(identity);
  const skills = useSkills(identity);
  // PR 4.1 — server-side profile + preferences. One round-trip each at
  // app boot; ``useThemeSync`` mirrors appearance into ThemeProvider +
  // <html> attrs so density/reduce-motion apply globally.
  const profile = useUserProfile();
  const preferences = useUserPreferences();
  useThemeSync(preferences.data);
  const [route, setRoute] = useState<AppRoute>(() => {
    // PR 4.3 — One-shot migration of legacy ``/settings/<section>`` URLs
    // into the hashed form. Runs at most once per session because the
    // migrator's own ``replaceState`` removes the legacy path. We do
    // it inside the lazy initialiser so the first paint already shows
    // the right section.
    migrateLegacySettingsPath();
    return routeFromLocation();
  });
  const [oauthStatus, setOauthStatus] = useState<string | null>(null);
  const [completedMcpAuthAction, setCompletedMcpAuthAction] =
    useState<CompletedMcpAuthAction | null>(null);

  useEffect(() => {
    // PR 4.3 — listen to both popstate (back/forward) and hashchange
    // (URL paste / "Manage" deep-links) so the section state is always
    // a function of the URL.
    function sync(): void {
      setRoute(routeFromLocation());
    }

    window.addEventListener("popstate", sync);
    window.addEventListener("hashchange", sync);
    return () => {
      window.removeEventListener("popstate", sync);
      window.removeEventListener("hashchange", sync);
    };
  }, []);

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
      applyAppRoute({ screen: "chat" }, setRoute, "replace");
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
            applyAppRoute({ screen: "chat" }, setRoute, "replace");
          } else {
            setCompletedMcpAuthAction(null);
            setOauthStatus(`${server.display_name} is connected.`);
            applyAppRoute(
              { screen: "settings", section: "connectors" },
              setRoute,
              "replace",
            );
          }
          await connectors.refresh().catch(() => undefined);
        }
      } catch (err) {
        if (!cancelled) {
          setOauthStatus(
            err instanceof Error
              ? err.message
              : "Connector authentication failed.",
          );
          applyAppRoute({ screen: "chat" }, setRoute, "replace");
        }
      }
    }

    void finishOAuth();
    return () => {
      cancelled = true;
    };
  }, [connectors.refresh, identity]);

  if (route.screen === "settings") {
    return (
      <SettingsScreen
        connectors={connectors}
        skills={skills}
        identity={identity}
        profile={profile}
        preferences={preferences}
        initialSection={route.section}
        onBackToChat={() => applyAppRoute({ screen: "chat" }, setRoute)}
        onSectionChange={(section) =>
          applyAppRoute({ screen: "settings", section }, setRoute)
        }
      />
    );
  }

  if (route.screen === "share") {
    return (
      <ShareScreen
        token={route.token}
        identity={identity}
        onForked={(conversationId) => {
          // After fork, navigate to the chat surface with the new
          // conversation pre-selected. The chat screen reads
          // ?conversationId= and opens that thread.
          window.location.href = `/?conversationId=${encodeURIComponent(conversationId)}`;
        }}
        onBackToChat={() => applyAppRoute({ screen: "chat" }, setRoute)}
      />
    );
  }

  return (
    <ChatScreen
      connectors={connectors}
      skills={skills}
      identity={identity}
      onOpenSettings={(section = "profile") => {
        applyAppRoute({ screen: "settings", section }, setRoute);
      }}
      oauthStatus={oauthStatus}
      completedMcpAuthAction={completedMcpAuthAction}
    />
  );
}
