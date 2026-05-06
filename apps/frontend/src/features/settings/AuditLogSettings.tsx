// PR 7.1 — Settings → Audit log panel.
//
// Admin-only paginated table of audit events across the four backend
// streams (mcp / skill / identity / deploy). Reads
// ``GET /v1/audit?…&cursor=…&limit=…`` via the facade. The unified
// stream is intentionally simple: the in-product table answers "who
// did what, when" — the SIEM export endpoint stays the source of
// truth for forensic verification.
//
// The page model is keyset cursor: the server hands back ``next_cursor``
// when there's more, ``has_more`` is the explicit signal for the "Load
// more" CTA. Filters (action prefix, actor_user_id, since/until) refetch
// page 1 and reset the cursor stack.

import {
  Badge,
  Button,
  Card,
  Field,
  TextInput,
} from "@enterprise-search/design-system";
import {
  type FormEvent,
  type ReactElement,
  useCallback,
  useEffect,
  useState,
} from "react";
import type { AuditEvent } from "@enterprise-search/api-types";
import { listAuditEvents } from "../../api/auditApi";
import type { RequestIdentity } from "../../api/config";

interface FilterState {
  action: string;
  actor: string;
  since: string;
  until: string;
}

const EMPTY_FILTERS: FilterState = {
  action: "",
  actor: "",
  since: "",
  until: "",
};

const PAGE_SIZE = 50;

export function AuditLogSettings({
  identity,
  isAdmin,
}: {
  identity: RequestIdentity;
  isAdmin: boolean;
}): ReactElement {
  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS);
  const [draftFilters, setDraftFilters] = useState<FilterState>(EMPTY_FILTERS);
  const [rows, setRows] = useState<AuditEvent[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [degradedStreams, setDegradedStreams] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchPage = useCallback(
    async (activeFilters: FilterState, next?: string | null): Promise<void> => {
      setLoading(true);
      setError(null);
      try {
        const response = await listAuditEvents(identity, {
          action: activeFilters.action || undefined,
          actor_user_id: activeFilters.actor || undefined,
          since: activeFilters.since || undefined,
          until: activeFilters.until || undefined,
          cursor: next ?? undefined,
          limit: PAGE_SIZE,
        });
        if (next) {
          setRows((existing) => [...existing, ...response.rows]);
        } else {
          setRows(response.rows);
        }
        setCursor(response.next_cursor);
        setHasMore(response.has_more);
        setDegradedStreams(response.degraded_streams ?? []);
      } catch (err) {
        setError(toMessage(err, "Could not load audit events"));
      } finally {
        setLoading(false);
      }
    },
    [identity],
  );

  useEffect(() => {
    if (!isAdmin) return;
    void fetchPage(filters, null);
  }, [filters, fetchPage, isAdmin]);

  const onApplyFilters = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      setFilters(draftFilters);
    },
    [draftFilters],
  );

  const onResetFilters = useCallback(() => {
    setDraftFilters(EMPTY_FILTERS);
    setFilters(EMPTY_FILTERS);
  }, []);

  const onLoadMore = useCallback(() => {
    if (!cursor || !hasMore) return;
    void fetchPage(filters, cursor);
  }, [cursor, fetchPage, filters, hasMore]);

  if (!isAdmin) {
    return (
      <div className="settings-section" data-section="audit-log">
        <header className="settings-section__header">
          <h2>Audit log</h2>
        </header>
        <Card>
          <p className="settings-section__hint">
            The audit log is visible to workspace admins only.
          </p>
        </Card>
      </div>
    );
  }

  return (
    <div className="settings-section" data-section="audit-log">
      <header className="settings-section__header">
        <div>
          <h2>Audit log</h2>
          <p className="settings-section__hint">
            Member, connector, skill, and deploy actions across this workspace.
            Events are append-only and exportable to your SIEM (Settings →
            Privacy & data).
          </p>
        </div>
      </header>

      <Card>
        <form
          className="audit-log__filters"
          onSubmit={onApplyFilters}
          aria-label="Filter audit events"
        >
          <Field label="Action">
            <TextInput
              value={draftFilters.action}
              placeholder="e.g. invitation.create"
              onChange={(e) =>
                setDraftFilters((d) => ({ ...d, action: e.target.value }))
              }
            />
          </Field>
          <Field label="Actor user id">
            <TextInput
              value={draftFilters.actor}
              placeholder="usr_…"
              onChange={(e) =>
                setDraftFilters((d) => ({ ...d, actor: e.target.value }))
              }
            />
          </Field>
          <Field label="Since (UTC)">
            <TextInput
              type="datetime-local"
              value={draftFilters.since}
              onChange={(e) =>
                setDraftFilters((d) => ({
                  ...d,
                  since: toIsoOrEmpty(e.target.value),
                }))
              }
            />
          </Field>
          <Field label="Until (UTC)">
            <TextInput
              type="datetime-local"
              value={draftFilters.until}
              onChange={(e) =>
                setDraftFilters((d) => ({
                  ...d,
                  until: toIsoOrEmpty(e.target.value),
                }))
              }
            />
          </Field>
          <div className="audit-log__filter-actions">
            <Button type="submit" variant="primary">
              Apply
            </Button>
            <Button type="button" variant="ghost" onClick={onResetFilters}>
              Reset
            </Button>
          </div>
        </form>
      </Card>

      <Card>
        {error ? (
          <div className="audit-log__error" role="alert">
            {error}
          </div>
        ) : null}
        {degradedStreams.length > 0 ? (
          <div className="audit-log__warning" role="status">
            One or more streams are temporarily unavailable:{" "}
            {degradedStreams.join(", ")}. Results may be incomplete.
          </div>
        ) : null}
        <table className="settings-table audit-log__table">
          <thead>
            <tr>
              <th scope="col">When</th>
              <th scope="col">Stream</th>
              <th scope="col">Actor</th>
              <th scope="col">Action</th>
              <th scope="col">Resource</th>
              <th scope="col">Outcome</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && !loading ? (
              <tr>
                <td colSpan={6} className="audit-log__empty">
                  No audit events match.
                </td>
              </tr>
            ) : (
              rows.map((event) => (
                <AuditRow
                  key={`${event.stream}:${event.audit_id}`}
                  event={event}
                />
              ))
            )}
            {loading ? (
              <tr>
                <td colSpan={6} className="audit-log__loading">
                  Loading…
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
        {hasMore && cursor ? (
          <div className="audit-log__more">
            <Button
              type="button"
              variant="ghost"
              onClick={onLoadMore}
              disabled={loading}
            >
              Load more
            </Button>
          </div>
        ) : null}
      </Card>
    </div>
  );
}

function AuditRow({ event }: { event: AuditEvent }): ReactElement {
  return (
    <tr>
      <td>
        <time dateTime={event.created_at} title={event.created_at}>
          {formatTimestamp(event.created_at)}
        </time>
      </td>
      <td>
        <Badge tone="neutral">{shortStream(event.stream)}</Badge>
      </td>
      <td>
        {event.actor_user_id ?? (
          <span className="audit-log__cell--muted">{event.actor_kind}</span>
        )}
      </td>
      <td>
        <code className="audit-log__action">{event.action}</code>
      </td>
      <td>
        <span className="audit-log__resource">
          {event.resource_type}
          {event.resource_id ? ` · ${event.resource_id}` : null}
        </span>
      </td>
      <td>
        <Badge tone={badgeToneForOutcome(event.outcome)}>{event.outcome}</Badge>
      </td>
    </tr>
  );
}

function shortStream(stream: AuditEvent["stream"]): string {
  switch (stream) {
    case "mcp_audit_events":
      return "mcp";
    case "skill_audit_events":
      return "skill";
    case "identity_audit_events":
      return "identity";
    case "deploy_audit_events":
      return "deploy";
  }
}

function badgeToneForOutcome(
  outcome: AuditEvent["outcome"],
): "success" | "warning" | "danger" {
  switch (outcome) {
    case "success":
      return "success";
    case "denied":
      return "warning";
    case "failure":
      return "danger";
  }
}

function formatTimestamp(iso: string): string {
  // Lean on the user's locale + the user's timezone (Settings →
  // Profile sets a tz; the browser already respects it via Intl).
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function toIsoOrEmpty(value: string): string {
  // ``datetime-local`` produces a naive timestamp in the user's local
  // tz (no offset). The backend wants RFC 3339; we suffix Z so the
  // server interprets the input as UTC. Operators reading audit logs
  // typically reason in UTC, so this is the least-surprise default.
  if (!value) return "";
  return `${value}:00Z`;
}

function toMessage(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}
