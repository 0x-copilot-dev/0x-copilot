/**
 * Canonical date / time formatters for the frontend.
 *
 * Before this file existed, three near-identical `formatTimestamp`
 * implementations + `formatDateTime` + `formatTimeShort` lived across
 * AuditLogSettings, AccountSessionsPanel, ContextPanel, and ApprovalTool —
 * with subtly different option objects and inconsistent error handling.
 *
 * Each helper here trusts the user's locale and timezone (browser Intl
 * already honours the Profile-set timezone). Every helper tolerates a
 * value that fails to parse and returns it verbatim, so callers never
 * need a wrapping try / catch.
 */

function parseIso(iso: string): Date | null {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date;
}

/**
 * Long form: "Mar 5, 2026, 09:14". Use in lists and tables where the
 * full date matters (audit log, session list).
 */
export function formatDateTime(iso: string): string {
  const date = parseIso(iso);
  if (date === null) return iso;
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Date only: "Mar 5, 2026". Use in compact summaries (invitations list,
 * API key creation date).
 */
export function formatDate(iso: string): string {
  const date = parseIso(iso);
  if (date === null) return iso;
  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/**
 * Time only, short form: "09:14". Use inline (approval receipt
 * timestamp, context-panel turn time).
 */
export function formatTimeShort(iso: string): string {
  const date = parseIso(iso);
  if (date === null) return iso;
  return date.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });
}
