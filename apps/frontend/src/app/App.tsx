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
import { SettingsScreen } from "../features/settings/SettingsScreen";
import { useSkills } from "../features/skills/useSkills";
import { ChatShell } from "@enterprise-search/chat-surface";
import { getAppTransport } from "../api/transport";
import { HashRouter, migrateLegacySettingsPath } from "./HashRouter";
import type { AppRoute } from "./routes";

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
  // Routing goes through the Router port (packages/chat-surface). HashRouter
  // owns every window.history / popstate / hashchange interaction on web;
  // the desktop substrate will swap in its own implementation without any
  // App.tsx changes.
  const [router] = useState(() => new HashRouter());
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
          setOauthStatus(
            err instanceof Error
              ? err.message
              : "Connector authentication failed.",
          );
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
        preferences={preferences}
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
  // Transport and Router via hooks instead of singletons. The transport
  // is a stable module singleton; the router is the local HashRouter
  // instance.
  return (
    <ChatShell transport={getAppTransport()} router={router}>
      {body}
    </ChatShell>
  );
}
