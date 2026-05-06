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
} from "@enterprise-search/design-system";
import type { FormEvent, ReactElement } from "react";
import { useEffect, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { authTone } from "../connectors/ConnectorConsentCard";
import type { ConnectorState } from "../connectors/useConnectors";
import type { SkillState } from "../skills/useSkills";
import type { RequestIdentity } from "../../api/config";
import type { UserProfileState } from "../me/useUserProfile";
import type { UserPreferencesState } from "../me/useUserPreferences";
// PR 8.1 — ACCOUNT group sections.
import { Appearance } from "./sections/Appearance";
import { Notifications } from "./sections/Notifications";
import { Profile } from "./sections/Profile";
import { Shortcuts } from "./sections/Shortcuts";
import { ApiKeys } from "./sections/ApiKeys";
// PR 8.1 — AI & DATA group sections.
import { ModelAndBehavior } from "./sections/ModelAndBehavior";
import { PrivacyAndData } from "./sections/PrivacyAndData";
import { useWorkspaceDefaults } from "./useWorkspaceDefaults";
// PR 8.1 — WORKSPACE group sections.
import { AuditLogSettings } from "./AuditLogSettings";
import { BillingSettings } from "./BillingSettings";
import { McpOverlay } from "../connectors/mcp/McpOverlay";
import { MembersSettings } from "./MembersSettings";
import { WorkspaceSettings } from "./WorkspaceSettings";
// PR 8.1 — top chrome reads workspace name + member count.
import { useWorkspace, useWorkspaceMembers } from "./useWorkspace";
import "./workspace.css";

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
  // PR 8.1 — ACCOUNT group (per-user identity + appearance + shortcuts +
  // personal API keys). Lands first so users find their own settings.
  | "profile"
  | "appearance"
  | "shortcuts"
  | "api-keys"
  // PR 8.1 — WORKSPACE group (admin / shared surfaces).
  | "workspace"
  | "members"
  | "billing"
  | "audit-log"
  // PR 8.1 — AI & DATA group (agent behavior + sources).
  | "model-and-behavior"
  | "connectors"
  | "skills"
  | "privacy-data"
  // PR 8.1 — NOTIFICATIONS (single section, kept as its own group to
  // match the design bundle's IA).
  | "notifications";

// PR 8.1 — `RailEntry` carries the icon glyph + an optional badge so the
// rail rows visually match the Atlas design (icon + label + count /
// "Admin" tag). Group entries are heading rows the user can't click.
type RailIcon =
  | "user"
  | "sun"
  | "command"
  | "key"
  | "building"
  | "users"
  | "card"
  | "doc"
  | "spark"
  | "link"
  | "book"
  | "shield"
  | "bell";

type RailEntry =
  | { kind: "group"; label: string }
  | {
      kind: "section";
      id: SettingsSection;
      label: string;
      icon: RailIcon;
      /**
       * Optional badge override. When omitted the rail computes a sensible
       * default at render time (member count, connector count, etc.).
       */
      badge?: string;
      /**
       * Slug for the data-driven badge resolver. `null` means no badge,
       * a string keys into the runtime count map below.
       */
      countKey?: "members" | "connectors" | "skills" | null;
      /** Show the static "Admin" pill — purely cosmetic; backend still gates. */
      adminPill?: boolean;
    };

const railSections: ReadonlyArray<RailEntry> = [
  { kind: "group", label: "Account" },
  { kind: "section", id: "profile", label: "Profile", icon: "user" },
  { kind: "section", id: "appearance", label: "Appearance", icon: "sun" },
  { kind: "section", id: "shortcuts", label: "Shortcuts", icon: "command" },
  { kind: "section", id: "api-keys", label: "API keys", icon: "key" },
  { kind: "group", label: "Workspace" },
  {
    kind: "section",
    id: "workspace",
    label: "Workspace",
    icon: "building",
    adminPill: true,
  },
  {
    kind: "section",
    id: "members",
    label: "Members & roles",
    icon: "users",
    countKey: "members",
  },
  {
    kind: "section",
    id: "billing",
    label: "Billing & usage",
    icon: "card",
  },
  {
    kind: "section",
    id: "audit-log",
    label: "Audit log",
    icon: "doc",
    adminPill: true,
  },
  { kind: "group", label: "AI & data" },
  {
    kind: "section",
    id: "model-and-behavior",
    label: "Model & behavior",
    icon: "spark",
  },
  {
    kind: "section",
    id: "connectors",
    label: "Connectors",
    icon: "link",
    countKey: "connectors",
  },
  {
    kind: "section",
    id: "skills",
    label: "Skills",
    icon: "book",
    countKey: "skills",
  },
  {
    kind: "section",
    id: "privacy-data",
    label: "Privacy & data",
    icon: "shield",
  },
  { kind: "group", label: "Notifications" },
  {
    kind: "section",
    id: "notifications",
    label: "Notifications",
    icon: "bell",
  },
];

export function SettingsScreen({
  connectors,
  skills,
  identity,
  profile,
  preferences,
  initialSection = "profile",
  dataResidency,
  onBackToChat,
  onSectionChange,
}: {
  connectors: ConnectorState;
  skills: SkillState;
  /**
   * PR 4.3 — caller's identity for the "AI & data" sections that read
   * directly from agentApi (workspace defaults, retention/effective,
   * export, delete-all). Optional so existing callers that don't need
   * those sections continue to compile.
   */
  identity?: RequestIdentity;
  /**
   * Hydrated user profile + preferences from the app shell.
   * Optional so legacy callers that mount SettingsScreen without
   * threading these (tests, storybook) keep working — the affected
   * sections render a soft-disabled state when the state is absent.
   */
  profile?: UserProfileState;
  preferences?: UserPreferencesState;
  initialSection?: SettingsSection;
  /**
   * Read-only deployment region label rendered in the Privacy & data
   * panel. Sourced from deploy config; ``null`` ⇒ "Not configured".
   */
  dataResidency?: string | null;
  onBackToChat: () => void;
  onSectionChange?: (section: SettingsSection) => void;
}): ReactElement {
  const [activeSection, setActiveSection] =
    useState<SettingsSection>(initialSection);

  useEffect(() => {
    setActiveSection(initialSection);
  }, [initialSection]);

  // Single hydration of workspace defaults for both Model & behavior and
  // Privacy & data. The hook tolerates being called without an identity
  // (returns nulls) so legacy callers without identity threading don't
  // break.
  const workspaceDefaults = useWorkspaceDefaults(identity ?? FALLBACK_IDENTITY);

  // Admin gating for the Workspace group. Backend enforces via the
  // ``admin:users`` permission scope; the client check hides controls
  // ahead of the round-trip. Members see the panels in read-only mode.
  const auth = useAuth();
  const isAdmin =
    auth.identity?.permission_scopes?.includes("admin:users") ?? false;

  // PR 8.1 — top chrome data + rail badges. Both hooks tolerate the
  // fallback identity and short-circuit to a null/empty state when the
  // org/user are blank, so legacy mounts still render.
  const workspace = useWorkspace(identity ?? FALLBACK_IDENTITY);
  const members = useWorkspaceMembers(identity ?? FALLBACK_IDENTITY);
  const counts = {
    members: members.members.length,
    connectors: connectors.servers.length,
    skills: skills.skills.length,
  };

  function handlePick(id: SettingsSection): void {
    setActiveSection(id);
    onSectionChange?.(id);
  }

  return (
    <main className="settings-shell">
      <SettingsTopChrome
        workspaceName={workspace.workspace?.display_name ?? null}
        userEmail={profile?.data?.email ?? null}
        onBack={onBackToChat}
        onJumpConnectors={() => handlePick("connectors")}
      />
      <div className="settings-shell__body">
        <aside className="settings-nav" aria-label="Settings sections">
          {railSections.map((entry, index) =>
            entry.kind === "group" ? (
              <div
                key={`group-${index}`}
                className="settings-nav-group"
                aria-hidden="true"
              >
                {entry.label}
              </div>
            ) : (
              <RailRow
                key={entry.id}
                entry={entry}
                active={activeSection === entry.id}
                count={entry.countKey ? counts[entry.countKey] : null}
                onPick={handlePick}
              />
            ),
          )}
        </aside>
        <section className="settings-content">
          {activeSection === "profile" && profile ? (
            <Profile profile={profile} />
          ) : null}
          {activeSection === "appearance" && preferences && profile ? (
            <Appearance preferences={preferences} profile={profile} />
          ) : null}
          {activeSection === "shortcuts" && preferences ? (
            <Shortcuts preferences={preferences} />
          ) : null}
          {activeSection === "notifications" && preferences ? (
            <Notifications preferences={preferences} />
          ) : null}
          {activeSection === "api-keys" ? <ApiKeys /> : null}
          {activeSection === "workspace" && identity ? (
            <WorkspaceSettings identity={identity} isAdmin={isAdmin} />
          ) : null}
          {activeSection === "members" && identity ? (
            <MembersSettings identity={identity} isAdmin={isAdmin} />
          ) : null}
          {activeSection === "billing" && identity ? (
            <BillingSettings identity={identity} />
          ) : null}
          {activeSection === "audit-log" && identity ? (
            <AuditLogSettings identity={identity} isAdmin={isAdmin} />
          ) : null}
          {activeSection === "model-and-behavior" ? (
            <ModelAndBehavior workspaceDefaults={workspaceDefaults} />
          ) : null}
          {activeSection === "connectors" ? (
            <ConnectorsSettings connectors={connectors} />
          ) : null}
          {activeSection === "skills" ? (
            <SkillsSettings skills={skills} />
          ) : null}
          {activeSection === "privacy-data" && identity !== undefined ? (
            <PrivacyAndData
              identity={identity}
              workspaceDefaults={workspaceDefaults}
              dataResidency={dataResidency}
            />
          ) : null}
        </section>
      </div>
    </main>
  );
}

function SettingsTopChrome({
  workspaceName,
  userEmail,
  onBack,
  onJumpConnectors,
}: {
  workspaceName: string | null;
  userEmail: string | null;
  onBack: () => void;
  onJumpConnectors: () => void;
}): ReactElement {
  const initial = userEmail ? userEmail.charAt(0).toUpperCase() : "·";
  return (
    <header className="settings-chrome" role="banner">
      <button
        type="button"
        className="settings-chrome__back"
        onClick={onBack}
        title="Back to chat"
      >
        <RailGlyph name="back" />
        <span>Back to Atlas</span>
      </button>
      <div className="settings-chrome__crumb" aria-live="polite">
        Settings
        {workspaceName ? (
          <>
            <span className="settings-chrome__crumb-sep" aria-hidden="true">
              ·
            </span>
            <strong>{workspaceName}</strong>
          </>
        ) : null}
      </div>
      <div className="settings-chrome__right">
        <button
          type="button"
          className="settings-chrome__shortcut"
          onClick={onJumpConnectors}
          title="Manage MCP servers"
        >
          <RailGlyph name="link" />
          <span>Manage MCP servers</span>
        </button>
        <div className="settings-chrome__user" aria-label="Signed-in user">
          <span className="settings-chrome__avatar" aria-hidden="true">
            {initial}
          </span>
          <span className="settings-chrome__email">
            {userEmail ?? "Signed in"}
          </span>
        </div>
      </div>
    </header>
  );
}

function RailRow({
  entry,
  active,
  count,
  onPick,
}: {
  entry: Extract<RailEntry, { kind: "section" }>;
  active: boolean;
  count: number | null;
  onPick: (id: SettingsSection) => void;
}): ReactElement {
  // Don't render zero-count badges for hooks still loading or empty
  // collections — only show the count chip when there's something to
  // count. Avoids "Connectors 0" before connectors hydrate.
  const badge =
    entry.badge ?? (count !== null && count > 0 ? String(count) : null);
  return (
    <button
      className={active ? "settings-nav__row is-active" : "settings-nav__row"}
      type="button"
      title={`Open ${entry.label} settings`}
      onClick={() => onPick(entry.id)}
    >
      <span className="settings-nav__icon" aria-hidden="true">
        <RailGlyph name={entry.icon} />
      </span>
      <span className="settings-nav__label">{entry.label}</span>
      {entry.adminPill ? (
        <span className="settings-nav__badge settings-nav__badge--admin">
          Admin
        </span>
      ) : badge ? (
        <span className="settings-nav__badge">{badge}</span>
      ) : null}
    </button>
  );
}

/**
 * Inline glyphs for the rail + chrome. The design system doesn't ship
 * an icon set today; rather than introduce one for a single surface we
 * inline the strokes here in the same style the design bundle used.
 * Stroke `currentColor` so the active-row colour change picks up
 * automatically.
 */
function RailGlyph({ name }: { name: RailIcon | "back" }): ReactElement | null {
  const common = {
    width: 16,
    height: 16,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.6,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  switch (name) {
    case "back":
      return (
        <svg {...common}>
          <path d="M15 6l-6 6 6 6" />
        </svg>
      );
    case "user":
      return (
        <svg {...common}>
          <circle cx="12" cy="8" r="4" />
          <path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6" />
        </svg>
      );
    case "sun":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5" />
        </svg>
      );
    case "command":
      return (
        <svg {...common}>
          <path d="M9 6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3z" />
        </svg>
      );
    case "key":
      return (
        <svg {...common}>
          <circle cx="8" cy="14" r="4" />
          <path d="M11 12l9-9 2 2-2 2 2 2-2 2-2-2-3 3" />
        </svg>
      );
    case "building":
      return (
        <svg {...common}>
          <path d="M4 21V5a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v16" />
          <path d="M15 9h3a2 2 0 0 1 2 2v10" />
          <path d="M8 7h2M8 11h2M8 15h2" />
        </svg>
      );
    case "users":
      return (
        <svg {...common}>
          <circle cx="9" cy="8" r="3.5" />
          <path d="M2 20c1-3.5 3.5-5 7-5s6 1.5 7 5" />
          <circle cx="17" cy="9" r="2.5" />
          <path d="M22 18c-.5-2-2-3-4-3" />
        </svg>
      );
    case "card":
      return (
        <svg {...common}>
          <rect x="3" y="6" width="18" height="13" rx="2" />
          <path d="M3 10h18" />
        </svg>
      );
    case "doc":
      return (
        <svg {...common}>
          <path d="M6 3h8l4 4v14H6z" />
          <path d="M14 3v4h4" />
          <path d="M9 13h6M9 17h6" />
        </svg>
      );
    case "spark":
      return (
        <svg {...common}>
          <path d="M12 3l1.7 4.3L18 9l-4.3 1.7L12 15l-1.7-4.3L6 9l4.3-1.7z" />
          <path d="M19 15l.6 1.4L21 17l-1.4.6L19 19l-.6-1.4L17 17l1.4-.6z" />
        </svg>
      );
    case "link":
      return (
        <svg {...common}>
          <path d="M10 13a4 4 0 0 0 5.7 0l3-3a4 4 0 1 0-5.7-5.7l-1 1" />
          <path d="M14 11a4 4 0 0 0-5.7 0l-3 3a4 4 0 1 0 5.7 5.7l1-1" />
        </svg>
      );
    case "book":
      return (
        <svg {...common}>
          <path d="M4 5a2 2 0 0 1 2-2h12v15H6a2 2 0 0 0-2 2z" />
          <path d="M4 18a2 2 0 0 1 2-2h12" />
          <path d="M8 7h7" />
        </svg>
      );
    case "shield":
      return (
        <svg {...common}>
          <path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" />
        </svg>
      );
    case "bell":
      return (
        <svg {...common}>
          <path d="M6 16V11a6 6 0 1 1 12 0v5l1.5 2H4.5z" />
          <path d="M10 21a2 2 0 0 0 4 0" />
        </svg>
      );
    default:
      return null;
  }
}

// PR 4.3 — fallback identity used only by legacy callers that mount
// SettingsScreen without threading identity. The "AI & data" sections
// that need real network calls render an error/loading state when
// the org/user are blank, so this is safe.
const FALLBACK_IDENTITY: RequestIdentity = {
  orgId: "",
  userId: "",
};

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
  // PR 4.4 — catalog wizard. Opens from the section header button; the
  // existing custom-URL form below stays for power users who already
  // know the endpoint they're connecting to.
  const [mcpOverlayOpen, setMcpOverlayOpen] = useState(false);

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
        <div className="settings-section__header-actions">
          <Button
            type="button"
            variant="primary"
            onClick={() => setMcpOverlayOpen(true)}
          >
            Browse catalog
          </Button>
          <Button
            type="button"
            variant="secondary"
            title="Refresh connectors"
            onClick={() => void connectors.refresh()}
          >
            Refresh
          </Button>
        </div>
      </div>

      <McpOverlay
        open={mcpOverlayOpen}
        onClose={() => setMcpOverlayOpen(false)}
        connectors={connectors}
      />

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
          const isSystem = skill.source_type === "system";
          // System skills are runtime infrastructure (e.g. search-subagent-logs).
          // They cannot be disabled or edited — disabling would break the
          // supervisor's ability to fulfil the protocol the skill defines.
          const isReadOnly = isPreloaded || isSystem;
          const readOnlyHint = isSystem
            ? "System Skills are required for runtime functionality and cannot be disabled."
            : "Preloaded Skills are read-only.";
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
                  <Badge tone={isSystem ? "accent" : "neutral"}>
                    {skill.source_type}
                  </Badge>
                </div>
              </div>
              <div className="connector-settings-row__controls">
                {isSystem ? (
                  <span className="settings-meta">Always on</span>
                ) : (
                  <Switch
                    label={skill.enabled ? "Enabled" : "Disabled"}
                    checked={skill.enabled}
                    onChange={(event) =>
                      void skills.setEnabled(
                        skill.skill_id,
                        event.target.checked,
                      )
                    }
                  />
                )}
                <span className="settings-meta">
                  Version {skill.version} - {skill.source_type}
                </span>
                <Button
                  type="button"
                  variant="secondary"
                  title={isReadOnly ? "View skill markdown" : "Edit this skill"}
                  onClick={() => beginEdit(skill)}
                >
                  {isReadOnly ? "View markdown" : "Edit"}
                </Button>
              </div>
              {isEditing && isReadOnly ? (
                <div className="skill-editor-form">
                  <Field
                    label={isSystem ? "System markdown" : "Preloaded markdown"}
                    hint={readOnlyHint}
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
              {isEditing && !isReadOnly ? (
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
