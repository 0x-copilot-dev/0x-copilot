/* global React */
/* =========================================================================
   0xCopilot — icons, brand mark, and mock data
   ========================================================================= */

let __mid = 0;
function Mark({ size = 26 }) {
  const id = React.useMemo(() => `tg${++__mid}`, []);
  const blade = "M200 96q46 10 54 60-28-8-54-24Z";
  return (
    <svg viewBox="0 0 400 400" width={size} height={size} aria-hidden="true">
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#9bd4ff" />
          <stop offset="1" stopColor="#4593d8" />
        </linearGradient>
      </defs>
      <g fill={`url(#${id})`}>
        {[0, 60, 120, 180, 240, 300].map((r) => (
          <path key={r} d={blade} transform={`rotate(${r} 200 200)`} />
        ))}
      </g>
      <circle
        cx="200"
        cy="200"
        r="20"
        fill="#0b0a0e"
        stroke={`url(#${id})`}
        strokeWidth="10"
      />
    </svg>
  );
}

const S = {
  width: "1em",
  height: "1em",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.7,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  viewBox: "0 0 24 24",
};
const Icon = {
  run: () => (
    <svg {...S}>
      <rect x="3" y="3" width="18" height="18" rx="4" />
      <path d="M10 9l5 3-5 3z" />
    </svg>
  ),
  activity: () => (
    <svg {...S}>
      <path d="M3 12h4l2.5 7 5-14L17 12h4" />
    </svg>
  ),
  plug: () => (
    <svg {...S}>
      <path d="M9 3v6M15 3v6M6 9h12v3a6 6 0 0 1-12 0z M12 18v3" />
    </svg>
  ),
  skill: () => (
    <svg {...S}>
      <path d="M12 3l2.1 5.3L20 10l-5.9 1.7L12 17l-2.1-5.3L4 10l5.9-1.7z" />
    </svg>
  ),
  gear: () => (
    <svg {...S}>
      <circle cx="12" cy="12" r="3.2" />
      <path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9L17 7M7 17l-2.1 2.1" />
    </svg>
  ),
  search: () => (
    <svg {...S}>
      <circle cx="11" cy="11" r="7" />
      <path d="M20 20l-3.5-3.5" />
    </svg>
  ),
  plus: () => (
    <svg {...S}>
      <path d="M12 5v14M5 12h14" />
    </svg>
  ),
  check: () => (
    <svg {...S}>
      <path d="M5 12l5 5L20 7" />
    </svg>
  ),
  x: () => (
    <svg {...S}>
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  ),
  chevR: () => (
    <svg {...S}>
      <path d="M9 6l6 6-6 6" />
    </svg>
  ),
  chevD: () => (
    <svg {...S}>
      <path d="M6 9l6 6 6-6" />
    </svg>
  ),
  back: () => (
    <svg {...S}>
      <path d="M15 6l-6 6 6 6" />
    </svg>
  ),
  key: () => (
    <svg {...S}>
      <circle cx="8" cy="14" r="4" />
      <path d="M11 12l9-9 2 2-2 2 2 2-2 2-2-2-3 3" />
    </svg>
  ),
  chip: () => (
    <svg {...S}>
      <rect x="6" y="6" width="12" height="12" rx="2" />
      <path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3" />
    </svg>
  ),
  sliders: () => (
    <svg {...S}>
      <path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M18 18h2" />
      <circle cx="15" cy="6" r="2" />
      <circle cx="9" cy="12" r="2" />
      <circle cx="15" cy="18" r="2" />
    </svg>
  ),
  lock: () => (
    <svg {...S}>
      <rect x="4" y="10" width="16" height="11" rx="2.5" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" />
    </svg>
  ),
  bell: () => (
    <svg {...S}>
      <path d="M6 16V11a6 6 0 1 1 12 0v5l1.6 2H4.4z" />
      <path d="M10 21a2 2 0 0 0 4 0" />
    </svg>
  ),
  user: () => (
    <svg {...S}>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21c1.4-4 4.4-6 8-6s6.6 2 8 6" />
    </svg>
  ),
  sun: () => (
    <svg {...S}>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.4 1.4M17.6 17.6L19 19M5 19l1.4-1.4M17.6 6.4L19 5" />
    </svg>
  ),
  cmd: () => (
    <svg {...S}>
      <path d="M9 6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3z" />
    </svg>
  ),
  trash: () => (
    <svg {...S}>
      <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" />
    </svg>
  ),
  download: () => (
    <svg {...S}>
      <path d="M12 4v11M7 10l5 5 5-5M4 20h16" />
    </svg>
  ),
  external: () => (
    <svg {...S}>
      <path d="M14 4h6v6M20 4l-9 9M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5" />
    </svg>
  ),
  warn: () => (
    <svg {...S}>
      <path d="M12 3l10 17H2z" />
      <path d="M12 9v5M12 17.5v.5" />
    </svg>
  ),
  send: () => (
    <svg {...S}>
      <path d="M4 12l16-8-6 16-3.5-6.5z" />
    </svg>
  ),
  stepBack: () => (
    <svg {...S}>
      <path d="M18 6L9 12l9 6zM6 5v14" />
    </svg>
  ),
  stepFwd: () => (
    <svg {...S}>
      <path d="M6 6l9 6-9 6zM18 5v14" />
    </svg>
  ),
  globe: () => (
    <svg {...S}>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18" />
    </svg>
  ),
  refresh: () => (
    <svg {...S}>
      <path d="M4 12a8 8 0 0 1 13.7-5.6L20 8M20 4v4h-4M20 12a8 8 0 0 1-13.7 5.6L4 16M4 20v-4h4" />
    </svg>
  ),
  eye: () => (
    <svg {...S}>
      <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  ),
  folder: () => (
    <svg {...S}>
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </svg>
  ),
  chats: () => (
    <svg {...S}>
      <path d="M4 5h16v10H9l-4 4z" />
    </svg>
  ),
  wallet: () => (
    <svg {...S}>
      <rect x="3" y="6" width="18" height="13" rx="3" />
      <path d="M21 10h-5a2.5 2.5 0 0 0 0 5h5" />
      <path d="M17 6V5a2 2 0 0 0-2-2H6a3 3 0 0 0-3 3" />
    </svg>
  ),
  doc: () => (
    <svg {...S}>
      <path d="M6 3h8l4 4v14H6z" />
      <path d="M14 3v4h4" />
    </svg>
  ),
  clock: () => (
    <svg {...S}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </svg>
  ),
  shield: () => (
    <svg {...S}>
      <path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" />
    </svg>
  ),
  pause: () => (
    <svg {...S}>
      <path d="M8 5v14M16 5v14" />
    </svg>
  ),
  play: () => (
    <svg {...S}>
      <path d="M7 5l12 7-12 7z" />
    </svg>
  ),
  dots: () => (
    <svg {...S}>
      <circle cx="5" cy="12" r="1.3" />
      <circle cx="12" cy="12" r="1.3" />
      <circle cx="19" cy="12" r="1.3" />
    </svg>
  ),
  coin: () => (
    <svg {...S}>
      <ellipse cx="12" cy="6.5" rx="8" ry="3.2" />
      <path d="M4 6.5v11c0 1.8 3.6 3.2 8 3.2s8-1.4 8-3.2v-11M4 12c0 1.8 3.6 3.2 8 3.2s8-1.4 8-3.2" />
    </svg>
  ),
  bolt: () => (
    <svg {...S}>
      <path d="M13 2L4 14h7l-1 8 9-12h-7z" />
    </svg>
  ),
  arrowR: () => (
    <svg {...S}>
      <path d="M5 12h14M13 6l6 6-6 6" />
    </svg>
  ),
};

/* ===== Workspace: timeline lanes + beads (Launch Week run) ===== */
const LANES = [
  { name: "Safe", cls: "l0", color: "#5fb2ec" },
  { name: "Sheets", cls: "l1", color: "#57c785" },
  { name: "X thread", cls: "l2", color: "#9bd4ff" },
  { name: "Discord", cls: "l3", color: "#98959f" },
];
const BEADS = [
  { lane: 0, x: 14, t: "11:36", app: "Safe", short: "connected · owners read" },
  { lane: 0, x: 30, t: "11:38", app: "Safe", short: "5 payouts batched" },
  {
    lane: 1,
    x: 44,
    t: "11:40",
    app: "Sheets",
    short: "tokenomics allocations read",
  },
  {
    lane: 1,
    x: 56,
    t: "11:41",
    app: "Sheets",
    short: "payout amounts written back",
  },
  {
    lane: 2,
    x: 68,
    t: "11:43",
    app: "X thread",
    short: "launch thread drafted",
  },
  {
    lane: 2,
    x: 80,
    t: "11:44",
    app: "X thread",
    short: "streaming body · 64%",
    live: true,
  },
  { lane: 3, x: 92, t: "11:47", app: "Discord", short: "AMA recap queued" },
];
const LIVE_X = 80;

/* the staged multisig payout batch shown in the sheet */
const STAGED = [
  {
    who: "dev.0xk",
    ini: "DK",
    role: "Core engineering",
    amount: "6,000",
    status: "signed",
  },
  {
    who: "nadia.eth",
    ini: "NA",
    role: "Product design",
    amount: "4,200",
    status: "signed",
  },
  {
    who: "mira.eth",
    ini: "MI",
    role: "Content & comms",
    amount: "2,750",
    status: "pending",
  },
  {
    who: "leo.base",
    ini: "LE",
    role: "Community",
    amount: "1,800",
    status: "queued",
  },
  {
    who: "cipher-audit",
    ini: "CA",
    role: "Security audit",
    amount: "9,500",
    status: "queued",
  },
];

const PLAN = [
  {
    s: "done",
    t: (
      <span>
        Read <b>tokenomics allocations</b> from Sheets
      </span>
    ),
  },
  {
    s: "done",
    t: (
      <span>
        Batch <b>5 contributor payouts</b> on Safe
      </span>
    ),
  },
  {
    s: "pending",
    t: (
      <span>
        Draft the <b>Launch Week thread</b> on X — streaming
      </span>
    ),
  },
  {
    s: "future",
    t: (
      <span>
        Post <b>AMA recap</b> to Discord
      </span>
    ),
  },
];

/* ===== BYOK provider keys ===== */
const PROVIDERS = [
  {
    id: "anthropic",
    name: "Anthropic",
    color: "#d97757",
    ini: "A",
    model: "Claude Sonnet 4.5",
    key: "sk-ant-•••• 4f2a",
    status: "connected",
  },
  {
    id: "openai",
    name: "OpenAI",
    color: "#0f9d76",
    ini: "O",
    model: "GPT-5",
    key: "sk-•••• 9c10",
    status: "connected",
  },
  {
    id: "openrouter",
    name: "OpenRouter",
    color: "#6b7280",
    ini: "OR",
    model: "Auto (router)",
    key: "sk-or-•••• 1b7e",
    status: "connected",
  },
  {
    id: "google",
    name: "Google AI",
    color: "#4285f4",
    ini: "G",
    model: null,
    key: null,
    status: "empty",
  },
  {
    id: "groq",
    name: "Groq",
    color: "#f55036",
    ini: "Gq",
    model: null,
    key: null,
    status: "empty",
  },
  {
    id: "xai",
    name: "xAI",
    color: "#1d1d1f",
    ini: "X",
    model: null,
    key: null,
    status: "empty",
  },
];

/* ===== Local models (Ollama-style) ===== */
const LOCAL_INSTALLED = [
  {
    id: "llama33",
    name: "Llama 3.3",
    param: "70B",
    size: "42 GB",
    note: "default local",
  },
  {
    id: "qwen25c",
    name: "Qwen 2.5 Coder",
    param: "32B",
    size: "20 GB",
    note: "code",
  },
];
const LOCAL_AVAILABLE = [
  {
    id: "deepseek",
    name: "DeepSeek-R1",
    param: "32B",
    size: "19 GB",
    note: "reasoning",
  },
  {
    id: "mistral3",
    name: "Mistral Small 3",
    param: "24B",
    size: "14 GB",
    note: "fast",
  },
  {
    id: "phi4",
    name: "Phi-4",
    param: "14B",
    size: "9.1 GB",
    note: "lightweight",
  },
  {
    id: "gemma3",
    name: "Gemma 3",
    param: "27B",
    size: "16 GB",
    note: "general",
  },
];

/* ===== Connectors / tools ===== */
const CONNECTORS = [
  {
    id: "safe",
    name: "Safe{Wallet}",
    color: "#12b886",
    ini: "◇",
    sub: "3-of-5 multisig · Base",
    perm: "act",
  },
  {
    id: "sheets",
    name: "Google Sheets",
    color: "#0f9d58",
    ini: "S",
    sub: "Treasury workbook",
    perm: "act",
  },
  {
    id: "x",
    name: "X",
    color: "#1d1d1f",
    ini: "𝕏",
    sub: "@0xcopilot · post + read",
    perm: "act",
  },
  {
    id: "discord",
    name: "Discord",
    color: "#5865f2",
    ini: "D",
    sub: "Community server · 4 channels",
    perm: "act",
  },
  {
    id: "fs",
    name: "Local files",
    color: "#e8b45e",
    ini: "▾",
    sub: "~/copilot/launch",
    perm: "act",
  },
  {
    id: "github",
    name: "GitHub",
    color: "#2b3137",
    ini: "G",
    sub: "read-only · 3 repos",
    perm: "read",
  },
];
const CONNECTOR_CATALOG = [
  {
    id: "notion",
    name: "Notion",
    color: "#2f3437",
    ini: "N",
    sub: "Docs & databases",
  },
  {
    id: "linear",
    name: "Linear",
    color: "#5e6ad2",
    ini: "L",
    sub: "Issues & projects",
  },
  {
    id: "slack",
    name: "Slack",
    color: "#4a154b",
    ini: "#",
    sub: "Channels & DMs",
  },
  {
    id: "gcal",
    name: "Google Calendar",
    color: "#4285f4",
    ini: "C",
    sub: "Events & scheduling",
  },
  {
    id: "dune",
    name: "Dune",
    color: "#f4603e",
    ini: "◔",
    sub: "On-chain analytics",
  },
  {
    id: "stripe",
    name: "Stripe",
    color: "#635bff",
    ini: "S",
    sub: "Payments & payouts",
  },
];

/* ===== Activity (run history) ===== */
const ACTIVITY = [
  {
    day: "Today",
    runs: [
      {
        title: "Launch Week ops",
        meta: "4 apps · 7 steps · awaiting 1 approval",
        time: "11:44",
        status: "running",
      },
      {
        title: "Weekly treasury reconciliation",
        meta: "Sheets, Safe, Dune · 12 steps · balanced",
        time: "09:02",
        status: "done",
      },
      {
        title: "Draft investor update",
        meta: "Docs · 5 steps · saved to Local files",
        time: "08:15",
        status: "done",
      },
    ],
  },
  {
    day: "Yesterday",
    runs: [
      {
        title: "Rebalance LP positions",
        meta: "paused — needed your approval on a swap",
        time: "18:30",
        status: "paused",
      },
      {
        title: "Triage new GitHub issues",
        meta: "GitHub · 9 steps · 3 labeled, 1 escalated",
        time: "14:07",
        status: "done",
      },
      {
        title: "Summarize Discord AMA",
        meta: "Discord · 4 steps · posted recap",
        time: "11:20",
        status: "done",
      },
    ],
  },
  {
    day: "Mon, Jul 14",
    runs: [
      {
        title: "Vendor invoice batch",
        meta: "stopped — you rejected 2 of 6 payouts",
        time: "16:44",
        status: "stopped",
      },
      {
        title: "Competitor launch digest",
        meta: "Web · 6 steps · saved 1 page",
        time: "10:03",
        status: "done",
      },
    ],
  },
];

/* ===== Notifications (consolidated) — desktop app channels ===== */
const NOTIF = [
  {
    id: "approval",
    nl: "Approval requested",
    ns: "The agent needs you to approve an action before it acts",
    d: { desktop: true, sound: true, email: false },
  },
  {
    id: "finished",
    nl: "Run finished",
    ns: "A background run completes end to end",
    d: { desktop: true, sound: false, email: true },
  },
  {
    id: "paused",
    nl: "Run paused / needs input",
    ns: "The agent is blocked and waiting on you",
    d: { desktop: true, sound: true, email: false },
  },
  {
    id: "error",
    nl: "Connector error",
    ns: "A tool auth expired or a sync failed",
    d: { desktop: true, sound: false, email: true },
  },
  {
    id: "spend",
    nl: "Spend threshold",
    ns: "Provider API spend crosses your monthly cap",
    d: { desktop: true, sound: false, email: true },
  },
  {
    id: "updates",
    nl: "Product updates",
    ns: "New features and release notes",
    d: { desktop: false, sound: false, email: false },
  },
];

const SHORTCUTS = [
  { l: "New run", k: ["⌘", "N"] },
  { l: "Command palette", k: ["⌘", "K"] },
  { l: "Approve action", k: ["⌘", "↵"] },
  { l: "Reject action", k: ["⌘", "⌫"] },
  { l: "Pause run", k: ["⌘", "."] },
  { l: "Rewind timeline", k: ["⌘", "←"] },
  { l: "Step forward", k: ["⌘", "→"] },
  { l: "Jump to live", k: ["⌘", "L"] },
  { l: "Switch mode", k: ["⌘", "M"] },
  { l: "Local model picker", k: ["⌘", "⇧", "M"] },
  { l: "Settings", k: ["⌘", ","] },
  { l: "Search activity", k: ["⌘", "⇧", "F"] },
];

/* ===== Chats (conversations) + Projects ===== */
const CHATS = [
  {
    id: "launch",
    title: "Launch Week ops",
    preview: "Streaming the launch thread",
    model: "Claude Sonnet 4.5",
    when: "now",
    status: "running",
    pinned: true,
    project: "launch",
  },
  {
    id: "recon",
    title: "Weekly treasury reconciliation",
    preview: "Balanced 3 accounts, flagged 1 variance",
    model: "Claude Sonnet 4.5",
    when: "2h",
    status: "done",
    project: "treasury",
  },
  {
    id: "investor",
    title: "Investor update — July",
    preview: "Draft saved to Local files",
    model: "Local · Llama 3.3 70B",
    when: "3h",
    status: "done",
    project: "launch",
  },
  {
    id: "lp",
    title: "Rebalance LP positions",
    preview: "Paused — a swap needs your approval",
    model: "Claude Sonnet 4.5",
    when: "1d",
    status: "paused",
    project: "treasury",
  },
  {
    id: "triage",
    title: "Triage new GitHub issues",
    preview: "Labeled 3, escalated 1",
    model: "Qwen 2.5 Coder 32B",
    when: "1d",
    status: "done",
    project: "growth",
  },
  {
    id: "ama",
    title: "Summarize Discord AMA",
    preview: "Posted recap to #announcements",
    model: "Claude Sonnet 4.5",
    when: "1d",
    status: "done",
    project: "launch",
  },
  {
    id: "digest",
    title: "Competitor launch digest",
    preview: "6 sources · saved 1 page",
    model: "Claude Sonnet 4.5",
    when: "Mon",
    status: "archived",
    project: "growth",
  },
  {
    id: "invoices",
    title: "Vendor invoice batch",
    preview: "You rejected 2 of 6 payouts",
    model: "Claude Sonnet 4.5",
    when: "Mon",
    status: "archived",
    project: "treasury",
  },
];
const PROJECTS = [
  {
    id: "launch",
    name: "Launch Week",
    desc: "GTM for the v2 launch",
    color: "#5fb2ec",
    chats: 3,
    files: 12,
  },
  {
    id: "treasury",
    name: "Treasury",
    desc: "Payments, runway & on-chain ops",
    color: "#57c785",
    chats: 3,
    files: 20,
  },
  {
    id: "growth",
    name: "Growth",
    desc: "Content, community & analytics",
    color: "#a98be0",
    chats: 2,
    files: 7,
  },
];
const PROJECT_FILES = [
  { n: "tokenomics.xlsx", m: "Sheets · edited 2d ago" },
  { n: "launch-brief.md", m: "Doc · edited 3d ago" },
  { n: "payout-batch.csv", m: "Export · 4h ago" },
  { n: "ama-notes.md", m: "Doc · 1d ago" },
];

Object.assign(window, {
  Mark,
  Icon,
  LANES,
  BEADS,
  LIVE_X,
  STAGED,
  PLAN,
  PROVIDERS,
  LOCAL_INSTALLED,
  LOCAL_AVAILABLE,
  CONNECTORS,
  CONNECTOR_CATALOG,
  ACTIVITY,
  NOTIF,
  SHORTCUTS,
  CHATS,
  PROJECTS,
  PROJECT_FILES,
});
