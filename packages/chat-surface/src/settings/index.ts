// Settings pages — NOT a destination (master PRD §3.5). Pages live off
// the profile menu and reuse SP-1 primitives.

export {
  NotificationsPage,
  NOTIFICATION_DESTINATION_ROWS,
  type DestinationRowDescriptor,
  type NotificationsPageProps,
  type NotificationsPageTabSlug,
} from "./NotificationsPage";
export {
  WebhookSecurityPage,
  MAX_SECRET_AGE_DAY_VALUES,
  clampMaxSecretAgeDays,
  type WebhookSecurityPageProps,
} from "./WebhookSecurityPage";
export {
  ProfilePage,
  type LinkWalletOutcome,
  type ProfileIdentityAnchor,
  type ProfileLinkedIdentity,
  type ProfilePagePerson,
  type ProfilePageProps,
} from "./ProfilePage";

// === Phase 5 (PR-5.3) — Account group section bodies ===
// Profile is reused from ./ProfilePage (above). Appearance + Shortcuts are the
// two Account bodies built here; they slot into SettingsSurface.renderSection.
export {
  AppearancePage,
  appearanceAttributes,
  splitAppearancePersistence,
  APPEARANCE_THEMES,
  APPEARANCE_ACCENTS,
  APPEARANCE_DENSITIES,
  type AppearancePageProps,
  type AppearanceValue,
  type AppearancePatch,
  type AppearanceTheme,
  type AppearanceAccentId,
  type AppearanceDensity,
  type AppearanceAttributes,
  type AppearancePersistenceSplit,
} from "./AppearancePage";
export { ShortcutsPage, SHORTCUTS, type ShortcutRow } from "./ShortcutsPage";
// === end Phase 5 (PR-5.3) ===
export {
  QuietHoursEditor,
  validateQuietHoursWindow,
  type QuietHoursEditorProps,
} from "./QuietHoursEditor";

// === Phase 5 (PR-5.1) — settings shell (nav SSOT + profile gate + router) ===
// The SettingsSurface hosts the 216px nav, the content router, and the
// savebar/toast dock; settingsNav.ts is the single source of truth for the
// section slugs, groups, and profile gate. Section bodies (PR-5.3…PR-5.9) are
// injected via the `renderSection` slot.
export {
  SettingsSurface,
  useSettingsSurface,
  SETTINGS_NAV_WIDTH,
  SETTINGS_CONTENT_MAX_WIDTH,
  type SettingsSurfaceProps,
  type SettingsSurfaceController,
  type SettingsDirtyState,
  type SettingsSurfaceToast,
} from "./SettingsSurface";
export {
  SETTINGS_NAV_GROUPS,
  SETTINGS_NAV_ITEMS,
  DEFAULT_SETTINGS_SLUG,
  SOLO_FOOTER_COPY,
  settingsNavForProfile,
  visibleSettingsSlugs,
  isSettingsSlugVisible,
  resolveSettingsSlug,
  showSoloFooter,
  settingsNavItem,
  type SettingsSectionSlug,
  type SettingsNavGroupId,
  type SettingsNavGroupView,
  type SettingsNavIcon,
  type SettingsNavItem as SettingsNavItemModel,
  type SettingsProfileGate,
} from "./settingsNav";
// === end Phase 5 (PR-5.1) ===

// === Phase 5 (PR-5.2) — settings design primitives (tokenized) ===
// Reusable settings chrome + flow modal + controls. Built on design-system
// v2 tokens; the actual sections (PR-5.3…PR-5.9) compose these.
export {
  Modal,
  StepDots,
  MODAL_WIDTH,
  type ModalProps,
  type StepDotsProps,
} from "./Modal";
export {
  SetCard,
  SecTitle,
  SecHead,
  SetNote,
  Frow,
  Krow,
  SettingsNavItem,
  type SetCardProps,
  type SecTitleProps,
  type SecHeadProps,
  type SetNoteProps,
  type SetNoteTone,
  type FrowProps,
  type KrowProps,
  type SettingsNavItemProps,
} from "./SettingsChrome";
export {
  SaveBar,
  Toast,
  type SaveBarProps,
  type ToastProps,
  type ToastTone,
} from "./SaveBar";
export {
  SegmentedControl,
  AccentSwatch,
  ThemeTile,
  ProgressBar,
  type SegmentedControlProps,
  type SegmentedOption,
  type AccentSwatchProps,
  type ThemeTileProps,
  type ProgressBarProps,
  type ProgressTone,
} from "./controls";
// === end Phase 5 (PR-5.2) ===

// === Phase 5 (PR-5.5) — Local models section + Download flow ===
// LocalModelsPage fills the "local-models" SettingsSurface slot; the runtime
// (Ollama pull/list/delete + SSE progress) is a host callback seam — chat-surface
// stays framework-agnostic. DownloadLocalModelModal is the DESIGN-SPEC §5 flow.
export { LocalModelsPage, type LocalModelsPageProps } from "./LocalModelsPage";
export {
  DownloadLocalModelModal,
  type DownloadLocalModelModalProps,
  type AvailableLocalModel,
  type LocalModelPullHandle,
  type LocalModelPullHandlers,
  type StartLocalModelPull,
  type LocalModelDownloadResult,
} from "./DownloadLocalModelModal";
// Curated download catalog (P2) — one SSOT for the FTUE gate card + Settings.
export { QWEN3_4B_PRESET, LOCAL_MODEL_PRESETS } from "./localModelPresets";
export {
  formatBytes,
  formatEta,
  humanStatus,
  placementLabel,
} from "./localModelsFormat";
// Local-models data seam: the Transport-backed port (status/list/size/remove/
// pull) + the curated "pick from available" catalog. Mirrors the providerKeys
// port pattern; both hosts wire `createLocalModelsPort(transport)`.
export {
  createLocalModelsPort,
  LOCAL_MODEL_CATALOG,
  LOCAL_MODEL_PULL_EVENT,
  DEFAULT_LOCAL_MODEL_QUANT,
  localModelInstalledTag,
  type LocalModelsPort,
} from "./data/localModels";
// === end Phase 5 (PR-5.5) ===
// === Phase 5 (PR-5.4) — Provider keys (BYOK) + Add-key flow ===
// The page depends on an injected `ProviderKeysPort` (default:
// `createProviderKeysPort(transport)`); plaintext keys never live in
// chat-surface — reads carry only the masked `key_hint`.
export {
  ProviderKeysPage,
  PROVIDER_KEYS_KEYCHAIN_NOTE,
  type ProviderKeysPageProps,
} from "./ProviderKeysPage";
export {
  AddProviderKeyModal,
  type AddProviderKeyModalProps,
  type AddProviderKeySubmit,
} from "./AddProviderKeyModal";
export {
  createProviderKeysPort,
  checkProviderKeyFormat,
  providerCatalogEntry,
  PROVIDER_CATALOG,
  type ProviderKeysPort,
  type ProviderCatalogEntry,
  type ProviderKeyValidation,
} from "./data/providerKeys";
// === end Phase 5 (PR-5.4) ===
// === Phase 5 (PR-3D) — Models curation (Settings → Models) ===
// The page reads the live catalog + persists the enabled set through an
// injected `ModelsPort` (default: `createModelsPort(transport)`), which
// read-merge-writes the workspace-defaults `enabled_models`.
export {
  ModelsPage,
  MODELS_PAGE_NOTE,
  type ModelsPageProps,
} from "./ModelsPage";
export {
  createModelsPort,
  groupModelsByProvider,
  filterModels,
  providerLabel,
  priceLabel,
  contextLabel,
  type ModelsPort,
  type CatalogModel,
  type ModelGroup,
} from "./data/models";
// === end Phase 5 (PR-5.4) ===

// === Phase 5 (PR-5.7) — Data & privacy (retention / memory / export / delete) ===
// PrivacyPage fills the "privacy" SettingsSurface slot. Controlled +
// presentation-only: retention/memory are props, and export/delete/activity/
// review are injected HOST callbacks (chat-surface never touches fs, history,
// routing, or storage). Delete is destructive and gated behind a typed
// confirmation (PRIVACY_DELETE_CONFIRM_PHRASE) — never auto (FR-5.20).
export {
  PrivacyPage,
  RETENTION_OPTIONS,
  PRIVACY_EXPORT_PATH,
  PRIVACY_DELETE_CONFIRM_PHRASE,
  type PrivacyPageProps,
  type RetentionChoice,
} from "./PrivacyPage";
// === end Phase 5 (PR-5.7) ===
// === Phase 5 (PR-5.6) — Model & behavior (default model / depth / web /
// approval policy / spend guardrail) ===
// `ModelBehaviorPage` fills the "model-behavior" SettingsSurface slot. It is a
// controlled, props-driven section: the default-model optgroups are SUPPLIED BY
// THE HOST (composed from PR-5.4 provider keys + PR-5.5 local models), and a
// dirty section docks its SaveBar through the injected surface controller. The
// approval-policy block (`ApprovalPolicy`) is the spec relocation of the web
// `ToolUsePolicyPanel`. Persistence (workspace defaults / tool-use policy /
// spend cap) is a host concern wired in the desktop-mount PR.
export {
  ModelBehaviorPage,
  REASONING_DEPTHS,
  type ModelBehaviorPageProps,
  type ModelBehaviorValue,
  type ModelBehaviorPatch,
  type ModelBehaviorModelOption,
  type ReasoningDepth,
  type SpendGuardrailValue,
} from "./ModelBehaviorPage";
// D4 — Spend-guardrail data seam: the Transport-backed port bound to the B7
// budget engine at /v1/budgets (the caller's user/month cap). Owns the
// dollars↔micro-USD conversion; both hosts wire `createSpendGuardrailPort`.
export {
  createSpendGuardrailPort,
  capUsdToMicro,
  microToCapUsd,
  type SpendGuardrailPort,
  type SpendGuardrailSnapshot,
} from "./data/spendGuardrail";
export {
  ApprovalPolicy,
  READ_ONLY_APPROVAL_OPTIONS,
  WRITE_APPROVAL_OPTIONS,
  DANGER_APPROVAL_OPTIONS,
  APPROVAL_POLICY_CONNECTOR_NOTE,
  type ApprovalPolicyProps,
  type ApprovalPolicyValue,
  type ReadOnlyApprovalMode,
  type WriteApprovalMode,
  type DangerApprovalMode,
} from "./ApprovalPolicy";
// === end Phase 5 (PR-5.6) ===

// === Phase 5 (PR-5.9) — Advanced group (Key storage & app lock · Developer
// tokens) ===
// The two Advanced-group section bodies. Both are controlled / props-driven and
// reuse the PR-5.2 chrome. `AppLockPage` reads a native Touch-ID capability the
// HOST supplies (chat-surface never calls a native API); `DeveloperTokensPage`
// mints/lists/revokes through the injected `DeveloperTokensPort` (default:
// `createDeveloperTokensPort(transport)` against `/v1/me/api-keys`), and the
// plaintext secret is revealed exactly once.
export {
  AppLockPage,
  APP_LOCK_AFTER_OPTIONS,
  APP_LOCK_KEYCHAIN_NOTE,
  TOUCH_ID_UNAVAILABLE_HINT,
  type AppLockPageProps,
  type AppLockValue,
  type AppLockPatch,
  type AppLockAfter,
  type KeychainProtectionValue,
} from "./AppLockPage";
export {
  DeveloperTokensPage,
  DEVELOPER_TOKENS_ONCE_NOTE,
  type DeveloperTokensPageProps,
} from "./DeveloperTokensPage";
export {
  createDeveloperTokensPort,
  maskDeveloperToken,
  lastUsedLabel,
  type DeveloperTokensPort,
} from "./data/developerTokens";
// === end Phase 5 (PR-5.9) ===
