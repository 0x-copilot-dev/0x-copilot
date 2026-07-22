/* design-parity · live PROJECTS render harness (vitest + jsdom)
 * =========================================================================
 * Renders the REAL shipping Projects surface to static HTML, one file per
 * design-harness state, so the browser extractor reads exactly the computed
 * styles the app produces. Design side: the vendored Claude Design mock at
 * design-kit/app-v3/index.html?dest=projects&state=<state>.
 *
 * WHICH component is "the live Projects surface" is not one answer — the two
 * hosts diverge, and that divergence is itself a finding:
 *
 *   • WEB (apps/frontend/src/features/projects/ProjectsRoute.tsx:840-947)
 *     renders its OWN host-side scaffold for the un-focused list
 *     (`.projects-grid3` / `.projects-card`, CSS at ProjectsRoute.tsx:961-1050).
 *     `<ProjectsDestination>` is mounted ONLY when a project is focused
 *     (ProjectsRoute.tsx:822-829) purely to host the `renderDetail` slot.
 *     The header comment states the reason at ProjectsRoute.tsx:19-23:
 *     the card name in the package grid is an `<ItemLink kind="project">`
 *     whose resolver used to render the literal label "Project".
 *
 *   • DESKTOP (apps/desktop/renderer/destinationBinders.tsx:563-568) mounts
 *     `<ProjectsDestination items={result} onRetry={retry} />` — the
 *     chat-surface CardGrid — with NO detail slot, no filter/create/star
 *     callbacks. Desktop therefore has no project detail view at all.
 *
 * So this harness emits THREE files:
 *   default.html            — WEB list (ProjectsRoute scaffold)   [required key]
 *   detail.html             — WEB detail (ProjectsRoute → ProjectsDestination
 *                             renderDetail → chat-surface ProjectDetailView,
 *                             solo profile)                       [required key]
 *   default-chatsurface.html— the DESKTOP list (ProjectsDestination CardGrid),
 *                             extra, so the comparator can diff the other host
 *                             against the same design anchors.
 *
 * Fixtures mirror design-kit/app-v3/copilot-data.jsx PROJECTS (3 rows:
 * Launch Week 3 chats/12 files, Treasury 3/20, Growth 2/7) and the CHATS rows
 * belonging to the first project, so row counts + string lengths match the
 * design side (computed styles depend on real content).
 *
 * Run: node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs \
 *        lib/render-live-projects.test.tsx
 * Output: surfaces/projects/live/<state>.html (+ copied ds.css / styles.css)
 * ========================================================================= */
import { createElement as h } from "react";
import { copyFileSync, mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, expect, it, vi } from "vitest";

import type {
  ConversationId,
  Project,
  ProjectActivity,
  ProjectId,
  ProjectListResponse,
  ProjectMembership,
  ProjectSummary,
  SectionResult,
  TenantId,
  UserId,
} from "@0x-copilot/api-types";

// ---------------------------------------------------------------------------
// Mock the web host's data layer. Same seam apps/frontend's own
// ProjectsRoute.test.tsx uses (ProjectsRoute.test.tsx:33-63) — the component
// under render is the REAL one; only the HTTP/SSE module is faked.
// ---------------------------------------------------------------------------
const projectsApiMocks = vi.hoisted(() => ({
  fetchProjects: vi.fn(),
  fetchProject: vi.fn(),
  fetchProjectMembers: vi.fn(),
  fetchProjectActivity: vi.fn(),
  activateProject: vi.fn(),
  archiveProject: vi.fn(),
  deleteProject: vi.fn(),
  starProject: vi.fn(),
  unstarProject: vi.fn(),
  streamProjectEvents: vi.fn(),
}));
vi.mock("../../../apps/frontend/src/api/projectsApi", async () => {
  const actual = await vi.importActual<
    typeof import("../../../apps/frontend/src/api/projectsApi")
  >("../../../apps/frontend/src/api/projectsApi");
  return { ...actual, ...projectsApiMocks };
});

// Imports below this line resolve through the mock above.
import { ProjectsRoute } from "../../../apps/frontend/src/features/projects/ProjectsRoute";
import {
  ProjectsDestination,
  cacheProjectNames,
} from "@0x-copilot/chat-surface";
import { RouterProvider } from "../../../packages/chat-surface/src/providers/RouterProvider";
// PRJ-09 probe: the real shell chrome, mounted the way each host mounts it.
import { ChatShell } from "../../../packages/chat-surface/src/shell/ChatShell";
import { DeploymentProfileProvider } from "../../../packages/chat-surface/src/providers/DeploymentProfileProvider";
import { destinationsForProfile } from "../../../packages/chat-surface/src/shell/destinations";
// Side-effect import: registers the `kind: "project"` ItemRef resolver so the
// CardGrid's `<ItemLink>` resolves instead of rendering the deleted chip.
import "../../../packages/chat-surface/src/destinations/projects/index";

const HERE = (p: string) => fileURLToPath(new URL(p, import.meta.url));
const REPO = (p: string) => HERE("../../../" + p); // tools/design-parity/lib -> repo root
const LIVE = (p: string) => HERE("../surfaces/projects/live/" + p);

const IDENTITY = { orgId: "org_dev", userId: "user_dev" };

// ---------------------------------------------------------------------------
// Fixtures — mirror design-kit/app-v3/copilot-data.jsx:202-212 (PROJECTS)
// and :193-200 (CHATS, project === "launch").
// ---------------------------------------------------------------------------
const asProjectId = (s: string): ProjectId => s as unknown as ProjectId;
const asTenantId = (s: string): TenantId => s as unknown as TenantId;
const asUserId = (s: string): UserId => s as unknown as UserId;

interface Seed {
  readonly id: string;
  readonly name: string;
  readonly desc: string;
  readonly hue: number;
  readonly emoji: string;
  readonly chats: number;
  readonly files: number;
}

const SEEDS: ReadonlyArray<Seed> = [
  {
    id: "launch",
    name: "Launch Week",
    desc: "GTM for the v2 launch",
    hue: 205,
    emoji: "🚀",
    chats: 3,
    files: 12,
  },
  {
    id: "treasury",
    name: "Treasury",
    desc: "Payments, runway & on-chain ops",
    hue: 145,
    emoji: "💠",
    chats: 3,
    files: 20,
  },
  {
    id: "growth",
    name: "Growth",
    desc: "Content, community & analytics",
    hue: 265,
    emoji: "📈",
    chats: 2,
    files: 7,
  },
];

function summaryOf(seed: Seed): ProjectSummary {
  return {
    id: asProjectId(seed.id),
    tenant_id: asTenantId("tenant_dev"),
    name: seed.name,
    description: seed.desc,
    icon_emoji: seed.emoji,
    color_hue: seed.hue,
    status: "active",
    owner_user_id: asUserId("user_dev"),
    viewer_role: null, // solo desktop profile → no role chip (FR-4.13)
    viewer_starred: false,
    counts: {
      chats: seed.chats,
      todos_open: 0,
      todos_done: 0,
      inbox_items: 0,
      library_items: seed.files,
      routines_active: 0,
      members: 1,
    },
    last_activity_at: "2026-07-21T09:00:00Z",
    updated_at: "2026-07-21T09:00:00Z",
  };
}

function fullProjectOf(seed: Seed): Project {
  return {
    id: asProjectId(seed.id),
    tenant_id: asTenantId("tenant_dev"),
    owner_user_id: asUserId("user_dev"),
    name: seed.name,
    description: seed.desc,
    icon_emoji: seed.emoji,
    color_hue: seed.hue,
    status: "active",
    archived_at: null,
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-21T09:00:00Z",
    last_activity_at: "2026-07-21T09:00:00Z",
    counts: {
      chats: seed.chats,
      todos_open: 0,
      todos_done: 0,
      inbox_items: 0,
      library_items: seed.files,
      routines_active: 0,
      members: 1,
    },
    viewer_role: null,
    viewer_starred: false,
  } as unknown as Project;
}

function membershipOf(): ProjectMembership {
  return {
    project_id: asProjectId("launch"),
    user_id: asUserId("user_dev"),
    role: "owner",
    added_at: "2026-07-01T00:00:00Z",
    added_by_user_id: asUserId("user_dev"),
  } as unknown as ProjectMembership;
}

/** The three CHATS rows the design shows under project "launch"
 *  (copilot-data.jsx:193,195,198 — launch / investor / ama). */
const LAUNCH_CHATS: ReadonlyArray<{
  readonly id: string;
  readonly conv: string;
  readonly action: string;
  readonly preview: string;
  readonly at: string;
}> = [
  {
    id: "act_1",
    conv: "conv_launch",
    action: "chat.updated",
    preview: "Launch Week ops — Streaming the launch thread",
    at: "2026-07-21T11:59:00Z",
  },
  {
    id: "act_2",
    conv: "conv_investor",
    action: "chat.updated",
    preview: "Investor update — July — Draft saved to Local files",
    at: "2026-07-21T09:00:00Z",
  },
  {
    id: "act_3",
    conv: "conv_ama",
    action: "chat.updated",
    preview: "Summarize Discord AMA — Posted recap to #announcements",
    at: "2026-07-20T18:00:00Z",
  },
];

function activityRows(): ReadonlyArray<ProjectActivity> {
  return LAUNCH_CHATS.map((c) => ({
    id: c.id,
    tenant_id: asTenantId("tenant_dev"),
    project_id: asProjectId("launch"),
    actor_user_id: asUserId("user_dev"),
    actor_display_name: "Sarah Chen",
    action: c.action,
    kind: "chat",
    ref: { kind: "chat", id: c.conv as unknown as ConversationId },
    preview: c.preview,
    occurred_at: c.at,
  })) as unknown as ReadonlyArray<ProjectActivity>;
}

function listResponse(): ProjectListResponse {
  return {
    items: SEEDS.map(summaryOf),
    next_cursor: null,
  } as unknown as ProjectListResponse;
}

// ---------------------------------------------------------------------------
// HTML shell — the REAL stylesheets (design-system tokens first, then the app
// sheet that consumes them) inside a fixed dark frame. Typography / colour /
// border / padding are frame-independent; width & height are comparator noise.
// ---------------------------------------------------------------------------
function shell(inner: string): string {
  return `<!doctype html>
<html lang="en" data-theme="dark">
  <head>
    <meta charset="utf-8" />
    <title>design-parity · projects · LIVE</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet" />
    <link rel="stylesheet" href="./ds.css" />
    <link rel="stylesheet" href="./styles.css" />
    <style>
      html, body { margin: 0; height: 100%; background: #050506; }
      #frame {
        width: 1040px; height: 760px; display: flex; flex-direction: column;
        background: var(--color-bg, #09090b); color: var(--color-text, #ececf1);
        font-family: var(--font-sans); overflow: hidden;
      }
    </style>
  </head>
  <body><div id="frame">${inner}</div></body>
</html>`;
}

function writeState(name: string, inner: string): void {
  expect(inner.length).toBeGreaterThan(200);
  writeFileSync(LIVE(`${name}.html`), shell(inner));
}

/** Serialize the route subtree (never the whole document). */
function captureRoute(): string {
  const el = document.querySelector('[data-testid="projects-route"]');
  return el === null ? "" : el.outerHTML;
}

beforeAll(() => {
  mkdirSync(LIVE(""), { recursive: true });
  copyFileSync(REPO("packages/design-system/src/styles.css"), LIVE("ds.css"));
  copyFileSync(REPO("apps/frontend/src/styles.css"), LIVE("styles.css"));
});

beforeEach(() => {
  for (const fn of Object.values(projectsApiMocks)) fn.mockReset();
  projectsApiMocks.fetchProjects.mockResolvedValue(listResponse());
  projectsApiMocks.fetchProject.mockResolvedValue(fullProjectOf(SEEDS[0]!));
  projectsApiMocks.fetchProjectMembers.mockResolvedValue({
    items: [membershipOf()],
    next_cursor: null,
  });
  projectsApiMocks.fetchProjectActivity.mockResolvedValue({
    items: activityRows(),
    next_cursor: null,
  });
  projectsApiMocks.streamProjectEvents.mockImplementation(() => ({
    close: vi.fn(),
  }));
});

afterEach(() => {
  cleanup();
});

// ===========================================================================
// default — the WEB host's un-focused project list
// ===========================================================================
it("renders the live Projects list (web host scaffold) → default.html", async () => {
  render(h(ProjectsRoute, { identity: IDENTITY }));

  await waitFor(() => {
    expect(screen.queryByTestId("projects-route-list")).not.toBeNull();
  });
  expect(screen.getAllByTestId("projects-route-row")).toHaveLength(3);

  writeState("default", captureRoute());
});

// ===========================================================================
// detail — the WEB host's focused project (chat-surface ProjectDetailView)
// ===========================================================================
it("renders the live Project detail (ProjectDetailView, solo profile) → detail.html", async () => {
  render(h(ProjectsRoute, { identity: IDENTITY }));

  await waitFor(() => {
    expect(screen.getAllByTestId("projects-route-open").length).toBe(3);
  });
  fireEvent.click(screen.getAllByTestId("projects-route-open")[0]!);

  await waitFor(() => {
    expect(screen.queryByTestId("project-detail-view")).not.toBeNull();
  });
  // Chats section is filled from the host's cross-destination slot.
  await waitFor(() => {
    expect(screen.getAllByTestId("projects-detail-chat-row").length).toBe(3);
  });

  writeState("detail", captureRoute());
});

// ===========================================================================
// default-chatsurface — the DESKTOP host's list (ProjectsDestination CardGrid)
// (apps/desktop/renderer/destinationBinders.tsx:563-568). Extra state: the
// same design anchors can be diffed against the other host.
// ===========================================================================
it("renders the live ProjectsDestination card grid (desktop host) → default-chatsurface.html", async () => {
  // Prime the name cache the way the web binder does so the card-name
  // `<ItemLink kind="project">` resolves to the real name rather than the
  // generic "Project" label (projectNameCache.ts:22-40).
  cacheProjectNames(SEEDS.map((s) => ({ id: s.id, name: s.name })));

  const rows = SEEDS.map(summaryOf);
  const items: SectionResult<ReadonlyArray<ProjectSummary>> = {
    status: "ok",
    data: rows,
  };
  const router = {
    current: () => ({ kind: "workspace", workspaceId: "launch" }),
    navigate: () => undefined,
    subscribe: () => () => undefined,
  };

  render(
    h(
      RouterProvider as never,
      { router } as never,
      h(ProjectsDestination, {
        items,
        counts: { all: 3, active: 3, archived: 0, starred: 0 },
        onCreateProject: () => undefined,
        onStarProject: () => undefined,
        onArchiveProject: () => undefined,
        now: Date.parse("2026-07-21T12:00:00Z"),
      }),
    ),
  );

  await waitFor(() => {
    expect(screen.getAllByTestId("project-card").length).toBe(3);
  });
  // ItemLink resolves asynchronously (useEffect) — wait for the real names.
  await waitFor(() => {
    expect(screen.getAllByTestId("item-link").length).toBe(3);
  });

  const el = document.querySelector('[data-testid="projects-destination"]');
  writeState("default-chatsurface", el === null ? "" : el.outerHTML);
});

// ===========================================================================
// PRJ-09 · destination title + subtitle in the topbar
// ===========================================================================
//
// Design side: design-kit/app-v3/copilot-app.jsx:238
//   `projects: ["Projects", "group chats, files & context"]`
// rendered at copilot-app.jsx:310 as
//   `<div className="tb-title"><h1>{tTitle}</h1><span className="sub">{tSub}</span></div>`
//
// Live side: packages/chat-surface/src/shell/Topbar.tsx:88 resolves the TITLE
// from the destinations registry; the SUBTITLE comes only from the `leaf` prop
// (Topbar.tsx:74-80, :142-146), which ChatShell forwards from `topbarLeaf`
// (ChatShell.tsx:308). Neither host passes `topbarLeaf`:
//   apps/frontend/src/app/App.tsx:1200-1226 (web)  — no such prop
//   apps/desktop/renderer/bootstrap.tsx:318-330 (desktop) — no such prop
// These two tests turn that grep into an executable assertion by mounting the
// REAL ChatShell with the REAL host prop-sets.
const shellStubs = {
  transport: {
    request: () => new Promise(() => {}),
    subscribeServerSentEvents: () => ({ close: () => undefined }),
    getSession: () => ({ bearer: null }),
    capabilities: () => ({
      substrate: "web" as const,
      nativeSecretStorage: false,
      fileSystemAccess: false,
      clipboardWrite: true,
      openExternal: false,
    }),
  },
  router: {
    current: () => {
      throw new Error("no route");
    },
    navigate: () => undefined,
    subscribe: () => () => undefined,
  },
  keyValueStore: { get: () => null, set: () => undefined, keys: () => [] },
  presenceSignal: {
    current: () => "visible" as const,
    subscribe: () => () => undefined,
  },
};

it("PRJ-09 · web host: topbar shows the title but NO subtitle", () => {
  render(
    h(
      DeploymentProfileProvider as never,
      { profile: "single_user_desktop" } as never,
      h(
        ChatShell as never,
        {
          ...shellStubs,
          activeDestination: "projects",
          onNavigate: () => undefined,
          // Mirrors App.tsx:1200-1226 exactly: NO `topbarLeaf` prop.
        } as never,
        null,
      ),
    ),
  );
  expect(screen.getByTestId("topbar-title").textContent).toBe("Projects");
  expect(screen.queryByTestId("topbar-subtitle")).toBeNull();
});

it("PRJ-09 · desktop host: topbar shows the title but NO subtitle", () => {
  render(
    h(
      ChatShell as never,
      {
        ...shellStubs,
        activeDestination: "projects",
        destinations: destinationsForProfile("single_user_desktop"),
        onNavigate: () => undefined,
        settingsActive: false,
        // Mirrors bootstrap.tsx:318-330 exactly: NO `topbarLeaf` prop.
      } as never,
      null,
    ),
  );
  expect(screen.getByTestId("topbar-title").textContent).toBe("Projects");
  expect(screen.queryByTestId("topbar-subtitle")).toBeNull();
});
