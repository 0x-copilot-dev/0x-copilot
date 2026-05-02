import type {
  McpOAuthClientConfigRequest,
  McpServer,
  Skill,
  SkillScope,
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
import { useEffect, useState } from "react";
import { authTone } from "../connectors/ConnectorConsentCard";
import type { ConnectorState } from "../connectors/useConnectors";
import type { SkillState } from "../skills/useSkills";

const DEFAULT_SKILL_MARKDOWN = `---
name: custom-workflow
description: Describe what this skill does and when the agent should use it.
allowed_tools: []
---
# Custom Workflow

## When To Use

Use this skill when...

## Workflow

1. Clarify the goal.
2. Gather the required context.
3. Produce the requested output.
`;

export type SettingsSection =
  | "general"
  | "account"
  | "capabilities"
  | "connectors"
  | "skills"
  | "claude-code";

const sections: Array<{ id: SettingsSection; label: string }> = [
  { id: "general", label: "General" },
  { id: "account", label: "Account" },
  { id: "capabilities", label: "Capabilities" },
  { id: "connectors", label: "Connectors" },
  { id: "skills", label: "Skills" },
  { id: "claude-code", label: "Claude Code" },
];

export function SettingsScreen({
  connectors,
  skills,
  initialSection = "general",
  onBackToChat,
  onSectionChange,
}: {
  connectors: ConnectorState;
  skills: SkillState;
  initialSection?: SettingsSection;
  onBackToChat: () => void;
  onSectionChange?: (section: SettingsSection) => void;
}): ReactElement {
  const [activeSection, setActiveSection] =
    useState<SettingsSection>(initialSection);

  useEffect(() => {
    setActiveSection(initialSection);
  }, [initialSection]);

  return (
    <main className="settings-shell">
      <aside className="settings-nav">
        <div className="settings-brand">
          <span className="aui-logo__mark" aria-hidden="true">
            *
          </span>
          <span>assistant-ui</span>
        </div>
        <button
          className="settings-back"
          type="button"
          title="Back to chat"
          onClick={onBackToChat}
        >
          Back to chat
        </button>
        <h1>Settings</h1>
        <nav aria-label="Settings sections">
          {sections.map((section) => (
            <button
              key={section.id}
              className={activeSection === section.id ? "is-active" : undefined}
              type="button"
              title={`Open ${section.label} settings`}
              onClick={() => {
                setActiveSection(section.id);
                onSectionChange?.(section.id);
              }}
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
        {activeSection === "skills" ? <SkillsSettings skills={skills} /> : null}
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
          title="Refresh connectors"
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
          <Button type="submit" disabled={submitting} title="Add connector">
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

function SkillsSettings({ skills }: { skills: SkillState }): ReactElement {
  const [displayName, setDisplayName] = useState("");
  const [scope, setScope] = useState<SkillScope>("user");
  const [markdown, setMarkdown] = useState(DEFAULT_SKILL_MARKDOWN);
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [editingSkillId, setEditingSkillId] = useState<string | null>(null);
  const [editDisplayName, setEditDisplayName] = useState("");
  const [editScope, setEditScope] = useState<SkillScope>("user");
  const [editMarkdown, setEditMarkdown] = useState("");
  const [editingError, setEditingError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    try {
      setFormError(null);
      setSubmitting(true);
      await skills.create({
        markdown,
        displayName: displayName.trim() || undefined,
        enabled: true,
        scope,
      });
      setDisplayName("");
      setScope("user");
      setMarkdown(DEFAULT_SKILL_MARKDOWN);
    } catch (err) {
      setFormError(errorMessage(err, "Could not create skill."));
    } finally {
      setSubmitting(false);
    }
  }

  function beginEdit(skill: Skill): void {
    setEditingSkillId(skill.skill_id);
    setEditDisplayName(skill.display_name);
    setEditScope(skill.scope);
    setEditMarkdown(skill.markdown);
    setEditingError(null);
  }

  async function onSaveEdit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (editingSkillId === null) {
      return;
    }
    try {
      setEditingError(null);
      setSaving(true);
      await skills.update(editingSkillId, {
        display_name: editDisplayName,
        scope: editScope,
        markdown: editMarkdown,
      });
      setEditingSkillId(null);
    } catch (err) {
      setEditingError(errorMessage(err, "Could not update skill."));
    } finally {
      setSaving(false);
    }
  }

  async function onDelete(skillId: string): Promise<void> {
    try {
      setEditingError(null);
      setSaving(true);
      await skills.remove(skillId);
      setEditingSkillId(null);
    } catch (err) {
      setEditingError(errorMessage(err, "Could not delete skill."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="settings-section">
      <div className="settings-section__header">
        <div>
          <h2>Skills</h2>
          <p>Enable preloaded workflows or add customer Skills as markdown.</p>
        </div>
        <Button
          type="button"
          variant="secondary"
          title="Refresh skills"
          onClick={() => void skills.refresh()}
        >
          Refresh
        </Button>
      </div>

      <Card>
        <form
          className="skill-editor-form"
          onSubmit={(event) => void onSubmit(event)}
        >
          <div className="skill-editor-form__row">
            <Field
              label="Display name"
              hint="Optional. Defaults to the markdown name."
            >
              <TextInput
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                placeholder="Customer Handoff"
              />
            </Field>
            <Field label="Scope">
              <Select
                value={scope}
                onChange={(event) => setScope(event.target.value as SkillScope)}
              >
                <option value="user">User</option>
                <option value="org">Organization</option>
              </Select>
            </Field>
          </div>
          <Field
            label="Skill markdown"
            hint="Start with YAML frontmatter containing name and description."
          >
            <textarea
              className="skill-markdown-editor"
              value={markdown}
              onChange={(event) => setMarkdown(event.target.value)}
              spellCheck={false}
            />
          </Field>
          <Button type="submit" disabled={submitting} title="Add skill">
            Add skill
          </Button>
        </form>
        {formError ? <p className="app-error">{formError}</p> : null}
      </Card>

      <div className="connector-settings-list">
        {skills.loading ? (
          <Card>
            <p>Loading skills...</p>
          </Card>
        ) : null}
        {skills.error ? <p className="app-error">{skills.error}</p> : null}
        {!skills.loading && skills.skills.length === 0 ? (
          <Card>
            <p>No skills configured yet.</p>
          </Card>
        ) : null}
        {skills.skills.map((skill) => {
          const isEditing = editingSkillId === skill.skill_id;
          const isPreloaded = skill.source_type === "preloaded";
          return (
            <Card className="connector-settings-row" key={skill.skill_id}>
              <div className="connector-settings-row__main">
                <div>
                  <h3>{skill.display_name}</h3>
                  <p>{skill.description || skill.virtual_path}</p>
                </div>
                <div className="skill-source-badges">
                  <Badge tone={skill.enabled ? "success" : "neutral"}>
                    {skill.enabled ? "enabled" : "disabled"}
                  </Badge>
                  <Badge tone="neutral">{skill.scope}</Badge>
                  <Badge tone="neutral">{skill.source_type}</Badge>
                </div>
              </div>
              <div className="connector-settings-row__controls">
                <Switch
                  label={skill.enabled ? "Enabled" : "Disabled"}
                  checked={skill.enabled}
                  onChange={(event) =>
                    void skills.setEnabled(skill.skill_id, event.target.checked)
                  }
                />
                <span className="settings-meta">
                  Version {skill.version} - {skill.source_type}
                </span>
                <Button
                  type="button"
                  variant="secondary"
                  title={
                    isPreloaded ? "View skill markdown" : "Edit this skill"
                  }
                  onClick={() => beginEdit(skill)}
                >
                  {isPreloaded ? "View markdown" : "Edit"}
                </Button>
              </div>
              {isEditing && isPreloaded ? (
                <div className="skill-editor-form">
                  <Field
                    label="Preloaded markdown"
                    hint="Preloaded Skills are read-only."
                  >
                    <textarea
                      className="skill-markdown-editor"
                      readOnly
                      value={skill.markdown}
                      spellCheck={false}
                    />
                  </Field>
                  <Button
                    type="button"
                    variant="secondary"
                    title="Close markdown viewer"
                    onClick={() => setEditingSkillId(null)}
                  >
                    Close
                  </Button>
                </div>
              ) : null}
              {isEditing && !isPreloaded ? (
                <form
                  className="skill-editor-form"
                  onSubmit={(event) => void onSaveEdit(event)}
                >
                  <div className="skill-editor-form__row">
                    <Field label="Display name">
                      <TextInput
                        value={editDisplayName}
                        onChange={(event) =>
                          setEditDisplayName(event.target.value)
                        }
                      />
                    </Field>
                    <Field label="Scope">
                      <Select
                        value={editScope}
                        onChange={(event) =>
                          setEditScope(event.target.value as SkillScope)
                        }
                      >
                        <option value="user">User</option>
                        <option value="org">Organization</option>
                      </Select>
                    </Field>
                  </div>
                  <Field label="Skill markdown">
                    <textarea
                      className="skill-markdown-editor"
                      value={editMarkdown}
                      onChange={(event) => setEditMarkdown(event.target.value)}
                      spellCheck={false}
                    />
                  </Field>
                  {editingError ? (
                    <p className="app-error">{editingError}</p>
                  ) : null}
                  <div className="skill-row-actions">
                    <Button
                      type="submit"
                      disabled={saving}
                      title="Save skill changes"
                    >
                      Save changes
                    </Button>
                    <Button
                      type="button"
                      variant="secondary"
                      title="Cancel editing skill"
                      onClick={() => setEditingSkillId(null)}
                    >
                      Cancel
                    </Button>
                    <Button
                      type="button"
                      variant="danger"
                      disabled={saving}
                      title="Delete this skill"
                      onClick={() => void onDelete(skill.skill_id)}
                    >
                      Delete
                    </Button>
                  </div>
                </form>
              ) : null}
            </Card>
          );
        })}
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
          title={`Authenticate ${server.display_name}`}
          onClick={() => void connectors.authenticate(server.server_id)}
        >
          Authenticate
        </Button>
        <Button
          type="button"
          variant="ghost"
          title={`Skip authentication for ${server.display_name}`}
          onClick={() => void connectors.skipAuth(server.server_id)}
        >
          Skip auth
        </Button>
        <Button
          type="button"
          variant="danger"
          title={`Remove ${server.display_name}`}
          onClick={() => void connectors.removeServer(server.server_id)}
        >
          Remove
        </Button>
      </div>
    </Card>
  );
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}
