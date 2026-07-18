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
  type ProfilePagePerson,
  type ProfilePageProps,
} from "./ProfilePage";
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
  SecHead,
  SetNote,
  Frow,
  Krow,
  SettingsNavItem,
  type SetCardProps,
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
