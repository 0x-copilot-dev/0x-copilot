/**
 * B5 — `/context` slash-command panel.
 *
 * Loads `/v1/agent/conversations/{id}/context` and renders the model's
 * context window, current input/output/cached_input tokens, headroom
 * (server-supplied integer percent), and a per-call + per-subagent +
 * compression-event breakdown.
 *
 * The panel is read-only and additive: opening it never starts a run
 * and never sends a message. All percentages come from the server —
 * the UI never re-derives them from `available_tokens / window_size`.
 */

import {
  Badge,
  Button,
  Card,
  classNames,
} from "@enterprise-search/design-system";
import type {
  ContextCallRow,
  ContextCompressionRow,
  ContextSubagentRow,
  ConversationContextResponse,
} from "@enterprise-search/api-types";
import type { ReactElement } from "react";
import { useCallback, useEffect, useState } from "react";

import { getConversationContext } from "../../../../api/agentApi";
import type { RequestIdentity } from "../../../../api/config";

export interface ContextPanelProps {
  conversationId: string;
  identity: RequestIdentity;
  onClose: () => void;
}

export function ContextPanel({
  conversationId,
  identity,
  onClose,
}: ContextPanelProps): ReactElement {
  const [data, setData] = useState<ConversationContextResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await getConversationContext(conversationId, identity));
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not load context");
    } finally {
      setLoading(false);
    }
  }, [conversationId, identity]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <aside className="details-panel" data-testid="context-panel">
      <header className="details-panel__header">
        <div>
          <h2>Context window</h2>
          <p className="details-panel__subtitle">
            Where tokens went in this conversation's last completed run.
          </p>
        </div>
        <div className="details-panel__header-actions">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => void reload()}
            disabled={loading}
          >
            {loading ? "Refreshing…" : "Refresh"}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onClose}
            aria-label="Close context panel"
          >
            ✕
          </Button>
        </div>
      </header>

      {error && (
        <Card tone="danger" className="details-panel__alert" role="alert">
          {error}
        </Card>
      )}

      {data && !error && (
        <div className="details-panel__body">
          <ContextWindowSection data={data} />
          <ContextCallTable rows={data.breakdown.by_call} />
          <ContextSubagentTable rows={data.breakdown.by_subagent} />
          <ContextCompressionList rows={data.breakdown.compression_events} />
        </div>
      )}
    </aside>
  );
}

function ContextWindowSection({
  data,
}: {
  data: ConversationContextResponse;
}): ReactElement {
  const { model, current } = data;
  const headroomLabel =
    current.headroom_pct === null ? "unknown" : `${current.headroom_pct}%`;
  const used = current.input_tokens + current.cached_input_tokens;
  const isEmpty = current.last_run_id === null;

  return (
    <Card tone="default" className="details-panel__section">
      <div className="details-panel__row">
        <strong>{model.name}</strong>
        <Badge tone="neutral" className="details-panel__badge">
          {model.provider}
        </Badge>
        {model.context_window_tokens !== null ? (
          <span className="details-panel__detail">
            {formatTokens(model.context_window_tokens)} window
          </span>
        ) : (
          <span className="details-panel__detail">window unknown</span>
        )}
      </div>

      {isEmpty ? (
        <p className="details-panel__empty">No completed runs yet.</p>
      ) : (
        <>
          <HeadroomGauge
            headroomPct={current.headroom_pct}
            used={used}
            window={model.context_window_tokens}
          />
          <dl className="details-panel__metrics">
            <Metric label="Input" value={current.input_tokens} />
            <Metric label="Cached input" value={current.cached_input_tokens} />
            <Metric label="Output" value={current.output_tokens} />
            <Metric
              label="Available"
              value={current.available_tokens}
              fallback="unknown"
            />
            <Metric label="Headroom" rawValue={headroomLabel} />
          </dl>
        </>
      )}
    </Card>
  );
}

function HeadroomGauge({
  headroomPct,
  used,
  window,
}: {
  headroomPct: number | null;
  used: number;
  window: number | null;
}): ReactElement {
  if (headroomPct === null || window === null) {
    return (
      <div
        className="details-panel__gauge details-panel__gauge--unknown"
        role="img"
        aria-label="Context window size unknown"
      >
        Window size unknown — pricing not configured for this model.
      </div>
    );
  }
  // Used percent is the inverse of headroom — both come straight from the
  // integers above with no float math.
  const usedPct = 100 - headroomPct;
  return (
    <div
      className="details-panel__gauge"
      role="img"
      aria-label={`${headroomPct}% headroom remaining`}
    >
      <div className="details-panel__gauge-track">
        <div
          className={classNames(
            "details-panel__gauge-fill",
            usedPct >= 90 && "details-panel__gauge-fill--danger",
            usedPct >= 75 &&
              usedPct < 90 &&
              "details-panel__gauge-fill--warning",
          )}
          style={{ width: `${usedPct}%` }}
        />
      </div>
      <div className="details-panel__gauge-legend">
        <span>{formatTokens(used)} used</span>
        <span>{formatTokens(window)} window</span>
      </div>
    </div>
  );
}

function ContextCallTable({
  rows,
}: {
  rows: ContextCallRow[];
}): ReactElement | null {
  if (rows.length === 0) return null;
  return (
    <Card tone="muted" className="details-panel__section">
      <h3>By model call</h3>
      <table className="details-panel__table">
        <thead>
          <tr>
            <th scope="col">Model</th>
            <th scope="col">Input</th>
            <th scope="col">Cached</th>
            <th scope="col">Output</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.event_id}>
              <td>{row.model_name}</td>
              <td>{formatTokens(row.input)}</td>
              <td>{formatTokens(row.cached_input)}</td>
              <td>{formatTokens(row.output)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function ContextSubagentTable({
  rows,
}: {
  rows: ContextSubagentRow[];
}): ReactElement | null {
  if (rows.length === 0) return null;
  return (
    <Card tone="muted" className="details-panel__section">
      <h3>By subagent</h3>
      <table className="details-panel__table">
        <thead>
          <tr>
            <th scope="col">Subagent</th>
            <th scope="col">Calls</th>
            <th scope="col">Total tokens</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.subagent_id}>
              <td>{row.name}</td>
              <td>{row.call_count}</td>
              <td>{formatTokens(row.total)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function ContextCompressionList({
  rows,
}: {
  rows: ContextCompressionRow[];
}): ReactElement | null {
  if (rows.length === 0) return null;
  return (
    <Card tone="muted" className="details-panel__section">
      <h3>Compression events</h3>
      <ul className="details-panel__list">
        {rows.map((row, index) => (
          <li key={`${row.at}-${index}`}>
            <strong>{row.strategy}</strong> · {formatTokens(row.before)} →{" "}
            {formatTokens(row.after)} ({_formatTimestamp(row.at)})
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Metric({
  label,
  value,
  rawValue,
  fallback,
}: {
  label: string;
  value?: number | null;
  rawValue?: string;
  fallback?: string;
}): ReactElement {
  let display: string;
  if (rawValue !== undefined) {
    display = rawValue;
  } else if (value === null || value === undefined) {
    display = fallback ?? "—";
  } else {
    display = formatTokens(value);
  }
  return (
    <div className="details-panel__metric">
      <dt>{label}</dt>
      <dd>{display}</dd>
    </div>
  );
}

function formatTokens(value: number): string {
  return `${value.toLocaleString()} tok`;
}

function _formatTimestamp(iso: string): string {
  try {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return iso;
    return date.toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}
