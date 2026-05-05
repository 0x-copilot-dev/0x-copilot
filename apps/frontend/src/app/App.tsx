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
import {
  clearPendingMcpAuthAction,
  readPendingMcpAuthAction,
  type CompletedMcpAuthAction,
} from "../features/chat/mcpAuthAction";
import { useConnectors } from "../features/connectors/useConnectors";
import {
  SettingsScreen,
  type SettingsSection,
} from "../features/settings/SettingsScreen";
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
  | { screen: "settings"; section: SettingsSection };

const mcpOAuthCompletions = new Map<string, Promise<McpServer>>();
const settingsSections = [
  "profile",
  "appearance",
  "shortcuts",
  "notifications",
  "general",
  "account",
  "capabilities",
  "connectors",
  "skills",
  "claude-code",
] satisfies SettingsSection[];

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

  if (auth.status === "anonymous" || auth.status === "error") {
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
            window.location.pathname === "/login"
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

function routeFromLocation(): AppRoute {
  const path = window.location.pathname.replace(/\/+$/, "") || "/";
  if (path === "/settings") {
    return { screen: "settings", section: "general" };
  }
  if (path.startsWith("/settings/")) {
    const section = decodeURIComponent(path.slice("/settings/".length));
    return {
      screen: "settings",
      section: isSettingsSection(section) ? section : "general",
    };
  }
  return { screen: "chat" };
}

function pathForRoute(route: AppRoute): string {
  if (route.screen === "chat") {
    return "/";
  }
  return route.section === "general"
    ? "/settings"
    : `/settings/${route.section}`;
}

function applyAppRoute(
  route: AppRoute,
  setRoute: (route: AppRoute) => void,
  mode: "push" | "replace" = "push",
): void {
  const path = pathForRoute(route);
  if (window.location.pathname !== path || window.location.search) {
    const method = mode === "replace" ? "replaceState" : "pushState";
    window.history[method]({}, "", path);
  }
  setRoute(route);
}

function isSettingsSection(value: string): value is SettingsSection {
  return settingsSections.includes(value as SettingsSection);
}

function EnterpriseSearchApp({
  identity,
}: {
  identity: RequestIdentity;
}): ReactElement {
  const connectors = useConnectors(identity);
  const skills = useSkills(identity);
  const [route, setRoute] = useState<AppRoute>(() => routeFromLocation());
  const [oauthStatus, setOauthStatus] = useState<string | null>(null);
  const [completedMcpAuthAction, setCompletedMcpAuthAction] =
    useState<CompletedMcpAuthAction | null>(null);

  useEffect(() => {
    function onPopState(): void {
      setRoute(routeFromLocation());
    }

    window.addEventListener("popstate", onPopState);
    return () => {
      window.removeEventListener("popstate", onPopState);
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
        initialSection={route.section}
        onBackToChat={() => applyAppRoute({ screen: "chat" }, setRoute)}
        onSectionChange={(section) =>
          applyAppRoute({ screen: "settings", section }, setRoute)
        }
      />
    );
  }

  return (
    <ChatScreen
      connectors={connectors}
      skills={skills}
      identity={identity}
      onOpenSettings={(section = "general") => {
        applyAppRoute({ screen: "settings", section }, setRoute);
      }}
      oauthStatus={oauthStatus}
      completedMcpAuthAction={completedMcpAuthAction}
    />
  );
}
