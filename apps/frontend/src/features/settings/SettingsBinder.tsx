// Web Settings binder (PRD-E FR-E.1/E.2, PRD-F PR-F.1).
//
// Mounts the chat-surface `SettingsSurface` — the SSOT settings shell + nav —
// on the WEB app, mirroring the desktop `apps/desktop/renderer/SettingsMount`.
// The nav model (`settingsNav`), chrome, profile gate, and icons all come from
// `@0x-copilot/chat-surface`; this binder only supplies the section BODIES,
// bound to the web app's existing data (the same api clients / hooks the legacy
// `SettingsScreen` used) and the redesigned `ProviderKeysPage` over the web
// `Transport`. No `apps/* → apps/*` import — the binder duplicates only pure
// web→SSOT slug projection, not components.
//
// -------------------------------------------------------------------------
// Web → SSOT section mapping (FR-E.5) — so no section is lost silently.
// The web router speaks the legacy `SettingsSection` slugs; the SSOT nav speaks
// `SettingsSectionSlug`. This binder bridges the two:
//
//   web `SettingsSection`   → SSOT `SettingsSectionSlug`   (source of the body)
//   ---------------------------------------------------------------------------
//   profile                 → profile                       (web `Profile`)
//   appearance              → appearance                    (web `Appearance`)
//   shortcuts               → shortcuts                     (web `Shortcuts`)
//   api-keys                → developer-tokens              (web `ApiKeys` — the
//                                                            personal bearer
//                                                            tokens; same concept)
//   provider-keys           → provider-keys                 (chat-surface
//                                                            `ProviderKeysPage`)
//   local-models            → local-models                  (chat-surface
//                                                            `LocalModelsPage` —
//                                                            converged; the
//                                                            legacy web section
//                                                            was retired)
//   model-and-behavior      → model-behavior                (web `ModelAndBehavior`)
//   privacy-data            → privacy                        (web `PrivacyAndData`)
//   notifications           → notifications                 (web `Notifications`)
//   models                  → models                         (curation — web has
//                                                            no body yet → the
//                                                            surface placeholder)
//   app-lock                → app-lock                       (desktop-only — web
//                                                            has no body → the
//                                                            surface placeholder)
//   workspace/members/      → workspace/members/            (web team admin
//   billing/audit-log        billing/audit                   components; only
//                                                            visible under the
//                                                            `team` profile)
//
// NOT handled here (kept on the legacy path, per FR-E.5): `connectors` and
// `skills` have NO SSOT nav slot — they are rail destinations (Tools / Skills)
// and the MCP-server management + skill editor still live in the legacy
// `SettingsScreen`. `App.tsx` routes those two sections to `SettingsScreen`; all
// other sections route here. Nothing is dropped.
// -------------------------------------------------------------------------

import {
  Icon,
  LOCAL_MODEL_CATALOG,
  LocalModelsPage,
  ProviderKeysPage,
  SettingsSurface,
  createLocalModelsPort,
  createProviderKeysPort,
  localModelInstalledTag,
  type SettingsSectionSlug,
} from "@0x-copilot/chat-surface";
import type { Transport } from "@0x-copilot/chat-transport";
import type {
  LocalModelSummary,
  LocalModelsStatus,
} from "@0x-copilot/api-types";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

import type { RequestIdentity } from "../../api/config";
import type { UserProfileState } from "../me/useUserProfile";
import type { SettingsSection } from "./SettingsScreen";
import { errorMessage } from "../../utils/errors";
import { Appearance } from "./sections/Appearance";
import { ApiKeys } from "./sections/ApiKeys";
import { ModelAndBehavior } from "./sections/ModelAndBehavior";
import { Notifications } from "./sections/Notifications";
import { PrivacyAndData } from "./sections/PrivacyAndData";
import { Profile } from "./sections/Profile";
import { Shortcuts } from "./sections/Shortcuts";
import { AuditLogSettings } from "./AuditLogSettings";
import { BillingSettings } from "./BillingSettings";
import { MembersSettings } from "./MembersSettings";
import { WorkspaceSettings } from "./WorkspaceSettings";
import { useWorkspaceDefaults } from "./useWorkspaceDefaults";

// A web section this binder does NOT render (routed to the legacy screen).
export type BinderExcludedSection = "connectors" | "skills";

/** The web sections the binder owns (everything the SSOT nav can reach). */
export type BinderSection = Exclude<SettingsSection, BinderExcludedSection>;

// web SettingsSection → SSOT SettingsSectionSlug (deep-link + active state).
const WEB_TO_SLUG: Record<BinderSection, SettingsSectionSlug> = {
  profile: "profile",
  appearance: "appearance",
  shortcuts: "shortcuts",
  "api-keys": "developer-tokens",
  workspace: "workspace",
  members: "members",
  billing: "billing",
  "audit-log": "audit",
  "model-and-behavior": "model-behavior",
  "provider-keys": "provider-keys",
  "local-models": "local-models",
  "privacy-data": "privacy",
  notifications: "notifications",
  models: "models",
  "app-lock": "app-lock",
};

// SSOT SettingsSectionSlug → web SettingsSection (reflect nav clicks to the URL).
const SLUG_TO_WEB: Record<SettingsSectionSlug, SettingsSection> = {
  profile: "profile",
  appearance: "appearance",
  shortcuts: "shortcuts",
  "provider-keys": "provider-keys",
  models: "models",
  "local-models": "local-models",
  "model-behavior": "model-and-behavior",
  privacy: "privacy-data",
  notifications: "notifications",
  "app-lock": "app-lock",
  "developer-tokens": "api-keys",
  workspace: "workspace",
  members: "members",
  billing: "billing",
  audit: "audit-log",
};

/** Web sections that ARE routed here (used by App.tsx to split the dispatch). */
export function isBinderSection(
  section: SettingsSection,
): section is BinderSection {
  return section !== "connectors" && section !== "skills";
}

/**
 * Map a chat-surface `SettingsSectionSlug` (e.g. the ⌘K palette's section) to
 * the web `SettingsSection` so `App.tsx` can navigate without re-deriving the
 * spelling deltas (`model-behavior`→`model-and-behavior`, `privacy`→
 * `privacy-data`, `developer-tokens`→`api-keys`). Unknown slugs fall back to
 * `profile`.
 */
export function webSectionForSlug(slug: string): SettingsSection {
  return SLUG_TO_WEB[slug as SettingsSectionSlug] ?? "profile";
}

export interface SettingsBinderProps {
  /** Web transport singleton (facade proxy) — backs the provider-keys port. */
  readonly transport: Transport;
  /** Hydrated user profile (Profile / Appearance sections). */
  readonly profile: UserProfileState;
  /** Caller identity for the workspace-defaults + team-admin sections. */
  readonly identity: RequestIdentity;
  /** Admin gate for the team Workspace group (backend still enforces). */
  readonly isAdmin: boolean;
  /** Read-only data-residency label (Privacy & data). */
  readonly dataResidency?: string | null;
  /** The active web section from the route. */
  readonly section: BinderSection;
  /** Reflect a section change back to the URL. */
  readonly onNavigate: (section: SettingsSection) => void;
}

export function SettingsBinder({
  transport,
  profile,
  identity,
  isAdmin,
  dataResidency,
  section,
  onNavigate,
}: SettingsBinderProps): ReactElement {
  const providerKeysPort = useMemo(
    () => createProviderKeysPort(transport),
    [transport],
  );
  const localModelsPort = useMemo(
    () => createLocalModelsPort(transport),
    [transport],
  );
  const workspaceDefaults = useWorkspaceDefaults(identity);

  // Local models (Ollama) — real wiring through the shared chat-surface port.
  // `status` always answers; when Ollama isn't running the page shows its setup
  // steps. Behaviourally identical to the desktop `SettingsMount`.
  const [localModelsStatus, setLocalModelsStatus] =
    useState<LocalModelsStatus | null>(null);
  const [localModels, setLocalModels] = useState<readonly LocalModelSummary[]>(
    [],
  );
  const [localModelsError, setLocalModelsError] = useState<string | null>(null);

  const refreshLocalModelsList = useCallback(() => {
    localModelsPort
      .list()
      .then(setLocalModels)
      .catch((err: unknown) =>
        setLocalModelsError(errorMessage(err, "Could not list local models.")),
      );
  }, [localModelsPort]);

  const recheckLocalModels = useCallback(() => {
    setLocalModelsError(null);
    localModelsPort
      .status()
      .then((next) => {
        setLocalModelsStatus(next);
        if (next.ollama_running) refreshLocalModelsList();
      })
      .catch((err: unknown) =>
        setLocalModelsError(
          errorMessage(err, "Could not reach the local runtime."),
        ),
      );
  }, [localModelsPort, refreshLocalModelsList]);

  useEffect(() => {
    recheckLocalModels();
  }, [recheckLocalModels]);

  // FR-F.5 (host-merge variant): the connected provider-keys row shows its
  // default model after reload by seeding `modelChips` from the workspace
  // default. The key store speaks `google`; the model resolver speaks
  // `gemini` — mirror the backend parser so the chip keys the catalog row.
  const modelChips = useMemo<Readonly<Record<string, string>>>(() => {
    const dm = workspaceDefaults.defaults?.default_model;
    if (dm === undefined || dm === null) return {};
    if (dm.provider === "" || dm.model_name === "") return {};
    const providerKey = dm.provider === "gemini" ? "google" : dm.provider;
    return { [providerKey]: dm.model_name };
  }, [workspaceDefaults.defaults]);

  // --- Local models: default-local persistence (C2) -----------------------
  // The "default local" chip + "Set default" round-trip is backed by the SAME
  // workspace-defaults contract as the default cloud model: `save()` does a
  // read-merge-write (full-document replace) that stores the chosen Ollama tag
  // in `behavior_overrides.default_local_model` WITHOUT clobbering sibling
  // fields. `null` = no default → no chip (matches today). Behaviourally
  // identical to the desktop SettingsMount.
  const defaultLocalModelName =
    workspaceDefaults.defaults?.behavior_overrides?.default_local_model ?? null;

  const persistDefaultLocalModel = async (
    name: string,
    toast: (message: string) => void,
  ): Promise<void> => {
    const current = workspaceDefaults.defaults;
    if (current === null) {
      toast("Couldn't set the default — retry once settings finish loading.");
      return;
    }
    try {
      await workspaceDefaults.save({
        default_model: current.default_model,
        default_connectors: current.default_connectors,
        retention_days: current.retention_days,
        behavior_overrides: {
          ...current.behavior_overrides,
          default_local_model: name,
        },
        enabled_models: current.enabled_models,
      });
      toast(`Default local model set to ${name}.`);
    } catch (err) {
      toast(
        errorMessage(
          err,
          "Saving the default local model failed — retry in a moment.",
        ),
      );
    }
  };

  const activeSlug = WEB_TO_SLUG[section];

  const renderSection = (
    slug: SettingsSectionSlug,
    controller: { showToast: (t: { message: ReactNode }) => void },
  ): ReactNode | undefined => {
    const toast = (message: string): void => controller.showToast({ message });
    switch (slug) {
      // --- Account --------------------------------------------------------
      case "profile":
        return <Profile profile={profile} />;
      case "appearance":
        return <Appearance profile={profile} />;
      case "shortcuts":
        return <Shortcuts />;

      // --- Models & keys --------------------------------------------------
      case "provider-keys":
        return (
          <ProviderKeysPage
            port={providerKeysPort}
            onToast={toast}
            modelChips={modelChips}
          />
        );
      case "local-models":
        // C2: the "default local" chip + "Set default" + download's "use as
        // default" toggle persist to workspace-defaults
        // (behavior_overrides.default_local_model) via read-merge-PUT — real
        // persistence, honest error toast on failure (no fake success).
        // Behaviourally identical to the desktop SettingsMount (lockstep).
        return (
          <LocalModelsPage
            status={localModelsStatus}
            models={localModels}
            availableModels={LOCAL_MODEL_CATALOG}
            defaultLocalModelName={defaultLocalModelName}
            loadError={localModelsError}
            onRecheck={recheckLocalModels}
            onDownloaded={(result) => {
              if (result.setAsDefault) {
                void persistDefaultLocalModel(
                  localModelInstalledTag(result.model.repo, result.model.quant),
                  toast,
                );
              }
              refreshLocalModelsList();
            }}
            onSetDefault={(name) => {
              void persistDefaultLocalModel(name, toast);
            }}
            startPull={(request, handlers) =>
              localModelsPort.pull(request.repo, request.quant, handlers)
            }
            resolveSize={(request) =>
              localModelsPort
                .size(request.repo, request.quant)
                .then((size) => size.size_bytes)
            }
            onDelete={(name) => {
              void localModelsPort
                .remove(name)
                .then(() => refreshLocalModelsList())
                .catch((err: unknown) =>
                  toast(errorMessage(err, "Could not remove the model.")),
                );
            }}
          />
        );
      case "model-behavior":
        return <ModelAndBehavior workspaceDefaults={workspaceDefaults} />;
      // "models" (curation) — no web body yet; the surface renders its
      // "Coming soon" placeholder (parity NG2 — stubbed bodies acceptable).

      // --- Data & privacy -------------------------------------------------
      case "privacy":
        return (
          <PrivacyAndData
            identity={identity}
            workspaceDefaults={workspaceDefaults}
            dataResidency={dataResidency}
          />
        );

      // --- Notifications --------------------------------------------------
      case "notifications":
        return <Notifications />;

      // --- Advanced -------------------------------------------------------
      // "app-lock" — desktop-only; the surface renders its placeholder.
      case "developer-tokens":
        // Personal bearer tokens (the legacy "API keys" section).
        return <ApiKeys />;

      // --- Team admin (only visible under the `team` profile) -------------
      case "workspace":
        return <WorkspaceSettings identity={identity} isAdmin={isAdmin} />;
      case "members":
        return <MembersSettings identity={identity} isAdmin={isAdmin} />;
      case "billing":
        return <BillingSettings identity={identity} />;
      case "audit":
        return <AuditLogSettings identity={identity} isAdmin={isAdmin} />;

      default:
        return undefined;
    }
  };

  return (
    <SettingsSurface
      activeSlug={activeSlug}
      onNavigate={(slug) => onNavigate(SLUG_TO_WEB[slug] ?? "profile")}
      renderSection={renderSection}
      // PRD-E FR-E.2: feed the shared Icon set so every nav item shows its
      // design glyph (14×14, stroke 1.7). SettingsNavIcon is a subset of IconName.
      renderNavIcon={(icon) => <Icon name={icon} size={14} />}
    />
  );
}
