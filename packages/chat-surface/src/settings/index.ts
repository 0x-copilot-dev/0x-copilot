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
