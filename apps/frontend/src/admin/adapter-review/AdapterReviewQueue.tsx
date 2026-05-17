// Admin review queue (Phase 7C).
//
// Lists tier-2 adapter candidates submitted by 7B. Filters by status /
// layout / scheme. Clicking a row hands the candidate id to ``onOpen``
// — the parent routes to ``AdapterReviewDetail``.

import type { ReactElement } from "react";
import { useEffect, useMemo, useState } from "react";

import {
  Badge,
  Card,
  Field,
  Select,
  TextInput,
} from "@enterprise-search/design-system";

import type { RequestIdentity } from "../../api/config";
import { errorMessage } from "../../utils/errors";

import { listAdapterReviewCandidates } from "./adapterReviewApi";
import {
  CANDIDATE_STATUSES,
  LAYOUT_TEMPLATES,
  type AdapterReviewCandidateSummary,
  type AdapterReviewListFilters,
  type CandidateStatus,
  type LayoutTemplate,
} from "./types";

export interface AdapterReviewQueueProps {
  readonly identity: RequestIdentity;
  readonly onOpen: (candidateId: string) => void;
}

type Filters = {
  status: CandidateStatus | "";
  layout: LayoutTemplate | "";
  scheme: string;
};

const INITIAL_FILTERS: Filters = { status: "", layout: "", scheme: "" };

export function AdapterReviewQueue({
  identity,
  onOpen,
}: AdapterReviewQueueProps): ReactElement {
  const [filters, setFilters] = useState<Filters>(INITIAL_FILTERS);
  const [rows, setRows] = useState<readonly AdapterReviewCandidateSummary[]>(
    [],
  );
  const [status, setStatus] = useState<"idle" | "loading" | "error">("idle");
  const [errorText, setErrorText] = useState<string | null>(null);

  const apiFilters: AdapterReviewListFilters = useMemo(() => {
    const out: AdapterReviewListFilters = {};
    if (filters.status !== "") Object.assign(out, { status: filters.status });
    if (filters.layout !== "") Object.assign(out, { layout: filters.layout });
    const trimmedScheme = filters.scheme.trim();
    if (trimmedScheme !== "") Object.assign(out, { scheme: trimmedScheme });
    return out;
  }, [filters]);

  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    setErrorText(null);
    listAdapterReviewCandidates(identity, apiFilters).then(
      (response) => {
        if (cancelled) return;
        const sorted = [...response.candidates].sort((a, b) =>
          b.submitted_at.localeCompare(a.submitted_at),
        );
        setRows(sorted);
        setStatus("idle");
      },
      (err: unknown) => {
        if (cancelled) return;
        setErrorText(errorMessage(err, "Failed to load review queue."));
        setStatus("error");
      },
    );
    return () => {
      cancelled = true;
    };
  }, [identity, apiFilters]);

  return (
    <section
      data-testid="adapter-review-queue"
      style={{ display: "flex", flexDirection: "column", gap: 12 }}
    >
      <header>
        <h1 style={{ margin: 0 }}>Adapter review queue</h1>
        <p style={{ marginTop: 4, color: "var(--color-text-muted)" }}>
          Tier-2 candidates submitted by tenants who passed the success
          criteria. Reviewers see synthetic samples only — never tenant data.
        </p>
      </header>

      <Card
        data-testid="adapter-review-filters"
        style={{
          display: "flex",
          gap: 12,
          flexWrap: "wrap",
          padding: 12,
        }}
      >
        <Field label="Status">
          <Select
            value={filters.status}
            data-testid="filter-status"
            onChange={(event) =>
              setFilters((prev) => ({
                ...prev,
                status: event.target.value as CandidateStatus | "",
              }))
            }
          >
            <option value="">All</option>
            {CANDIDATE_STATUSES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Layout">
          <Select
            value={filters.layout}
            data-testid="filter-layout"
            onChange={(event) =>
              setFilters((prev) => ({
                ...prev,
                layout: event.target.value as LayoutTemplate | "",
              }))
            }
          >
            <option value="">All</option>
            {LAYOUT_TEMPLATES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Scheme">
          <TextInput
            value={filters.scheme}
            placeholder="atlas://…"
            data-testid="filter-scheme"
            onChange={(event) =>
              setFilters((prev) => ({
                ...prev,
                scheme: event.target.value,
              }))
            }
          />
        </Field>
      </Card>

      {status === "loading" ? (
        <p data-testid="adapter-review-loading">Loading candidates…</p>
      ) : null}
      {status === "error" && errorText !== null ? (
        <p
          role="alert"
          data-testid="adapter-review-error"
          style={{ color: "var(--color-text-danger)" }}
        >
          {errorText}
        </p>
      ) : null}

      <Card style={{ padding: 0 }}>
        <table
          data-testid="adapter-review-table"
          style={{ width: "100%", borderCollapse: "collapse" }}
        >
          <thead>
            <tr>
              <th style={cellStyle}>Candidate</th>
              <th style={cellStyle}>Scheme</th>
              <th style={cellStyle}>Layout</th>
              <th style={cellStyle}>Origin</th>
              <th style={cellStyle}>Submitted</th>
              <th style={cellStyle}>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.candidate_id}
                data-testid={`adapter-review-row-${row.candidate_id}`}
                onClick={() => onOpen(row.candidate_id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onOpen(row.candidate_id);
                  }
                }}
                tabIndex={0}
                role="button"
                aria-label={`Open candidate ${row.candidate_id}`}
                style={{ cursor: "pointer" }}
              >
                <td style={cellStyle}>{row.candidate_id}</td>
                <td style={cellStyle}>{row.scheme}</td>
                <td style={cellStyle}>{row.layout_template}</td>
                <td style={cellStyle}>{row.origin_tenant_redacted}</td>
                <td style={cellStyle}>{row.submitted_at}</td>
                <td style={cellStyle}>
                  <Badge tone={toneForStatus(row.status)}>{row.status}</Badge>
                </td>
              </tr>
            ))}
            {rows.length === 0 && status === "idle" ? (
              <tr>
                <td
                  colSpan={6}
                  data-testid="adapter-review-empty"
                  style={{ ...cellStyle, color: "var(--color-text-muted)" }}
                >
                  No candidates match these filters.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </Card>
    </section>
  );
}

const cellStyle = {
  padding: "10px 12px",
  borderBottom: "1px solid var(--color-border)",
  textAlign: "left" as const,
  fontSize: "var(--font-size-sm)",
};

function toneForStatus(
  status: CandidateStatus,
): "neutral" | "success" | "warning" | "danger" | "accent" {
  switch (status) {
    case "approved":
      return "success";
    case "rejected":
      return "danger";
    case "changes-requested":
      return "warning";
    case "in-review":
      return "accent";
    default:
      return "neutral";
  }
}
