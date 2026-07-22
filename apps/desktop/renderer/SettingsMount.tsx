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

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

import {
  AppLockPage,
  AppearancePage,
  DeveloperTokensPage,
  Icon,
  LocalModelsPage,
  ModelsPage,
  ModelBehaviorPage,
  NotificationsPage,
  PrivacyPage,
  ProfilePage,
  ProviderKeysPage,
  SettingsSurface,
  ShortcutsPage,
  LOCAL_MODEL_CATALOG,
  appearanceAttributes,
  createDeveloperTokensPort,
  createLocalModelsPort,
  createModelsPort,
  createProviderKeysPort,
  createSpendGuardrailPort,
  localModelInstalledTag,
  type AppLockValue,
  type AppearanceValue,
  type KeychainProtectionValue,
  type ModelBehaviorModelOption,
  type ModelBehaviorValue,
  type SpendGuardrailPort,
  type SpendGuardrailValue,
  type ProfileIdentityAnchor,
  type ProfilePagePerson,
  type RetentionChoice,
  type SettingsSectionSlug,
  type SettingsSurfaceController,
} from "@0x-copilot/chat-surface";
import type {
  LocalModelSummary,
  LocalModelsStatus,
  NotificationDefaults,
  UpdateNotificationDefaultsRequest,
  UpdateWorkspaceDefaultsRequest,
  UserId,
  UserProfile,
  WorkspaceDefaultsResponse,
} from "@0x-copilot/api-types";
import {
  CHANNELS,
  isTransportHttpError,
  type AuthLinkOutcome,
  type RendererSession,
  type Transport,
} from "@0x-copilot/chat-transport";
import type { LinkWalletOutcome } from "@0x-copilot/chat-surface";

import { SECURE_STORAGE_CHANNELS } from "../main/services/secure-storage-channels";
import { buildModelCatalog } from "./composer/desktopModelCatalog";

interface SecureStorageStatus {
  readonly mode?: string;
  readonly keychainAvailable?: boolean;
}
interface SecureStorageSetResult {
  readonly ok?: boolean;
  readonly mode?: string;
  readonly error?: string;
}

export interface SettingsMountProps {
  /** The renderer's IPC transport (facade proxy) — backs the live ports. */
  readonly transport: Transport;
  /** The signed-in session, for the Profile identity row. */
  readonly session: RendererSession;
  /**
   * Sign out (Profile section). REQUIRED — the host must wire this to the real
   * session clear (bootstrap → SignInGate.signOut → `authSignOut` IPC →
   * AuthService.signOut). It is intentionally non-optional so a missing wire
   * fails typecheck rather than silently becoming a dead "Sign out" button —
   * the exact regression that shipped once (bootstrap omitted the prop, so the
   * click resolved to `undefined?.()` and did nothing).
   */
  readonly onSignOut: () => void;
  /**
   * PR-6.4: controlled active section. When provided (a slug, or `null` for the
   * default section) the surface is controlled — the ⌘K palette can deep-link a
   * section (FR-6.6/6.8) and switch it in place without remounting. When omitted
   * (`undefined`) the surface stays uncontrolled and owns its own section state
   * (the rail-foot open path and every existing caller / test).
   */
  readonly activeSection?: SettingsSectionSlug | null;
  /** PR-6.4: reflect the user's in-surface section clicks back to the host. */
  readonly onSectionChange?: (slug: SettingsSectionSlug) => void;
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
  // `null` == "Auto": no persisted default → the runtime baseline (D1).
  reasoningDepth: null,
  webAccess: true,
  approvalPolicy: { readOnly: "auto", write: "require", danger: "require" },
  spend: { monthlyCapUsd: null, pauseAtCap: false },
};

const NO_SPEND: SpendGuardrailValue = {
  monthlyCapUsd: null,
  pauseAtCap: false,
};

function spendEquals(a: SpendGuardrailValue, b: SpendGuardrailValue): boolean {
  return a.monthlyCapUsd === b.monthlyCapUsd && a.pauseAtCap === b.pauseAtCap;
}

const DEFAULT_APP_LOCK: AppLockValue = {
  encryptHistory: false,
  requireTouchId: false,
  lockAfter: "15m",
};

function makeNotificationDefaults(userId: string): NotificationDefaults {
  return {
    user_id: userId as UserId,
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

// A placeholder wallet email (`<address>@wallet.invalid`) is NOT a real
// address — never show it. Kept in sync with the backend
// WALLET_PLACEHOLDER_EMAIL_DOMAIN by convention (the profile payload's
// `email_is_placeholder` / `wallet_address` are the primary signals; this
// suffix check only backstops the pre-profile session fallback).
const WALLET_PLACEHOLDER_SUFFIX = "@wallet.invalid";

function anchorFromEmail(
  email: string,
  verified: boolean,
): ProfileIdentityAnchor {
  if (email.toLowerCase().endsWith(WALLET_PLACEHOLDER_SUFFIX)) {
    // Recover the address from the placeholder's local part (lowercase; the
    // profile fetch replaces it with the checksummed form + chain).
    return {
      kind: "wallet",
      address: email.slice(0, email.length - WALLET_PLACEHOLDER_SUFFIX.length),
      chainId: null,
      chainLabel: null,
    };
  }
  return { kind: "email", email, verified };
}

// Pre-profile fallback (rendered for the sub-second before /me/profile loads).
function personFromSession(session: RendererSession): ProfilePagePerson {
  return {
    user_id: session.workspaceId,
    display_name: session.displayName,
    avatar_url: null,
    anchor: anchorFromEmail(session.email ?? "", false),
  };
}

// The honest identity, built from the facade profile: a wallet anchor
// (checksummed address + chain) for SIWE accounts, else the real email + its
// verified state. Never surfaces the `@wallet.invalid` placeholder.
function personFromProfile(profile: UserProfile): ProfilePagePerson {
  const walletAddress = profile.wallet_address ?? null;
  const anchor: ProfileIdentityAnchor =
    walletAddress !== null && walletAddress !== ""
      ? {
          kind: "wallet",
          address: walletAddress,
          chainId: profile.chain_id ?? null,
          chainLabel: profile.chain_name ?? null,
        }
      : profile.auth_method === "local"
        ? // "Use locally" device account: anchored to this install — never
          // surface the synthetic device-…@local.invalid placeholder (D3).
          { kind: "device" }
        : profile.email_is_placeholder === true
          ? anchorFromEmail(profile.email, false)
          : {
              kind: "email",
              email: profile.email,
              verified: profile.email_verified_at !== null,
            };
  return {
    user_id: profile.user_id,
    display_name: profile.display_name,
    avatar_url: profile.avatar_url,
    anchor,
    authMethod: profile.auth_method ?? undefined,
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

/** Best-effort error → message for the local-models card (no util on desktop). */
function localModelsErrorMessage(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback;
}

// ---------------------------------------------------------------------------
// SettingsMount
// ---------------------------------------------------------------------------

export function SettingsMount({
  transport,
  session,
  onSignOut,
  activeSection,
  onSectionChange,
}: SettingsMountProps): ReactElement {
  const providerKeysPort = useMemo(
    () => createProviderKeysPort(transport),
    [transport],
  );
  const modelsPort = useMemo(() => createModelsPort(transport), [transport]);
  const developerTokensPort = useMemo(
    () => createDeveloperTokensPort(transport),
    [transport],
  );
  const localModelsPort = useMemo(
    () => createLocalModelsPort(transport),
    [transport],
  );
  const touchIdAvailable = transport.capabilities().nativeSecretStorage;

  // --- controlled section state -------------------------------------------
  // Honest identity: fetch the real profile (wallet address + chain + whether
  // the email is a placeholder) so the Profile page never shows the synthetic
  // `@wallet.invalid` address. Until it loads, fall back to the session.
  const [profile, setProfile] = useState<UserProfile | null>(null);
  useEffect(() => {
    let cancelled = false;
    void transport
      .request<UserProfile>({ method: "GET", path: "/v1/me/profile" })
      .then((loaded) => {
        if (!cancelled) setProfile(loaded);
      })
      .catch(() => {
        // Fall back to the session-derived identity (no hard failure).
      });
    return () => {
      cancelled = true;
    };
  }, [transport]);

  // Account-linking (PRD FR-U1): re-fetch the profile after a link/unlink so
  // the Linked-accounts panel reflects the change immediately.
  const refreshProfile = useCallback(async (): Promise<void> => {
    const loaded = await transport.request<UserProfile>({
      method: "GET",
      path: "/v1/me/profile",
    });
    setProfile(loaded);
  }, [transport]);

  // Unlink (PRD FR-L5): DELETE through the transport. A 409 last-sign-in-method
  // guard surfaces as a TransportHttpError whose message ProfilePage shows.
  const handleUnlinkIdentity = useCallback(
    async (kind: string, id: string): Promise<void> => {
      const wireKind = kind === "wallet" ? "wallet" : "oidc";
      await transport.request<void>({
        method: "DELETE",
        path: `/v1/me/identities/${wireKind}/${encodeURIComponent(id)}`,
      });
      await refreshProfile().catch(() => undefined);
    },
    [transport, refreshProfile],
  );

  // Wallet link (PRD FR-L1/M1): drive the system-browser SIWE proof via main,
  // then map the renderer-safe outcome into the shape ProfilePage's
  // merge-confirm flow consumes. `confirmMerge` re-runs the whole flow (fresh
  // signature) after the user consents.
  const handleLinkWallet = useCallback(
    async ({
      confirmMerge,
    }: {
      confirmMerge: boolean;
    }): Promise<LinkWalletOutcome> => {
      const bridge = typeof window !== "undefined" ? window.bridge : undefined;
      if (bridge === undefined) {
        return { status: "error", message: "Wallet linking isn’t available." };
      }
      try {
        const outcome = await bridge.ipc.invoke<AuthLinkOutcome>(
          CHANNELS.authLinkWallet,
          { workspaceId: session.workspaceId, confirmMerge },
        );
        if (
          outcome.status === "linked" ||
          outcome.status === "already_linked" ||
          outcome.status === "merged"
        ) {
          await refreshProfile().catch(() => undefined);
          return { status: outcome.status };
        }
        if (outcome.status === "merge_required") {
          return {
            status: "merge_required",
            message: outcome.message ?? undefined,
          };
        }
        return {
          status: "error",
          message: outcome.message ?? "Could not link that wallet.",
        };
      } catch (err) {
        return {
          status: "error",
          message:
            isTransportHttpError(err) || err instanceof Error
              ? err.message
              : "Could not link that wallet.",
        };
      }
    },
    [session.workspaceId, refreshProfile],
  );
  const [appearance, setAppearance] =
    useState<AppearanceValue>(DEFAULT_APPEARANCE);
  const [modelBehavior, setModelBehavior] = useState<ModelBehaviorValue>(
    DEFAULT_MODEL_BEHAVIOR,
  );

  // --- Model & behavior: real default-model wiring ------------------------
  // The default-model select is backed by the workspace-defaults contract
  // (GET on mount, read-merge-PUT on change — the PUT is a full-document
  // replace). Options come from the same curated catalog as the Run composer,
  // gated by which BYOK providers actually have keys.
  const [mbConfiguredProviders, setMbConfiguredProviders] = useState<
    ReadonlySet<string>
  >(new Set());
  const [mbProvidersKnown, setMbProvidersKnown] = useState(false);
  const [workspaceDefaults, setWorkspaceDefaults] =
    useState<WorkspaceDefaultsResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    void providerKeysPort
      .list()
      .then((keys) => {
        if (cancelled) return;
        const providers = new Set<string>();
        for (const key of keys) {
          providers.add(key.provider);
          // Key store speaks `google`; catalog + runtime speak `gemini`.
          if (key.provider === "google") providers.add("gemini");
        }
        setMbConfiguredProviders(providers);
        setMbProvidersKnown(true);
      })
      .catch(() => {
        // Fail open (catalog stays selectable); the PUT is the backstop.
      });
    return () => {
      cancelled = true;
    };
  }, [providerKeysPort]);

  useEffect(() => {
    let cancelled = false;
    void transport
      .request<WorkspaceDefaultsResponse>({
        method: "GET",
        path: "/v1/agent/workspace/defaults",
      })
      .then((res) => {
        if (!cancelled) setWorkspaceDefaults(res);
      })
      .catch(() => {
        // Section renders without a persisted default (select stays unset).
      });
    return () => {
      cancelled = true;
    };
  }, [transport]);

  const mbCatalog = useMemo(
    () =>
      buildModelCatalog({
        configuredProviders: mbConfiguredProviders,
        providersKnown: mbProvidersKnown,
        localModelNames: [],
      }),
    [mbConfiguredProviders, mbProvidersKnown],
  );
  // The select's value: the catalog id when the persisted default matches a
  // curated entry, else the bare model_name (rendered as a synthetic option).
  const mbDefaultValue = useMemo(() => {
    const dm = workspaceDefaults?.default_model;
    if (!dm || dm.provider === "" || dm.model_name === "") return null;
    const match = mbCatalog.find(
      (m) => m.provider === dm.provider && m.model_name === dm.model_name,
    );
    return match ? match.id : dm.model_name;
  }, [mbCatalog, workspaceDefaults]);
  // Legacy fallback for the provider-keys row chip, kept SYMMETRIC with the web
  // binder so the two hosts render identically (closes the modelChips
  // divergence). New keys carry `summary.default_model` (PR-F.5 per-provider
  // column) and never hit this; a key stored before the per-provider write path
  // has a null column, so it falls back to the single workspace default here.
  // Key store speaks `google`; the model resolver speaks `gemini`.
  const providerModelChips = useMemo<Readonly<Record<string, string>>>(() => {
    const dm = workspaceDefaults?.default_model;
    if (!dm || dm.provider === "" || dm.model_name === "") return {};
    const providerKey = dm.provider === "gemini" ? "google" : dm.provider;
    return { [providerKey]: dm.model_name };
  }, [workspaceDefaults]);
  const mbCloudModels = useMemo(() => {
    const options: ModelBehaviorModelOption[] = mbCatalog.map((m) => ({
      value: m.id,
      label: m.name,
      sub: m.provider,
      disabled: m.disabled,
    }));
    if (
      mbDefaultValue !== null &&
      !options.some((o) => o.value === mbDefaultValue)
    ) {
      options.push({
        value: mbDefaultValue,
        label: mbDefaultValue,
        sub: workspaceDefaults?.default_model?.provider,
        disabled: false,
      });
    }
    return options;
  }, [mbCatalog, mbDefaultValue, workspaceDefaults]);

  const persistDefaultModel = async (
    value: string | null,
    toast: (message: string) => void,
  ): Promise<void> => {
    if (value === null) {
      // The contract has no "no default" state (default_model is required);
      // runs always send the composer's explicit pick anyway. Say so.
      toast(
        "Runs use the composer's model pick; the saved default is unchanged.",
      );
      return;
    }
    const entry = mbCatalog.find((m) => m.id === value);
    const dm = workspaceDefaults?.default_model;
    const selection = entry
      ? { provider: entry.provider, model_name: entry.model_name }
      : dm && dm.model_name === value
        ? { provider: dm.provider, model_name: dm.model_name }
        : null;
    if (selection === null || workspaceDefaults === null) {
      toast(
        "Couldn't resolve that model — retry once settings finish loading.",
      );
      return;
    }
    const body: UpdateWorkspaceDefaultsRequest = {
      default_model: selection,
      default_connectors: workspaceDefaults.default_connectors,
      retention_days: workspaceDefaults.retention_days,
      behavior_overrides: workspaceDefaults.behavior_overrides,
    };
    try {
      const updated = await transport.request<WorkspaceDefaultsResponse>({
        method: "PUT",
        path: "/v1/agent/workspace/defaults",
        body,
      });
      setWorkspaceDefaults(updated);
      toast(`Default model saved — new runs start on ${selection.model_name}.`);
    } catch {
      toast("Saving the default model failed — retry in a moment.");
    }
  };

  // --- Local models: default-local persistence (C2) -----------------------
  // The "default local" chip + "Set default" round-trip is backed by the SAME
  // workspace-defaults contract as the default cloud model: a read-merge-PUT
  // (full-document replace) that stores the chosen Ollama tag in
  // ``behavior_overrides.default_local_model`` WITHOUT clobbering sibling
  // fields. ``null`` = no default → no chip (matches today). Behaviourally
  // identical to the web SettingsBinder.
  const defaultLocalModelName =
    workspaceDefaults?.behavior_overrides?.default_local_model ?? null;

  const persistDefaultLocalModel = async (
    name: string,
    toast: (message: string) => void,
  ): Promise<void> => {
    if (workspaceDefaults === null) {
      toast("Couldn't set the default — retry once settings finish loading.");
      return;
    }
    const body: UpdateWorkspaceDefaultsRequest = {
      default_model: workspaceDefaults.default_model,
      default_connectors: workspaceDefaults.default_connectors,
      retention_days: workspaceDefaults.retention_days,
      behavior_overrides: {
        ...workspaceDefaults.behavior_overrides,
        default_local_model: name,
      },
      enabled_models: workspaceDefaults.enabled_models,
    };
    try {
      const updated = await transport.request<WorkspaceDefaultsResponse>({
        method: "PUT",
        path: "/v1/agent/workspace/defaults",
        body,
      });
      setWorkspaceDefaults(updated);
      toast(`Default local model set to ${name}.`);
    } catch (err) {
      toast(
        localModelsErrorMessage(
          err,
          "Saving the default local model failed — retry in a moment.",
        ),
      );
    }
  };

  // --- Model & behavior: reasoning-depth (D1) + web-access (D3) defaults ----
  // Both persist to ``behavior_overrides`` via the SAME read-merge-PUT pattern
  // as the default (cloud/local) model — a full-document replace that never
  // clobbers sibling fields. Kept byte-identical to how the web SettingsBinder
  // will bind the shared ModelBehaviorPage in D5 so the two hosts stay lockstep.
  const mbReasoningDepth =
    workspaceDefaults?.behavior_overrides?.default_reasoning_depth ?? null;
  const mbWebAccess =
    workspaceDefaults?.behavior_overrides?.web_access_default ?? true;

  const persistBehaviorOverride = async (
    patch: Partial<WorkspaceDefaultsResponse["behavior_overrides"]>,
    toast: (message: string) => void,
    okMessage: string,
  ): Promise<void> => {
    if (workspaceDefaults === null) {
      toast("Couldn't save — retry once settings finish loading.");
      return;
    }
    const body: UpdateWorkspaceDefaultsRequest = {
      default_model: workspaceDefaults.default_model,
      default_connectors: workspaceDefaults.default_connectors,
      retention_days: workspaceDefaults.retention_days,
      behavior_overrides: { ...workspaceDefaults.behavior_overrides, ...patch },
      enabled_models: workspaceDefaults.enabled_models,
    };
    try {
      const updated = await transport.request<WorkspaceDefaultsResponse>({
        method: "PUT",
        path: "/v1/agent/workspace/defaults",
        body,
      });
      setWorkspaceDefaults(updated);
      toast(okMessage);
    } catch {
      toast("Saving that setting failed — retry in a moment.");
    }
  };

  // --- Model & behavior: spend guardrail (D4) -----------------------------
  // The monthly API cap + pause-at-cap toggle bind to the B7 budget engine via
  // the shared SpendGuardrailPort (GET /v1/budgets/me → POST/PATCH/DELETE). The
  // cap uses the ModelBehaviorPage SaveBar (a deferred Save, not autosave, since
  // it is a text field); an honest error surfaces on failure — never a fake
  // success or a fabricated $0 cap on a load error.
  const spendPort: SpendGuardrailPort = useMemo(
    () => createSpendGuardrailPort(transport),
    [transport],
  );
  const [spend, setSpend] = useState<SpendGuardrailValue>(NO_SPEND);
  const [spendBaseline, setSpendBaseline] =
    useState<SpendGuardrailValue | null>(null);
  const [spendSaving, setSpendSaving] = useState(false);
  const [spendSaveError, setSpendSaveError] = useState<string | null>(null);
  const [spendLoadError, setSpendLoadError] = useState<string | null>(null);

  const loadSpend = useCallback(() => {
    let cancelled = false;
    setSpendLoadError(null);
    void spendPort
      .read()
      .then((snap) => {
        if (cancelled) return;
        const value: SpendGuardrailValue = {
          monthlyCapUsd: snap.monthlyCapUsd,
          pauseAtCap: snap.pauseAtCap,
        };
        setSpend(value);
        setSpendBaseline(value);
      })
      .catch(() => {
        if (cancelled) return;
        // Honest failure — never a fabricated $0 cap. The page shows Retry.
        setSpendBaseline(null);
        setSpendLoadError("Couldn't load your spend cap. Retry?");
      });
    return () => {
      cancelled = true;
    };
  }, [spendPort]);
  useEffect(() => loadSpend(), [loadSpend]);

  const spendDirty =
    spendBaseline !== null && !spendEquals(spend, spendBaseline);

  const saveSpend = async (toast: (message: string) => void): Promise<void> => {
    setSpendSaving(true);
    setSpendSaveError(null);
    try {
      await spendPort.save(spend);
      setSpendBaseline(spend);
      toast(
        spend.monthlyCapUsd === null
          ? "Monthly spend cap cleared."
          : `Monthly spend cap saved ($${spend.monthlyCapUsd}).`,
      );
    } catch {
      setSpendSaveError("Saving the spend cap failed — retry in a moment.");
    } finally {
      setSpendSaving(false);
    }
  };

  const [retention, setRetention] = useState<RetentionChoice>("forever");
  const [memoryEnabled, setMemoryEnabled] = useState(true);
  const [notifications, setNotifications] = useState<NotificationDefaults>(() =>
    makeNotificationDefaults(session.email ?? session.workspaceId),
  );
  const [appLock, setAppLock] = useState<AppLockValue>(DEFAULT_APP_LOCK);

  // "Protect secrets with macOS Keychain" — real state from main over the
  // bridge (default policy is file/off, so no keychain prompt ever fires
  // until the user flips this). Enabling performs the boot-secrets migration
  // in main, which is exactly where the one OS prompt belongs.
  const [keychainProtection, setKeychainProtection] =
    useState<KeychainProtectionValue | null>(null);
  useEffect(() => {
    // No bridge (unit tests, web preview) → the row simply never renders.
    if (typeof window === "undefined" || window.bridge === undefined) return;
    let cancelled = false;
    void window.bridge.ipc
      .invoke<SecureStorageStatus>(SECURE_STORAGE_CHANNELS.get, {})
      .then((status) => {
        if (cancelled) return;
        setKeychainProtection({
          enabled: status.mode === "keychain",
          available: status.keychainAvailable === true,
        });
      })
      .catch(() => {
        // Bridge unavailable (tests / web preview) → row simply not rendered.
      });
    return () => {
      cancelled = true;
    };
  }, []);
  const handleKeychainProtectionChange = (
    enabled: boolean,
    toast: (message: string) => void,
  ): void => {
    if (typeof window === "undefined" || window.bridge === undefined) return;
    setKeychainProtection((prev) =>
      prev === null ? prev : { ...prev, busy: true },
    );
    void window.bridge.ipc
      .invoke<SecureStorageSetResult>(SECURE_STORAGE_CHANNELS.set, { enabled })
      .then((result) => {
        const on = result.mode === "keychain";
        setKeychainProtection((prev) =>
          prev === null ? prev : { ...prev, enabled: on, busy: false },
        );
        if (result.ok === true) {
          toast(
            on
              ? "Keychain protection on — macOS may ask for access after app updates."
              : "Secrets now stored in a file only your account can read.",
          );
        } else {
          toast(
            `Couldn't change keychain protection${result.error !== undefined ? `: ${result.error}` : ""}.`,
          );
        }
      })
      .catch(() => {
        setKeychainProtection((prev) =>
          prev === null ? prev : { ...prev, busy: false },
        );
        toast("Couldn't change keychain protection — try again.");
      });
  };

  // Local models (Ollama) — real wiring through the chat-surface port. `status`
  // always answers; when Ollama isn't running the page shows its setup steps.
  // Behaviourally identical to the web `SettingsBinder`.
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
        setLocalModelsError(
          localModelsErrorMessage(err, "Could not list local models."),
        ),
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
          localModelsErrorMessage(err, "Could not reach the local runtime."),
        ),
      );
  }, [localModelsPort, refreshLocalModelsList]);

  useEffect(() => {
    recheckLocalModels();
  }, [recheckLocalModels]);

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
            person={
              profile !== null
                ? personFromProfile(profile)
                : personFromSession(session)
            }
            // Persist to the facade (PUT /v1/me/profile), not just local state,
            // so a rename survives a reload — and reflect the server response.
            onSaveDisplayName={(next) => {
              void transport
                .request<UserProfile>({
                  method: "PUT",
                  path: "/v1/me/profile",
                  body: { display_name: next },
                })
                .then((updated) => {
                  setProfile(updated);
                  toast("Display name saved.");
                })
                .catch(() => {
                  toast("Couldn't save your display name. Please try again.");
                });
            }}
            onSignOut={onSignOut}
            // Linked accounts (PRD FR-U1): the real list from /me/profile.
            // Show the panel even when empty (matches web), so Link/Unlink are
            // always reachable.
            linkedIdentities={(profile?.linked_identities ?? []).map(
              (entry) => ({
                kind: entry.kind,
                id: entry.id,
                provider: entry.provider ?? null,
                email: entry.email ?? null,
                address: entry.address ?? null,
                chainName: entry.chain_name ?? null,
              }),
            )}
            // Unlink (FR-L5) — DELETE via transport; the last-method guard is
            // surfaced honestly by ProfilePage from the thrown error message.
            onUnlinkIdentity={handleUnlinkIdentity}
            // Wallet link (FR-L1/M1) — system-browser SIWE proof + POST; owns
            // the merge-confirm flow. Only wired when the native bridge exists.
            onLinkWallet={
              typeof window !== "undefined" && window.bridge !== undefined
                ? handleLinkWallet
                : undefined
            }
            // Google link (FR-L2) — system-browser OAuth; refresh + toast on
            // completion. `onLinkGoogle` is fire-and-forget per the contract.
            onLinkGoogle={
              typeof window !== "undefined" && window.bridge !== undefined
                ? () => {
                    void window.bridge?.ipc
                      .invoke<AuthLinkOutcome>(CHANNELS.authLinkGoogle, {
                        workspaceId: session.workspaceId,
                      })
                      .then((outcome) => {
                        if (
                          outcome.status === "linked" ||
                          outcome.status === "already_linked"
                        ) {
                          void refreshProfile().catch(() => undefined);
                          toast(
                            outcome.status === "linked"
                              ? "Google account linked."
                              : "That Google account was already linked.",
                          );
                        } else if (outcome.status === "merge_required") {
                          toast(
                            "That Google account belongs to another account. Link its wallet from here to merge them.",
                          );
                        } else {
                          toast(
                            outcome.message ??
                              "Couldn’t link that Google account.",
                          );
                        }
                      })
                      .catch(() => {
                        toast(
                          "Couldn’t start Google linking. Please try again.",
                        );
                      });
                  }
                : undefined
            }
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
        return (
          <ProviderKeysPage
            port={providerKeysPort}
            onToast={toast}
            modelChips={providerModelChips}
          />
        );
      case "models":
        return <ModelsPage port={modelsPort} onToast={toast} />;
      case "local-models":
        // C2: the "default local" chip + "Set default" + download's "use as
        // default" toggle persist to workspace-defaults
        // (behavior_overrides.default_local_model) via read-merge-PUT — real
        // persistence, honest error toast on failure (no fake success).
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
                  toast(
                    localModelsErrorMessage(err, "Could not remove the model."),
                  ),
                );
            }}
          />
        );
      case "model-behavior":
        return (
          <ModelBehaviorPage
            // Default model / reasoning depth / web access are server-backed
            // (workspace defaults, autosaved via read-merge-PUT). The spend cap
            // is server-backed too (the B7 budget engine) but deferred behind
            // the SaveBar. Approval policy stays local until its persistence
            // lands (out of D1/D3/D4 scope).
            value={{
              ...modelBehavior,
              defaultModel: mbDefaultValue,
              reasoningDepth: mbReasoningDepth,
              webAccess: mbWebAccess,
              spend,
            }}
            cloudModels={mbCloudModels}
            onChange={(patch) => {
              if (patch.defaultModel !== undefined) {
                void persistDefaultModel(patch.defaultModel, toast);
              }
              if (patch.reasoningDepth !== undefined) {
                void persistBehaviorOverride(
                  { default_reasoning_depth: patch.reasoningDepth },
                  toast,
                  "Reasoning depth saved.",
                );
              }
              if (patch.webAccess !== undefined) {
                void persistBehaviorOverride(
                  { web_access_default: patch.webAccess },
                  toast,
                  patch.webAccess
                    ? "Web access enabled by default."
                    : "Web access off by default.",
                );
              }
              if (patch.spend !== undefined) {
                setSpend(patch.spend);
              }
              setModelBehavior((prev) => ({ ...prev, ...patch }));
            }}
            controller={controller}
            // Spend-cap SaveBar contract (deferred save, honest errors).
            dirty={spendDirty}
            saving={spendSaving}
            saveError={spendSaveError}
            error={spendLoadError}
            onRetry={loadSpend}
            onSave={() => void saveSpend(toast)}
            onDiscard={() => {
              if (spendBaseline !== null) setSpend(spendBaseline);
            }}
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
            keychainProtection={keychainProtection ?? undefined}
            onKeychainProtectionChange={(enabled) =>
              handleKeychainProtectionChange(enabled, toast)
            }
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

  return (
    <SettingsSurface
      // PR-6.4: `activeSection === undefined` leaves the surface uncontrolled
      // (existing behaviour); a slug/`null` controls it so the palette can
      // deep-link + switch section in place.
      activeSlug={activeSection}
      onNavigate={onSectionChange}
      renderSection={renderSection}
      // PRD-E: feed the shared Icon set so every nav item shows its design glyph
      // (14×14, stroke 1.7). SettingsNavIcon is a subset of IconName.
      renderNavIcon={(icon) => <Icon name={icon} size={14} />}
    />
  );
}
