import type {
  McpOAuthClientConfigRequest,
  McpServer,
} from "@enterprise-search/api-types";
import {
  Badge,
  Button,
  Card,
  Field,
  Select,
  Switch,
  TextInput,
  useTheme,
  type ThemeScheme,
} from "@enterprise-search/design-system";
import type { FormEvent, ReactElement } from "react";
import { useState } from "react";
import { authTone } from "../connectors/ConnectorConsentCard";
import type { ConnectorState } from "../connectors/useConnectors";

type SettingsSection =
  | "general"
  | "account"
  | "capabilities"
  | "connectors"
  | "claude-code";

const sections: Array<{ id: SettingsSection; label: string }> = [
  { id: "general", label: "General" },
  { id: "account", label: "Account" },
  { id: "capabilities", label: "Capabilities" },
  { id: "connectors", label: "Connectors" },
  { id: "claude-code", label: "Claude Code" },
];

export function SettingsScreen({
  connectors,
  onBackToChat,
}: {
  connectors: ConnectorState;
  onBackToChat: () => void;
}): ReactElement {
  const [activeSection, setActiveSection] =
    useState<SettingsSection>("connectors");

  return (
    <main className="settings-shell">
      <aside className="settings-nav">
        <button className="settings-back" type="button" onClick={onBackToChat}>
          Back to chat
        </button>
        <h1>Settings</h1>
        <nav aria-label="Settings sections">
          {sections.map((section) => (
            <button
              key={section.id}
              className={activeSection === section.id ? "is-active" : undefined}
              type="button"
              onClick={() => setActiveSection(section.id)}
            >
              {section.label}
            </button>
          ))}
        </nav>
      </aside>
      <section className="settings-content">
        {activeSection === "general" ? <GeneralSettings /> : null}
        {activeSection === "account" ? (
          <PlaceholderSettings title="Account" />
        ) : null}
        {activeSection === "capabilities" ? (
          <PlaceholderSettings
            title="Capabilities"
            body="Agent capabilities are driven by enabled connectors for this milestone."
          />
        ) : null}
        {activeSection === "connectors" ? (
          <ConnectorsSettings connectors={connectors} />
        ) : null}
        {activeSection === "claude-code" ? (
          <PlaceholderSettings
            title="Claude Code"
            body="Claude Code style settings can live here later without changing connector management."
          />
        ) : null}
      </section>
    </main>
  );
}

function GeneralSettings(): ReactElement {
  const { scheme, setScheme } = useTheme();

  return (
    <div className="settings-section">
      <h2>General</h2>
      <Card>
        <Field
          label="Color scheme"
          hint="Theme tokens update the whole UI kit."
        >
          <Select
            value={scheme}
            onChange={(event) => setScheme(event.target.value as ThemeScheme)}
          >
            <option value="dark">Dark</option>
            <option value="light">Light</option>
            <option value="slate">Slate</option>
          </Select>
        </Field>
      </Card>
    </div>
  );
}

function PlaceholderSettings({
  title,
  body = "This section is intentionally light for now. Privacy and billing are out of scope.",
}: {
  title: string;
  body?: string;
}): ReactElement {
  return (
    <div className="settings-section">
      <h2>{title}</h2>
      <Card>
        <p>{body}</p>
      </Card>
    </div>
  );
}

function ConnectorsSettings({
  connectors,
}: {
  connectors: ConnectorState;
}): ReactElement {
  const [url, setUrl] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [scope, setScope] = useState("");
  const [authorizationEndpoint, setAuthorizationEndpoint] = useState("");
  const [tokenEndpoint, setTokenEndpoint] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!url.trim()) {
      return;
    }
    try {
      const oauthClient = oauthClientFromForm({
        clientId,
        clientSecret,
        scope,
        authorizationEndpoint,
        tokenEndpoint,
      });
      setFormError(null);
      setSubmitting(true);
      await connectors.addServer(url.trim(), oauthClient);
      setUrl("");
      setClientId("");
      setClientSecret("");
      setScope("");
      setAuthorizationEndpoint("");
      setTokenEndpoint("");
    } catch (err) {
      setFormError(
        err instanceof Error ? err.message : "Could not add connector.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="settings-section">
      <div className="settings-section__header">
        <div>
          <h2>Connectors</h2>
          <p>
            Allow the agent to reference other apps and services only after
            explicit consent.
          </p>
        </div>
        <Button
          type="button"
          variant="secondary"
          onClick={() => void connectors.refresh()}
        >
          Refresh
        </Button>
      </div>

      <Card>
        <form
          className="connector-add-form"
          onSubmit={(event) => void onSubmit(event)}
        >
          <Field
            label="Add custom connector"
            hint="For OAuth MCP servers without dynamic client registration, add a pre-registered OAuth client below."
          >
            <TextInput
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              placeholder="https://mcp.example.com"
            />
          </Field>
          <Field label="OAuth client ID">
            <TextInput
              value={clientId}
              onChange={(event) => setClientId(event.target.value)}
              placeholder="Optional client_id"
            />
          </Field>
          <Field label="OAuth client secret">
            <TextInput
              type="password"
              value={clientSecret}
              onChange={(event) => setClientSecret(event.target.value)}
              placeholder="Optional client_secret"
            />
          </Field>
          <Field label="OAuth scope">
            <TextInput
              value={scope}
              onChange={(event) => setScope(event.target.value)}
              placeholder="Optional, for example: mcp"
            />
          </Field>
          <Field
            label="Authorization endpoint"
            hint="Optional advanced override when the server does not advertise OAuth metadata."
          >
            <TextInput
              value={authorizationEndpoint}
              onChange={(event) => setAuthorizationEndpoint(event.target.value)}
              placeholder="https://auth.example.com/authorize"
            />
          </Field>
          <Field label="Token endpoint" hint="Optional advanced override.">
            <TextInput
              value={tokenEndpoint}
              onChange={(event) => setTokenEndpoint(event.target.value)}
              placeholder="https://auth.example.com/token"
            />
          </Field>
          <Button type="submit" disabled={submitting}>
            Add connector
          </Button>
        </form>
        {formError ? <p className="app-error">{formError}</p> : null}
        {connectors.error ? (
          <p className="app-error">{connectors.error}</p>
        ) : null}
      </Card>

      <div className="connector-settings-list">
        {connectors.loading ? (
          <Card>
            <p>Loading connectors...</p>
          </Card>
        ) : null}
        {!connectors.loading && connectors.servers.length === 0 ? (
          <Card>
            <p>No connectors configured yet.</p>
          </Card>
        ) : null}
        {connectors.servers.map((server) => (
          <ConnectorSettingsRow
            key={server.server_id}
            server={server}
            connectors={connectors}
          />
        ))}
      </div>
    </div>
  );
}

function oauthClientFromForm({
  clientId,
  clientSecret,
  scope,
  authorizationEndpoint,
  tokenEndpoint,
}: {
  clientId: string;
  clientSecret: string;
  scope: string;
  authorizationEndpoint: string;
  tokenEndpoint: string;
}): McpOAuthClientConfigRequest | undefined {
  const trimmedClientId = clientId.trim();
  const trimmedClientSecret = clientSecret.trim();
  const trimmedScope = scope.trim();
  const trimmedAuthorizationEndpoint = authorizationEndpoint.trim();
  const trimmedTokenEndpoint = tokenEndpoint.trim();
  const hasOAuthConfig = [
    trimmedClientId,
    trimmedClientSecret,
    trimmedScope,
    trimmedAuthorizationEndpoint,
    trimmedTokenEndpoint,
  ].some(Boolean);
  if (!hasOAuthConfig) {
    return undefined;
  }
  if (!trimmedClientId) {
    throw new Error(
      "OAuth client ID is required when OAuth settings are provided.",
    );
  }
  return {
    client_id: trimmedClientId,
    ...(trimmedClientSecret
      ? {
          client_secret: trimmedClientSecret,
          token_endpoint_auth_method: "client_secret_post",
        }
      : { token_endpoint_auth_method: "none" }),
    ...(trimmedScope ? { scope: trimmedScope } : {}),
    ...(trimmedAuthorizationEndpoint
      ? { authorization_endpoint: trimmedAuthorizationEndpoint }
      : {}),
    ...(trimmedTokenEndpoint ? { token_endpoint: trimmedTokenEndpoint } : {}),
  };
}

function ConnectorSettingsRow({
  server,
  connectors,
}: {
  server: McpServer;
  connectors: ConnectorState;
}): ReactElement {
  return (
    <Card className="connector-settings-row">
      <div className="connector-settings-row__main">
        <div>
          <h3>{server.display_name}</h3>
          <p>{server.url}</p>
        </div>
        <Badge tone={authTone(server.auth_state)}>
          {server.auth_state.replaceAll("_", " ")}
        </Badge>
      </div>
      <div className="connector-settings-row__controls">
        <Switch
          label={server.enabled ? "Enabled" : "Disabled"}
          checked={server.enabled}
          onChange={(event) =>
            void connectors.setEnabled(server.server_id, event.target.checked)
          }
        />
        <Button
          type="button"
          variant="secondary"
          onClick={() => void connectors.authenticate(server.server_id)}
        >
          Authenticate
        </Button>
        <Button
          type="button"
          variant="ghost"
          onClick={() => void connectors.skipAuth(server.server_id)}
        >
          Skip auth
        </Button>
        <Button
          type="button"
          variant="danger"
          onClick={() => void connectors.removeServer(server.server_id)}
        >
          Remove
        </Button>
      </div>
    </Card>
  );
}
