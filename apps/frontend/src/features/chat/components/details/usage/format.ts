/**
 * Display formatters for usage / token surfaces.
 *
 * `formatTokens` was previously inlined as `${value.toLocaleString()} tok`
 * across ContextPanel, UsageConversationView, UsageWorkspaceChart, and
 * UsageTopUsersTable. One canonical helper so the suffix and locale
 * formatting stay consistent across every "tok"-suffixed display.
 */

export function formatTokens(value: number): string {
  return `${value.toLocaleString()} tok`;
}
