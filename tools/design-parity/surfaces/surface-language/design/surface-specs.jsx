/* global React */
/* Payloads + SurfaceSpecs for the studio surface language page.
   Specs are dot-path only — no expressions, no handlers, no URLs (PRD-01). */

const PAYOUT_ROWS = [
  {
    id: "tx_8f21a",
    who: "nova.eth",
    role: "Design",
    amount: 3100,
    status: "staged",
    when: "07-27 11:42",
  },
  {
    id: "tx_8f21b",
    who: "kira.eth",
    role: "Protocol",
    amount: 4250,
    status: "staged",
    when: "07-27 11:42",
  },
  {
    id: "tx_8f21c",
    who: "0xlune",
    role: "Community",
    amount: 1800,
    status: "signed",
    when: "07-27 09:18",
  },
  {
    id: "tx_8f21d",
    who: "mira.eth",
    role: "Growth",
    amount: 2400,
    status: "staged",
    when: "07-27 11:43",
  },
  {
    id: "tx_8f21e",
    who: "dev.tomo",
    role: "Protocol",
    amount: 5200,
    status: "signed",
    when: "07-26 16:04",
  },
  {
    id: "tx_8f21f",
    who: "sable.eth",
    role: "Ops",
    amount: 1450,
    status: "held",
    when: "07-26 15:55",
  },
  {
    id: "tx_8f220",
    who: "rin.eth",
    role: "Design",
    amount: 2750,
    status: "signed",
    when: "07-26 12:31",
  },
  {
    id: "tx_8f221",
    who: "juno.eth",
    role: "Docs",
    amount: 900,
    status: "staged",
    when: "07-26 10:07",
  },
];
const PAYOUT_SPEC = {
  archetype: "table",
  title_path: "batch.label",
  subtitle_path: "batch.window",
  items_path: "rows",
  columns: [
    { label: "Tx", path: "id", format: "id" },
    { label: "Contributor", path: "who", format: "text" },
    { label: "Role", path: "role", format: "text" },
    { label: "Amount", path: "amount", format: "currency", align: "end" },
    { label: "Status", path: "status", format: "status" },
    { label: "Staged", path: "when", format: "datetime" },
  ],
  link: { label: "Open batch in Safe", url_path: "batch.url" },
};
const PAYOUT_CHANGES = [
  { row: 0, field: "amount", old: "3,100.00", next: "3,400.00" },
  { row: 3, field: "status", old: "staged", next: "held" },
  { row: 7, field: "amount", old: "900.00", next: "1,200.00" },
];

const FORECAST_COLS = [
  { label: "Month", path: "month", format: "text" },
  { label: "Region", path: "region", format: "text" },
  { label: "Bookings", path: "bookings", format: "number", align: "end" },
  { label: "Forecast", path: "forecast", format: "number", align: "end" },
];
const FORECAST_ROWS = [
  { month: "2026-05", region: "EMEA", bookings: 128400, forecast: 141000 },
  { month: "2026-05", region: "AMER", bookings: 204100, forecast: 219500 },
  { month: "2026-06", region: "EMEA", bookings: 133800, forecast: 148200 },
  { month: "2026-06", region: "AMER", bookings: 211600, forecast: 228000 },
  { month: "2026-07", region: "EMEA", bookings: 139500, forecast: 156700 },
  { month: "2026-07", region: "AMER", bookings: 218900, forecast: 240400 },
];

const OPP_FIELDS = [
  { key: "account", label: "Account", value: "Northwind Logistics" },
  { key: "stage", label: "Stage", value: "Discovery" },
  { key: "amount", label: "Amount", value: "48,000.00 USD", numeric: true },
  { key: "close", label: "Close date", value: "2026-08-14", numeric: true },
  { key: "owner", label: "Owner", value: "mira.eth" },
  {
    key: "next",
    label: "Next step",
    value: "Security review with their platform team",
  },
];
const OPP_CHANGES = [
  {
    key: "stage",
    label: "Stage",
    old: "Discovery",
    next: "Negotiation",
    src: "call transcript · 07-27",
  },
  {
    key: "amount",
    label: "Amount",
    old: "48,000.00 USD",
    next: "61,500.00 USD",
    src: "quote v3",
  },
  {
    key: "close",
    label: "Close date",
    old: "2026-08-14",
    next: "2026-09-30",
    src: "call transcript · 07-27",
  },
];
const OPP_SPEC = {
  archetype: "record",
  title_path: "opportunity.name",
  subtitle_path: "opportunity.account.name",
  fields: [
    { label: "Account", path: "opportunity.account.name" },
    { label: "Stage", path: "opportunity.stage" },
    { label: "Amount", path: "opportunity.amount", format: "currency" },
    { label: "Close date", path: "opportunity.close_date", format: "date" },
    { label: "Owner", path: "opportunity.owner.name" },
    { label: "Next step", path: "opportunity.next_step" },
  ],
  link: { label: "Open in Salesforce", url_path: "opportunity.web_url" },
};

const MAIL_BODY = [
  {
    t: "Launch Week payouts are staged and the AMA recap is drafted. Nothing has left the treasury yet — the batch is waiting on a signature.",
  },
  {
    t: "This week's batch covers 8 contributors for a total of ",
    diff: [{ o: "21,850", n: "22,150" }],
    t2: " USDC, paid from the ops safe. Two line items moved after the Thursday sync.",
  },
  {
    t: "The recap thread goes out an hour after the transfers confirm, so the numbers in it are the ones actually on chain.",
  },
];
const MAIL_SPEC = {
  archetype: "message",
  title_path: "draft.subject",
  subtitle_path: "draft.to",
  body_path: "draft.body_text",
  fields: [
    { label: "To", path: "draft.to" },
    { label: "Cc", path: "draft.cc" },
    { label: "From", path: "draft.from" },
  ],
  link: { label: "Open draft in Gmail", url_path: "draft.web_url" },
};

const DOC_OUTLINE = ["What shipped", "Numbers", "Open questions", "Next cycle"];
const DOC_SPEC = {
  archetype: "doc",
  title_path: "page.title",
  subtitle_path: "page.breadcrumb",
  body_path: "page.blocks",
  link: { label: "Open in Notion", url_path: "page.url" },
};

const BOARD_COLS = [
  {
    name: "Triage",
    cards: [
      { t: "Payout CSV drops the memo column", m: "LW-208 · nova.eth" },
      { t: "Safe nonce mismatch on retry", m: "LW-211 · unassigned" },
    ],
  },
  {
    name: "In progress",
    cards: [
      {
        t: "Stage transfers from the contributor sheet",
        m: "LW-142 · dev.tomo",
        chg: true,
      },
      { t: "Recap thread draft", m: "LW-190 · 0xlune" },
    ],
  },
  {
    name: "In review",
    cards: [{ t: "Approval gate copy pass", m: "LW-177 · rin.eth" }],
  },
  {
    name: "Done",
    cards: [
      { t: "Event log export", m: "LW-160 · kira.eth" },
      { t: "Cycle 14 retro notes", m: "LW-151 · juno.eth" },
    ],
  },
];
const BOARD_SPEC = {
  archetype: "board",
  title_path: "cycle.name",
  subtitle_path: "cycle.team.name",
  groups_path: "cycle.columns",
  group_label_path: "name",
  items_path: "issues",
  columns: [
    { label: "Title", path: "title" },
    { label: "Meta", path: "identifier" },
  ],
  link: { label: "Open cycle in Linear", url_path: "cycle.url" },
};

const GENERIC_PAYLOAD = [
  { key: "incident_number", label: "Incident Number", value: "4127" },
  { key: "title", label: "Title", value: "Elevated 5xx on payouts-api" },
  { key: "status", label: "Status", value: "acknowledged" },
  { key: "urgency", label: "Urgency", value: "high" },
  {
    key: "created_at",
    label: "Created At",
    value: "2026-07-28T09:12:04Z",
    numeric: true,
  },
  { key: "service", label: "Service", value: "{ 6 fields }" },
  { key: "assignments", label: "Assignments", value: "2 items" },
  { key: "html_url", label: "Html Url", value: "https://…/incidents/4127" },
];

const SURFACES = [
  {
    id: "payouts",
    tab: "table://",
    kicker: "Table",
    tier: 1,
    src: "oklch(0.76 0.1 158)",
    uri: "table://safe/batch/0x9f21",
    title: "Launch Week payout batch",
    sub: "8 transfers · ops safe · 2026-07-27",
    tool: "safe.batch.read",
    spec: PAYOUT_SPEC,
    chips: [{ t: "21,850 USDC" }, { t: "8 rows" }],
    approve: "Approve 3 changes to the batch",
  },
  {
    id: "forecast",
    tab: "dataset artifact",
    kicker: "Dataset artifact",
    tier: 0,
    src: "oklch(0.76 0.1 232)",
    uri: "artifact://dataset/forecast",
    title: "forecast",
    sub: "forecast.csv · text/csv",
    tool: "publish_artifact",
    chips: [{ t: "r1" }, { t: "6 rows × 4 cols" }, { t: "1.1 KB" }],
    approve: "Save patched revision",
  },
  {
    id: "opp",
    tab: "record://",
    kicker: "Record",
    tier: 1,
    src: "oklch(0.76 0.1 258)",
    uri: "record://salesforce/opportunity/006Ab",
    title: "Northwind — platform expansion",
    sub: "Northwind Logistics · Q3 renewal",
    tool: "salesforce.opportunity.read",
    spec: OPP_SPEC,
    chips: [{ t: "61,500 USD" }],
    approve: "Apply 3 field changes",
  },
  {
    id: "mail",
    tab: "message://",
    kicker: "Message",
    tier: 1,
    src: "oklch(0.76 0.1 28)",
    uri: "message://gmail/draft/18f2c",
    title: "Launch Week payouts + AMA recap",
    sub: "to community@0xcopilot.tech",
    tool: "gmail.draft.read",
    spec: MAIL_SPEC,
    chips: [{ t: "draft" }],
    approve: "Send message",
  },
  {
    id: "doc",
    tab: "doc://",
    kicker: "Doc",
    tier: 2,
    src: "oklch(0.76 0.1 300)",
    uri: "doc://notion/page/9c41",
    title: "AMA recap — Launch Week",
    sub: "Community / Launch Week / Recaps",
    tool: "notion.page.read",
    spec: DOC_SPEC,
    chips: [{ t: "4 sections" }],
    approve: "Publish 1 edited block",
  },
  {
    id: "board",
    tab: "board://",
    kicker: "Board",
    tier: 2,
    src: "oklch(0.76 0.1 288)",
    uri: "board://linear/cycle/14",
    title: "Cycle 14 — Launch Week",
    sub: "Platform · 7 issues",
    tool: "linear.cycle.read",
    spec: BOARD_SPEC,
    chips: [{ t: "7 issues" }],
    approve: "Move 1 issue",
  },
  {
    id: "generic",
    tab: "no spec",
    kicker: "Incident",
    tier: 3,
    src: "oklch(0.79 0.1 78)",
    uri: "incident://pagerduty/4127",
    title: "Elevated 5xx on payouts-api",
    sub: "pagerduty · incident 4127",
    tool: "pagerduty.incident.read",
    chips: [{ t: "acknowledged" }],
  },
];

const TIER_LABEL = {
  0: {
    t: "run artifact",
    d: "A run artifact, not a tool payload — same table language.",
  },
  1: {
    t: "curated spec",
    d: "One of the 12 hand-written specs shipped for tools we know.",
  },
  2: {
    t: "generated · cached",
    d: "A small model wrote the spec once; it is cached per tool.",
  },
  3: {
    t: "no spec",
    d: "Nothing matched. The generic view is a real view, not an error.",
  },
};

const LANG_RULES = [
  {
    n: "01",
    b: "Dot-paths only.",
    t: "A spec maps payload paths to roles. No computed values — so no total row unless the payload carries one.",
  },
  {
    n: "02",
    b: "200 rows, then a cap line.",
    t: "Above 50 columns the table windows instead of painting. The cap is stated in the footer, never silent.",
  },
  {
    n: "03",
    b: "Links are url_path.",
    t: "Resolved out of the payload and host-sanitized. A spec can never carry a destination.",
  },
  {
    n: "04",
    b: "Unknown degrades.",
    t: "An unknown archetype falls to the generic view. Nothing in this pane ever renders as an error.",
  },
];

Object.assign(window, {
  PAYOUT_ROWS,
  PAYOUT_SPEC,
  PAYOUT_CHANGES,
  FORECAST_COLS,
  FORECAST_ROWS,
  OPP_FIELDS,
  OPP_CHANGES,
  MAIL_BODY,
  DOC_OUTLINE,
  BOARD_COLS,
  GENERIC_PAYLOAD,
  SURFACES,
  TIER_LABEL,
  LANG_RULES,
  OPP_SPEC,
  MAIL_SPEC,
  DOC_SPEC,
  BOARD_SPEC,
});
