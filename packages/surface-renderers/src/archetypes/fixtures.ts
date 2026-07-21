import type {
  SurfaceDiff,
  SurfaceSpec,
  SurfaceState,
} from "../_shared/specTypes";

// Test fixtures mirroring the PRD-01 golden SurfaceSpec fixtures
// (services/ai-backend/.../surfaces/fixtures/*.spec.json) plus representative
// tool-output data, used by the archetype golden-render tests (PRD-03 AC1).

// --- record: linear_get_issue ---------------------------------------------

export const LINEAR_RECORD_SPEC: SurfaceSpec = {
  spec_version: 1,
  archetype: "record",
  source: { server: "seed:linear", tool: "get_issue" },
  title_path: "issue.title",
  subtitle_path: "issue.identifier",
  fields: [
    { label: "State", path: "issue.state.name", format: "badge" },
    { label: "Assignee", path: "issue.assignee.displayName", format: "user" },
    { label: "Priority", path: "issue.priorityLabel" },
    { label: "Updated", path: "issue.updatedAt", format: "datetime" },
  ],
  link: { label: "Open in Linear", url_path: "issue.url" },
};

export const LINEAR_RECORD_DATA = {
  issue: {
    title: "Fix login redirect loop",
    identifier: "ENG-1421",
    state: { name: "In Progress" },
    assignee: { displayName: "Sarah Chen" },
    priorityLabel: "High",
    updatedAt: "2026-07-20T10:00:00Z",
    url: "https://linear.app/acme/issue/ENG-1421",
  },
};

export const LINEAR_RECORD_STATE: SurfaceState = {
  spec: LINEAR_RECORD_SPEC,
  data: LINEAR_RECORD_DATA,
};

export const LINEAR_RECORD_DIFF: SurfaceDiff = {
  spec: LINEAR_RECORD_SPEC,
  changes: [
    { field: "issue.state.name", old: "Todo", new: "In Progress" },
    {
      field: "issue.assignee.displayName",
      old: "Unassigned",
      new: "Sarah Chen",
    },
    { field: "issue.priorityLabel", old: "Medium", new: "High" },
  ],
};

// --- table: github_list_issues --------------------------------------------

export const GITHUB_TABLE_SPEC: SurfaceSpec = {
  spec_version: 1,
  archetype: "table",
  source: { server: "seed:github", tool: "list_issues" },
  title_path: "repository.full_name",
  items_path: "issues",
  columns: [
    { label: "Number", path: "number", format: "number", align: "end" },
    { label: "Title", path: "title", align: "start" },
    { label: "State", path: "state", format: "badge" },
    { label: "Assignee", path: "assignee.login", format: "user" },
    { label: "Updated", path: "updated_at", format: "datetime" },
  ],
  link: { label: "Open on GitHub", url_path: "html_url" },
};

export const GITHUB_TABLE_DATA = {
  repository: { full_name: "acme/web" },
  html_url: "https://github.com/acme/web/issues",
  issues: [
    {
      number: 128,
      title: "Composer drops focus on send",
      state: "open",
      assignee: { login: "jdoe" },
      updated_at: "2026-07-19T09:30:00Z",
    },
    {
      number: 131,
      title: "Dark theme contrast on badges",
      state: "closed",
      assignee: { login: "mkim" },
      updated_at: "2026-07-18T14:05:00Z",
    },
  ],
};

export const GITHUB_TABLE_STATE: SurfaceState = {
  spec: GITHUB_TABLE_SPEC,
  data: GITHUB_TABLE_DATA,
};

// --- message: gmail_message -----------------------------------------------

export const GMAIL_MESSAGE_SPEC: SurfaceSpec = {
  spec_version: 1,
  archetype: "message",
  source: { server: "seed:gmail", tool: "get_message" },
  title_path: "message.subject",
  subtitle_path: "message.from",
  fields: [
    { label: "From", path: "message.from", format: "user" },
    { label: "To", path: "message.to" },
    { label: "Date", path: "message.date", format: "datetime" },
    { label: "Snippet", path: "message.snippet" },
  ],
  link: { label: "Open in Gmail", url_path: "message.url" },
};

export const GMAIL_MESSAGE_DATA = {
  message: {
    subject: "Renewal terms — Q4 wrap and FY27 path",
    from: "jordan.reyes@acme.com",
    to: "sam.park@yourco.com",
    date: "2026-07-20T08:15:00Z",
    snippet:
      "Thanks for the call this morning — sending the locked-price block.",
    url: "https://mail.google.com/mail/u/0/#inbox/abc123",
  },
};

export const GMAIL_MESSAGE_STATE: SurfaceState = {
  spec: GMAIL_MESSAGE_SPEC,
  data: GMAIL_MESSAGE_DATA,
};

export const GMAIL_MESSAGE_DIFF: SurfaceDiff = {
  spec: GMAIL_MESSAGE_SPEC,
  changes: [
    {
      field: "message.snippet",
      old: "Draft pending.",
      new: "Confirming the locked-price block from MSA §3.2.",
    },
  ],
};

// --- doc -------------------------------------------------------------------

export const DOC_SPEC: SurfaceSpec = {
  spec_version: 1,
  archetype: "doc",
  source: { server: "seed:notion", tool: "get_page" },
  title_path: "page.title",
  subtitle_path: "page.author",
  items_path: "page.sections",
  fields: [
    { label: "Heading", path: "heading" },
    { label: "Body", path: "body" },
  ],
};

export const DOC_DATA = {
  page: {
    title: "Q4 Renewal Playbook",
    author: "Revenue Ops",
    sections: [
      {
        heading: "Executive summary",
        body: "Locked-price block holds to FY27.",
      },
      { heading: "Risks", body: "Two accounts flagged for churn review." },
    ],
  },
};

export const DOC_STATE: SurfaceState = { spec: DOC_SPEC, data: DOC_DATA };

// --- board -----------------------------------------------------------------

export const BOARD_SPEC: SurfaceSpec = {
  spec_version: 1,
  archetype: "board",
  source: { server: "seed:linear", tool: "list_issues" },
  title_path: "board.name",
  items_path: "cards",
  group_by_path: "status",
  columns: [
    { label: "Title", path: "title" },
    { label: "Assignee", path: "assignee", format: "user" },
  ],
};

export const BOARD_DATA = {
  board: { name: "Sprint 42" },
  cards: [
    {
      title: "Wire archetype renderers",
      status: "In Progress",
      assignee: "Sarah",
    },
    { title: "Spec authoring skill", status: "Todo", assignee: "Marcus" },
    { title: "Golden fixtures", status: "In Progress", assignee: "Priya" },
  ],
};

export const BOARD_STATE: SurfaceState = { spec: BOARD_SPEC, data: BOARD_DATA };
