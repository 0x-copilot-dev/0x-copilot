// TeamDetailRoute — `/team/<id>` data binder. Pure host-side fetch +
// loading / error / ready states for the person-detail surface
// (sub-PRD §7.1). Activity tab is admin-only; the server returns an
// empty array for non-admin callers (§6.1), so this view simply
// renders whatever comes back.

import { useEffect, useState, type ReactElement } from "react";

import type {
  PersonDetailResponse,
  UserId,
} from "@enterprise-search/api-types";

import type { RequestIdentity } from "../../api/config";
import { fetchPerson } from "../../api/teamApi";
import { errorMessage } from "../../utils/errors";

type ViewState =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | { readonly kind: "ready"; readonly detail: PersonDetailResponse };

interface TeamDetailRouteProps {
  readonly identity: RequestIdentity;
  readonly personId: UserId;
  readonly onClose: () => void;
}

export function TeamDetailRoute({
  identity,
  personId,
  onClose,
}: TeamDetailRouteProps): ReactElement {
  const [state, setState] = useState<ViewState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });

    void (async () => {
      try {
        const detail = await fetchPerson(identity, personId);
        if (!cancelled) {
          setState({ kind: "ready", detail });
        }
      } catch (err) {
        if (!cancelled) {
          setState({
            kind: "error",
            message: errorMessage(err, "Could not load person."),
          });
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [identity, personId]);

  return (
    <section
      aria-label="Person detail"
      data-testid="team-detail-route"
      data-user-id={personId}
      data-state={state.kind}
      style={paneStyle}
    >
      <header style={headerStyle}>
        <button
          type="button"
          data-testid="team-detail-close"
          onClick={onClose}
          style={backButtonStyle}
        >
          ← Back to Team
        </button>
      </header>
      {state.kind === "loading" ? (
        <div data-testid="team-detail-loading">Loading…</div>
      ) : state.kind === "error" ? (
        <div role="alert" data-testid="team-detail-error">
          {state.message}
        </div>
      ) : (
        <div data-testid="team-detail-body">
          <h2 style={{ margin: "0 0 4px 0" }}>
            {state.detail.person.display_name}
            {state.detail.person.is_self ? " (you)" : ""}
          </h2>
          <div style={{ color: "var(--color-text-muted)", fontSize: 13 }}>
            {state.detail.person.email} · {state.detail.person.role} ·{" "}
            {state.detail.person.presence}
          </div>
          <section
            data-testid="team-detail-agents"
            data-count={state.detail.agents.length}
            style={sectionStyle}
          >
            <h3 style={subhStyle}>Agents ({state.detail.agents.length})</h3>
          </section>
          <section
            data-testid="team-detail-projects"
            data-count={state.detail.projects.length}
            style={sectionStyle}
          >
            <h3 style={subhStyle}>Projects ({state.detail.projects.length})</h3>
          </section>
          <section
            data-testid="team-detail-activity"
            data-count={state.detail.recent_activity.length}
            style={sectionStyle}
          >
            <h3 style={subhStyle}>
              Recent activity ({state.detail.recent_activity.length})
            </h3>
            {state.detail.recent_activity.length === 0 ? (
              <div style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
                No activity (admin-only projection).
              </div>
            ) : (
              <ul
                style={{ listStyle: "none", margin: 0, padding: 0 }}
                data-testid="team-detail-activity-list"
              >
                {state.detail.recent_activity.map((row, idx) => (
                  <li key={idx} style={{ padding: "6px 0", fontSize: 13 }}>
                    <time>{row.at}</time> — {row.summary}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </section>
  );
}

const paneStyle = {
  height: "100%",
  padding: 16,
  boxSizing: "border-box",
  overflow: "auto",
  background: "var(--color-bg)",
  color: "var(--color-text)",
} as const;

const headerStyle = {
  marginBottom: 12,
} as const;

const backButtonStyle = {
  background: "transparent",
  border: "none",
  color: "var(--color-accent)",
  cursor: "pointer",
  fontSize: 13,
  padding: 0,
} as const;

const sectionStyle = {
  marginTop: 16,
  borderTop: "1px solid var(--color-border)",
  paddingTop: 12,
} as const;

const subhStyle = {
  margin: "0 0 8px 0",
  fontSize: 14,
  fontWeight: 600,
} as const;
