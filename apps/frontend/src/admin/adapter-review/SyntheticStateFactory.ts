// Synthetic state for the tier-2 adapter preview pane (Phase 7C).
//
// CRITICAL: every value here MUST be plausibly synthetic. Reviewers
// must never see tenant-private data (PRD §9.5.3). This module is the
// source of truth for what the preview pane mounts the candidate
// against — never real customer data, never anything fetched from a
// tenant connector.
//
// Well-known dummy values only: ``acme.example.com`` (per RFC 2606
// reserved test domain), ``Test User``, ``2026-01-01``, ``$10.00``.
// A test asserts that this output contains no real-PII patterns.

import type { LayoutTemplate } from "./types";

export interface SyntheticState {
  // The "current" view the adapter renders against ``renderCurrent``.
  readonly current: unknown;
  // The "diff" view the adapter renders against ``renderDiff``.
  readonly diff: unknown;
}

const SYNTHETIC_FORM: SyntheticState = {
  current: {
    resourceId: "acct_synthetic_001",
    saas: "Atlas Demo CRM",
    openUrl: "https://acme.example.com/accounts/synthetic_001",
    fields: {
      name: "Atlas Demo Account",
      owner: "Test User",
      stage: "Discovery",
      value: 10000,
      close_date: "2026-01-01",
      notes: "Synthetic record for adapter review. No real customer data.",
    },
  },
  diff: {
    resourceId: "acct_synthetic_001",
    saas: "Atlas Demo CRM",
    openUrl: "https://acme.example.com/accounts/synthetic_001",
    reasoning:
      "Stage advanced after the synthetic discovery call. Value adjusted to reflect new scope.",
    fieldChanges: [
      { field: "stage", old: "Discovery", new: "Proposal" },
      { field: "value", old: 10000, new: 15000 },
    ],
  },
};

const SYNTHETIC_TABLE: SyntheticState = {
  current: {
    resourceId: "list_synthetic_tickets",
    saas: "Atlas Demo Helpdesk",
    openUrl: "https://acme.example.com/tickets",
    columns: ["ticket", "title", "priority", "owner"],
    rows: [
      {
        ticket: "TKT-1001",
        title: "Synthetic onboarding issue",
        priority: "P2",
        owner: "Test User",
      },
      {
        ticket: "TKT-1002",
        title: "Synthetic billing question",
        priority: "P3",
        owner: "Test User",
      },
    ],
  },
  diff: {
    resourceId: "list_synthetic_tickets",
    saas: "Atlas Demo Helpdesk",
    openUrl: "https://acme.example.com/tickets",
    reasoning: "Two tickets re-prioritised after the synthetic triage pass.",
    fieldChanges: [
      { field: "TKT-1001.priority", old: "P2", new: "P1" },
      { field: "TKT-1002.priority", old: "P3", new: "P2" },
    ],
  },
};

const SYNTHETIC_KANBAN: SyntheticState = {
  current: {
    resourceId: "board_synthetic_sprint",
    saas: "Atlas Demo Tracker",
    openUrl: "https://acme.example.com/boards/synthetic_sprint",
    columns: [
      {
        column: "Backlog",
        cards: [
          { id: "CARD-1", title: "Synthetic backlog item", owner: "Test User" },
        ],
      },
      {
        column: "In progress",
        cards: [
          {
            id: "CARD-2",
            title: "Synthetic in-flight item",
            owner: "Test User",
          },
        ],
      },
      {
        column: "Done",
        cards: [],
      },
    ],
  },
  diff: {
    resourceId: "board_synthetic_sprint",
    saas: "Atlas Demo Tracker",
    openUrl: "https://acme.example.com/boards/synthetic_sprint",
    reasoning: "One card advanced after the synthetic standup.",
    fieldChanges: [{ field: "CARD-2.column", old: "In progress", new: "Done" }],
  },
};

const SYNTHETIC_DEFINITION_LIST: SyntheticState = {
  current: {
    resourceId: "page_synthetic_doc",
    saas: "Atlas Demo Wiki",
    openUrl: "https://acme.example.com/wiki/synthetic_doc",
    fields: {
      title: "Synthetic Runbook Page",
      author: "Test User",
      last_edited: "2026-01-01",
      tags: ["synthetic", "review-only", "no-real-data"],
      summary:
        "This is a synthetic wiki entry for adapter review. Contains no real customer data.",
    },
  },
  diff: {
    resourceId: "page_synthetic_doc",
    saas: "Atlas Demo Wiki",
    openUrl: "https://acme.example.com/wiki/synthetic_doc",
    reasoning: "Tag list rotated after the synthetic content audit.",
    fieldChanges: [
      {
        field: "tags",
        old: ["synthetic", "review-only", "no-real-data"],
        new: ["synthetic", "review-only", "audited"],
      },
    ],
  },
};

const SYNTHETIC_BY_TEMPLATE: Record<LayoutTemplate, SyntheticState> = {
  form: SYNTHETIC_FORM,
  table: SYNTHETIC_TABLE,
  kanban: SYNTHETIC_KANBAN,
  "definition-list": SYNTHETIC_DEFINITION_LIST,
};

export function syntheticStateFor(template: LayoutTemplate): SyntheticState {
  return SYNTHETIC_BY_TEMPLATE[template];
}

export function allSyntheticStates(): readonly SyntheticState[] {
  return Object.values(SYNTHETIC_BY_TEMPLATE);
}
