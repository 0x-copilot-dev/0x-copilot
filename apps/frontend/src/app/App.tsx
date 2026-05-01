import { ThemeProvider } from "@enterprise-search/design-system";
import type { McpServer } from "@enterprise-search/api-types";
import type { ReactElement } from "react";
import { useEffect, useState } from "react";
import "@enterprise-search/design-system/styles.css";
import "streamdown/styles.css";
import "../styles.css";
import type { RequestIdentity } from "../api/config";
import { completeMcpOAuth } from "../api/mcpApi";
import { getSessionIdentity } from "../api/sessionApi";
import { ChatScreen } from "../features/chat/ChatScreen";
import { useConnectors } from "../features/connectors/useConnectors";
import {
  SettingsScreen,
  type SettingsSection,
} from "../features/settings/SettingsScreen";
import { useSkills } from "../features/skills/useSkills";

type Screen = "chat" | "settings";

const mcpOAuthCompletions = new Map<string, Promise<McpServer>>();

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

function EnterpriseSearchApp(): ReactElement {
  const [identity, setIdentity] = useState<RequestIdentity | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const connectors = useConnectors(identity);
  const skills = useSkills(identity);
  const [screen, setScreen] = useState<Screen>("chat");
  const [settingsSection, setSettingsSection] =
    useState<SettingsSection>("general");
  const [oauthStatus, setOauthStatus] = useState<string | null>(null);

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
    if (window.location.pathname !== "/mcp/oauth/callback") {
      return;
    }
    const params = new URLSearchParams(window.location.search);
    const state = params.get("state");
    const code = params.get("code");
    const oauthError = params.get("error");
    const oauthErrorDescription = params.get("error_description");
    if (!state || (!code && !oauthError)) {
      setOauthStatus(
        "Connector authentication callback was missing state, code, or error.",
      );
      window.history.replaceState({}, "", "/");
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
          setOauthStatus(`${server.display_name} is connected.`);
          setSettingsSection("connectors");
          setScreen("settings");
          await connectors.refresh();
        }
      } catch (err) {
        if (!cancelled) {
          setOauthStatus(
            err instanceof Error
              ? err.message
              : "Connector authentication failed.",
          );
        }
      } finally {
        window.history.replaceState({}, "", "/");
      }
    }

    void finishOAuth();
    return () => {
      cancelled = true;
    };
  }, [connectors.refresh]);

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

  if (screen === "settings") {
    return (
      <SettingsScreen
        connectors={connectors}
        skills={skills}
        initialSection={settingsSection}
        onBackToChat={() => setScreen("chat")}
      />
    );
  }

  return (
    <ChatScreen
      connectors={connectors}
      skills={skills}
      identity={identity}
      onOpenSettings={(section = "general") => {
        setSettingsSection(section);
        setScreen("settings");
      }}
      oauthStatus={oauthStatus}
    />
  );
}
