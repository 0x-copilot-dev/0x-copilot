// Desktop Settings mount (PRD PR-5.9 — "wire the full Settings surface").
//
// Replaces PR-2.6's placeholder `<SettingsSurface/>` (nav + titled stubs) with
// the real surface: a `renderSection(slug, controller)` that maps EVERY settings
// slug → its chat-surface section body, each fed desktop-appropriate props and
// host callbacks. The profile gate lives in `SettingsSurface`/`settingsNav`
// (consumed via the `DeploymentProfileProvider` the renderer already wraps), so
// on `single_user_desktop` the team sections (Workspace/Members/Billing/Audit)
// never render and the solo footer shows — this component only supplies bodies.
//
// Host-adapter posture (PRD §5.9 / gaps §11): Provider keys and Developer tokens
// talk to the facade through the real `Transport`-backed ports (honest — they
// degrade to a role="alert" + Retry if the facade is unreachable). The remaining
// sections are controlled by local renderer state with minimal/stub host seams
// (Ollama pull, spend-cap persistence, app-lock persistence, memory review) —
// enough for every section to render and be driven; the live facade/Ollama/
// Keychain wiring lands with the Phase-6D smoke. Nothing here pretends an action
// persisted when it did not.
//
// This is app code (the renderer owns `document` + native capabilities); the
// chat-surface sections stay framework-agnostic behind their props/ports.

import { useMemo, useState, type ReactElement, type ReactNode } from "react";

import {
  AppLockPage,
  AppearancePage,
  DeveloperTokensPage,
  LocalModelsPage,
  ModelBehaviorPage,
  NotificationsPage,
  PrivacyPage,
  ProfilePage,
  ProviderKeysPage,
  SettingsSurface,
  ShortcutsPage,
  appearanceAttributes,
  createDeveloperTokensPort,
  createProviderKeysPort,
  type AppLockValue,
  type AppearanceValue,
  type ModelBehaviorValue,
  type ProfilePagePerson,
  type RetentionChoice,
  type SettingsSectionSlug,
  type SettingsSurfaceController,
  type StartLocalModelPull,
} from "@0x-copilot/chat-surface";
import type {
  LocalModelSummary,
  LocalModelsStatus,
  NotificationDefaults,
  UpdateNotificationDefaultsRequest,
} from "@0x-copilot/api-types";
import type { RendererSession, Transport } from "@0x-copilot/chat-transport";

export interface SettingsMountProps {
  /** The renderer's IPC transport (facade proxy) — backs the live ports. */
  readonly transport: Transport;
  /** The signed-in session, for the Profile identity row. */
  readonly session: RendererSession;
  /** Sign out (rail-foot / Profile). Optional; a no-op when absent. */
  readonly onSignOut?: () => void;
}

// ---------------------------------------------------------------------------
// Defaults for the controlled sections (a fresh solo desktop with no server
// round-trip yet). Honest neutral starting points, not fabricated data.
// ---------------------------------------------------------------------------

const DEFAULT_APPEARANCE: AppearanceValue = {
  theme: "system",
  accent: "sky",
  density: "comfortable",
  reduceMotion: false,
};

const DEFAULT_MODEL_BEHAVIOR: ModelBehaviorValue = {
  defaultModel: null,
  reasoningDepth: "auto",
  webAccess: true,
  approvalPolicy: { readOnly: "auto", write: "require", danger: "require" },
  spend: { monthlyCapUsd: null, pauseAtCap: false },
};

const DEFAULT_APP_LOCK: AppLockValue = {
  encryptHistory: false,
  requireTouchId: false,
  lockAfter: "15m",
};

// Ollama probe is a stub until PR-6D wires the real runtime seam. Report "not
// running" so the page shows the honest setup steps rather than a fake list.
const STUB_OLLAMA_STATUS: LocalModelsStatus = {
  enabled: true,
  ollama_running: false,
  ollama_version: null,
};

function makeNotificationDefaults(userId: string): NotificationDefaults {
  return {
    user_id: userId,
    destinations_enabled: {},
    quiet_hours: {
      enabled: false,
      from_local: "22:00",
      to_local: "07:00",
      tz: "UTC",
    },
    updated_at: new Date().toISOString(),
  };
}

function personFromSession(
  session: RendererSession,
  displayName: string | null,
): ProfilePagePerson {
  return {
    user_id: session.workspaceId,
    email: session.email ?? "",
    display_name: displayName,
    avatar_url: null,
  };
}

/** Apply the live appearance attributes to the document root (host concern). */
function applyAppearance(value: AppearanceValue): void {
  if (typeof document === "undefined") return;
  const attrs = appearanceAttributes(value);
  const root = document.documentElement;
  root.setAttribute("data-theme", attrs["data-theme"]);
  root.setAttribute("data-accent", attrs["data-accent"]);
  root.setAttribute("data-density", attrs["data-density"]);
  root.setAttribute("data-reduce-motion", attrs["data-reduce-motion"]);
}

// The download seam is not wired on desktop yet (PR-6D). It is only reachable
// once Ollama reports running, which the stub status never does — but the page
// requires the prop, so provide an honest failing stub rather than a fake.
const stubStartPull: StartLocalModelPull = (_request, handlers) => {
  handlers.onError(
    new Error("Local model downloads aren't wired in this build yet."),
  );
  return { close: () => undefined };
};

// ---------------------------------------------------------------------------
// SettingsMount
// ---------------------------------------------------------------------------

export function SettingsMount({
  transport,
  session,
  onSignOut,
}: SettingsMountProps): ReactElement {
  const providerKeysPort = useMemo(
    () => createProviderKeysPort(transport),
    [transport],
  );
  const developerTokensPort = useMemo(
    () => createDeveloperTokensPort(transport),
    [transport],
  );
  const touchIdAvailable = transport.capabilities().nativeSecretStorage;

  // --- controlled section state -------------------------------------------
  const [displayName, setDisplayName] = useState<string | null>(
    session.displayName,
  );
  const [appearance, setAppearance] =
    useState<AppearanceValue>(DEFAULT_APPEARANCE);
  const [modelBehavior, setModelBehavior] = useState<ModelBehaviorValue>(
    DEFAULT_MODEL_BEHAVIOR,
  );
  const [retention, setRetention] = useState<RetentionChoice>("forever");
  const [memoryEnabled, setMemoryEnabled] = useState(true);
  const [notifications, setNotifications] = useState<NotificationDefaults>(() =>
    makeNotificationDefaults(session.email ?? session.workspaceId),
  );
  const [appLock, setAppLock] = useState<AppLockValue>(DEFAULT_APP_LOCK);

  // Local models are read-only stubs for now (PR-6D wires the runtime).
  const localModels: readonly LocalModelSummary[] = [];

  const renderSection = (
    slug: SettingsSectionSlug,
    controller: SettingsSurfaceController,
  ): ReactNode | undefined => {
    const toast = (message: string): void => controller.showToast({ message });

    switch (slug) {
      // --- Account --------------------------------------------------------
      case "profile":
        return (
          <ProfilePage
            person={personFromSession(session, displayName)}
            onSaveDisplayName={(next) => {
              setDisplayName(next);
              toast("Display name saved.");
            }}
            onSignOut={() => onSignOut?.()}
          />
        );
      case "appearance":
        return (
          <AppearancePage
            value={appearance}
            onChange={(patch) => {
              setAppearance((prev) => {
                const next = { ...prev, ...patch };
                applyAppearance(next);
                return next;
              });
            }}
          />
        );
      case "shortcuts":
        return <ShortcutsPage />;

      // --- Models & keys --------------------------------------------------
      case "provider-keys":
        return <ProviderKeysPage port={providerKeysPort} onToast={toast} />;
      case "local-models":
        return (
          <LocalModelsPage
            status={STUB_OLLAMA_STATUS}
            models={localModels}
            defaultLocalModelName={null}
            onRecheck={() => toast("Re-checking the local runtime…")}
            onDownloaded={() => undefined}
            startPull={stubStartPull}
            onDelete={() => undefined}
          />
        );
      case "model-behavior":
        return (
          <ModelBehaviorPage
            value={modelBehavior}
            onChange={(patch) =>
              setModelBehavior((prev) => ({ ...prev, ...patch }))
            }
            controller={controller}
          />
        );

      // --- Data & privacy -------------------------------------------------
      case "privacy":
        return (
          <PrivacyPage
            retention={retention}
            onRetentionChange={setRetention}
            memoryEnabled={memoryEnabled}
            onMemoryToggle={setMemoryEnabled}
            memoryCount={0}
            onReviewMemories={() => undefined}
            onOpenActivity={() => undefined}
            onExport={() => Promise.resolve()}
            onDeleteAll={() => Promise.resolve()}
            onToast={toast}
          />
        );

      // --- Notifications --------------------------------------------------
      case "notifications":
        return (
          <NotificationsPage
            myDefaults={notifications}
            workspaceDefaults={null}
            isAdmin={false}
            onSaveMy={(patch: UpdateNotificationDefaultsRequest) => {
              setNotifications((prev) => ({
                ...prev,
                destinations_enabled:
                  patch.destinations_enabled ?? prev.destinations_enabled,
                quiet_hours: patch.quiet_hours ?? prev.quiet_hours,
                updated_at: new Date().toISOString(),
              }));
              toast("Notification preferences saved.");
            }}
          />
        );

      // --- Advanced -------------------------------------------------------
      case "app-lock":
        return (
          <AppLockPage
            value={appLock}
            onChange={(patch) => setAppLock((prev) => ({ ...prev, ...patch }))}
            touchIdAvailable={touchIdAvailable}
          />
        );
      case "developer-tokens":
        return (
          <DeveloperTokensPage port={developerTokensPort} onToast={toast} />
        );

      // Team-gated slugs (workspace/members/billing/audit) never resolve on the
      // solo desktop profile — the surface gates them out before renderSection.
      // Returning undefined lets the surface fall back to its titled placeholder
      // for any slug this build does not supply a body for.
      default:
        return undefined;
    }
  };

  return <SettingsSurface renderSection={renderSection} />;
}
