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
import { getSessionIdentity } from "../api/sessionApi";
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

type AppRoute =
  | { screen: "chat" }
  | { screen: "settings"; section: SettingsSection };

const mcpOAuthCompletions = new Map<string, Promise<McpServer>>();
const settingsSections = [
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
      <EnterpriseSearchApp />
    </ThemeProvider>
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

function EnterpriseSearchApp(): ReactElement {
  const [identity, setIdentity] = useState<RequestIdentity | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const connectors = useConnectors(identity);
  const skills = useSkills(identity);
  const [route, setRoute] = useState<AppRoute>(() => routeFromLocation());
  const [oauthStatus, setOauthStatus] = useState<string | null>(null);
  const [completedMcpAuthAction, setCompletedMcpAuthAction] =
    useState<CompletedMcpAuthAction | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function loadSession(): Promise<void> {
      try {
        const nextIdentity = await getSessionIdentity();
        if (!cancelled) {
          setIdentity(nextIdentity);
          setSessionError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setSessionError(
            err instanceof Error
              ? err.message
              : "Could not load session identity.",
          );
        }
      }
    }

    void loadSession();
    return () => {
      cancelled = true;
    };
  }, []);

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
    if (
      window.location.pathname !== "/mcp/oauth/callback" ||
      identity === null
    ) {
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

  if (sessionError !== null) {
    return (
      <main className="app-loading">
        <p>{sessionError}</p>
      </main>
    );
  }

  if (identity === null) {
    return (
      <main className="app-loading">
        <p>Loading session...</p>
      </main>
    );
  }

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
