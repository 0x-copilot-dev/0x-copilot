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
