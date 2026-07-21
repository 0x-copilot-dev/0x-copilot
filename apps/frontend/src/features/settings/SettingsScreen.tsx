// Legacy Settings shell.
//
// Responsibilities kept here (and ONLY these): resolve the shell-level data the
// sections share (local-models gate probe, workspace defaults, admin flag,
// workspace + member counts), render the nav rail, and dispatch the active
// section to its body. Everything else was extracted:
//   • the routing type + nav descriptor → `./settingsSections`
//   • the top chrome + rail rows + glyphs → `./SettingsNav`
//   • the Connectors sub-feature        → `./sections/ConnectorsSettings`
//   • the Skills sub-feature            → `./sections/SkillsSettings`
//
// NOTE: on web this screen now serves ONLY the `connectors` + `skills` sections
// (App.tsx routes every other section to the converged chat-surface
// `SettingsSurface` via `SettingsBinder`). It stays mounted because those two
// sub-features have no SSOT nav slot yet; retiring it means moving them onto the
// Tools / Skills rail destinations. The extractions above make that a lift-and-
// mount rather than a surgery.

import type { ReactElement } from "react";
import { useEffect, useState } from "react";

import { useAuth } from "../auth/AuthContext";
import type { ConnectorState } from "../connectors/useConnectors";
import type { SkillState } from "../skills/useSkills";
import type { RequestIdentity } from "../../api/config";
import type { UserProfileState } from "../me/useUserProfile";
import { getLocalModelsStatus } from "../../api/localModelsApi";
// PR 8.1 — ACCOUNT group sections.
import { Appearance } from "./sections/Appearance";
import { Notifications } from "./sections/Notifications";
import { Profile } from "./sections/Profile";
import { Shortcuts } from "./sections/Shortcuts";
import { ApiKeys } from "./sections/ApiKeys";
// PR 8.1 — AI & DATA group sections.
import { ModelAndBehavior } from "./sections/ModelAndBehavior";
import { PrivacyAndData } from "./sections/PrivacyAndData";
// BYOK — per-user model provider keys.
import { ProviderKeys } from "./sections/ProviderKeys";
// The two large sub-features this screen still serves on web.
import { ConnectorsSettings } from "./sections/ConnectorsSettings";
import { SkillsSettings } from "./sections/SkillsSettings";
// PR 8.1 — WORKSPACE group sections.
import { AuditLogSettings } from "./AuditLogSettings";
import { BillingSettings } from "./BillingSettings";
import { MembersSettings } from "./MembersSettings";
import { WorkspaceSettings } from "./WorkspaceSettings";
// Nav chrome + descriptor.
import { RailRow, SettingsTopChrome, headerIdentityLabel } from "./SettingsNav";
import { railSections, type SettingsSection } from "./settingsSections";
// PR 8.1 — top chrome reads workspace name + member count.
import { useWorkspace, useWorkspaceMembers } from "./useWorkspace";
import { useWorkspaceDefaults } from "./useWorkspaceDefaults";
import "./workspace.css";

// The routing union stays importable from this module (App.tsx / routes.ts /
// HashRouter.ts consume it here) even though it now lives in `settingsSections`.
export type { SettingsSection } from "./settingsSections";

// PR 4.3 — fallback identity used only by legacy callers that mount
// SettingsScreen without threading identity. The "AI & data" sections
// that need real network calls render an error/loading state when
// the org/user are blank, so this is safe.
const FALLBACK_IDENTITY: RequestIdentity = {
  orgId: "",
  userId: "",
};

export function SettingsScreen({
  connectors,
  skills,
  identity,
  profile,
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
   * Hydrated user profile from the app shell. Optional so legacy callers
   * that mount SettingsScreen without threading it (tests, storybook)
   * keep working — the affected sections render a soft-disabled state
   * when absent. Preferences are read from `UserPreferencesProvider`
   * directly by the panels that need them (PRD 04), no prop threading.
   */
  profile?: UserProfileState;
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

  // Round 2 — the "Local models" section is server-gated: the status probe
  // returns `enabled: false` on cloud/multi-tenant deployments, where it
  // stays hidden. A failed probe also hides it (treat as unavailable).
  const [localModelsEnabled, setLocalModelsEnabled] = useState(false);
  useEffect(() => {
    let cancelled = false;
    getLocalModelsStatus()
      .then((status) => {
        if (!cancelled) setLocalModelsEnabled(status.enabled);
      })
      .catch(() => {
        if (!cancelled) setLocalModelsEnabled(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
        userEmail={headerIdentityLabel(profile?.data ?? null)}
        onBack={onBackToChat}
        onJumpConnectors={() => handlePick("connectors")}
      />
      <div className="settings-shell__body">
        <aside className="settings-nav" aria-label="Settings sections">
          {railSections
            .filter(
              (entry) =>
                entry.kind !== "section" ||
                entry.id !== "local-models" ||
                localModelsEnabled,
            )
            .map((entry, index) =>
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
          {activeSection === "appearance" && profile ? (
            <Appearance profile={profile} />
          ) : null}
          {activeSection === "shortcuts" ? <Shortcuts /> : null}
          {activeSection === "notifications" ? <Notifications /> : null}
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
          {activeSection === "provider-keys" ? <ProviderKeys /> : null}
          {/* local-models is served by the converged SettingsBinder →
              chat-surface LocalModelsPage; App.tsx never routes it here (only
              connectors/skills reach SettingsScreen). The legacy nav entry
              below still deep-links to the binder via onSectionChange. */}
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
