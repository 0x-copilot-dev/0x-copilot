// App-level dispatch tests for PR-4.11 (IA fold + six-destination dispatch +
// folded-slug redirects). No App-level test existed before this PR; routing
// primitives are covered by `app/HashRouter.test.ts` and the destinations
// contract by `packages/chat-surface/src/shell/destinations.test.ts`. This
// file adds:
//
//   1. Pure `foldedRedirectFor` unit tests — the FR-4.31 fold map (all seven
//      folded slugs → their absorbing destination; the six live slugs → null).
//   2. The FR-4.30 solo-rail contract (six slugs + Tools/Skills labels) that
//      the shell renders under `single_user_desktop`.
//   3. `CopilotApp` render tests — each of the six live slugs renders its
//      binder; the callback wiring navigates (onOpenRun → Run; Activity's
//      retention link → Settings → Privacy); a folded deep-link redirects.
//
// The binder Routes + ChatShell are stubbed so the tests exercise the App's
// dispatch + navigation wiring, not the binders' own fetch/SSE behaviour
// (covered by each `features/*/…Route.test.tsx`).

import { act, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { destinationsForProfile } from "@0x-copilot/chat-surface";

import {
  FOLDED_DESTINATION_REDIRECTS,
  ROOT_DESTINATION,
  foldedRedirectFor,
  type AppRoute,
} from "./routes";

// --- Prop capture -----------------------------------------------------------
// Each binder stub stashes the props the App passed it so a test can invoke
// the wired callbacks (onOpenRun / onOpenRetentionSettings / …).
type Captured = Record<string, Record<string, unknown>>;
const captured: Captured = {};

// --- Module mocks -----------------------------------------------------------

vi.mock("../features/chat/ChatScreen", () => ({
  ChatScreen: () => <div data-testid="run-cockpit" />,
}));
vi.mock("../features/chats/ChatsArchiveRoute", () => ({
  ChatsArchiveRoute: (props: Record<string, unknown>) => {
    captured.chats = props;
    return <div data-testid="chats-stub" />;
  },
}));
vi.mock("../features/activity/ActivityRoute", () => ({
  ActivityRoute: (props: Record<string, unknown>) => {
    captured.activity = props;
    return <div data-testid="activity-stub" />;
  },
}));
vi.mock("../features/skills/SkillsRoute", () => ({
  SkillsRoute: (props: Record<string, unknown>) => {
    captured.skills = props;
    return <div data-testid="skills-stub" />;
  },
}));
vi.mock("../features/projects/ProjectsRoute", () => ({
  ProjectsRoute: (props: Record<string, unknown>) => {
    captured.projects = props;
    return <div data-testid="projects-stub" />;
  },
}));
vi.mock("../features/connectors/ConnectorsGateway", () => ({
  ConnectorsGateway: (props: Record<string, unknown>) => {
    captured.connectors = props;
    return <div data-testid="connectors-stub" />;
  },
}));
vi.mock("../features/team/TeamGateway", () => ({
  TeamGateway: () => <div data-testid="team-stub" />,
}));
// PRD-05 — the real Run cockpit binder, stubbed so the flag-dispatch tests
// assert WHICH surface the `run` slug mounts (legacy ChatScreen vs RunRoute)
// without pulling the full RunDestination/ThreadCanvas tree. Lazy-imported in
// App.tsx; vi.mock intercepts the dynamic import too.
vi.mock("../features/run/RunRoute", () => ({
  RunRoute: (props: Record<string, unknown>) => {
    captured.run = props;
    return <div data-testid="run-route-stub" />;
  },
}));
// PRD-05 — the `runCockpitWeb` flag, mocked so the dispatch tests control it
// deterministically (jsdom's localStorage is a no-op here). Default OFF; tests
// flip it on via `vi.mocked(isRunCockpitWebEnabled).mockReturnValue(true)`.
vi.mock("./featureFlags", () => ({
  RUN_COCKPIT_WEB_FLAG_KEY: "enterprise.flags.run-cockpit-web",
  isRunCockpitWebEnabled: vi.fn(() => false),
}));
vi.mock("../features/palette/PaletteHost", () => ({
  PaletteHost: () => null,
}));
// The settings surface is a redirect target (memory fold + retention link);
// stub the PRD-E `SettingsBinder` (every section — the legacy SettingsScreen
// is retired, PR-E.3) so those tests assert the dispatch without pulling the
// real Settings surface + its data ports.
vi.mock("../features/settings/SettingsBinder", () => ({
  SettingsBinder: () => <div data-testid="settings-stub" />,
}));

// Data hooks + transport — the App wires these but the stubs above ignore them.
vi.mock("../features/connectors/useConnectors", () => ({
  useConnectors: () => ({}),
}));
vi.mock("../features/skills/useSkills", () => ({
  useSkills: () => ({
    skills: [],
    loading: false,
    error: null,
    refresh: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    setEnabled: vi.fn(),
  }),
}));
vi.mock("../features/me/useUserProfile", () => ({
  useUserProfile: () => null,
}));
vi.mock("../api/transport", () => ({
  getAppTransport: () => ({}),
}));

// Keep every real chat-surface export (DeploymentProfileProvider, the KV/secret
// stores, resolver helpers, destinationsForProfile) but swap ChatShell for a
// passthrough so the tests assert the dispatched body without rendering the
// full rail/topbar chrome.
vi.mock("@0x-copilot/chat-surface", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@0x-copilot/chat-surface")>();
  return {
    ...actual,
    ChatShell: ({ children }: { children: ReactNode }) => (
      <div data-testid="shell">{children}</div>
    ),
  };
});

// Imported after the mocks so the mocked module graph is in force.
import { CopilotApp } from "./App";
import { isRunCockpitWebEnabled } from "./featureFlags";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

function seedLocation(path: string): void {
  window.history.replaceState(null, "", path);
}

function renderAt(path: string) {
  seedLocation(path);
  return render(<CopilotApp identity={IDENTITY} roles={[]} />);
}

beforeEach(() => {
  for (const key of Object.keys(captured)) {
    delete captured[key];
  }
  // PRD-05 — the `runCockpitWeb` flag is mocked (jsdom's localStorage here is a
  // no-op stub); default it OFF so the legacy dispatch is the baseline. Tests
  // opt into the cockpit by overriding the return value.
  vi.mocked(isRunCockpitWebEnabled).mockReturnValue(false);
  seedLocation("/");
});

afterEach(() => {
  vi.clearAllMocks();
});

// ===========================================================================
// FR-4.31 — folded-slug redirect map (pure)
// ===========================================================================

describe("foldedRedirectFor (FR-4.31)", () => {
  it("redirects agents/inbox → Activity", () => {
    for (const slug of ["agents", "inbox"] as const) {
      expect(foldedRedirectFor({ screen: "chat", destination: slug })).toEqual({
        screen: "chat",
        destination: "activity",
      });
    }
  });

  it("redirects home/library/todos/routines → Run", () => {
    for (const slug of ["home", "library", "todos", "routines"] as const) {
      expect(foldedRedirectFor({ screen: "chat", destination: slug })).toEqual({
        screen: "chat",
        destination: "run",
      });
    }
  });

  it("redirects memory → Settings → Privacy & data", () => {
    expect(
      foldedRedirectFor({ screen: "chat", destination: "memory" }),
    ).toEqual({ screen: "settings", section: "privacy-data" });
  });

  it("returns null for the six live solo slugs (no redirect)", () => {
    for (const slug of [
      "run",
      "chats",
      "projects",
      "activity",
      "connectors",
      "tools",
    ] as const) {
      expect(
        foldedRedirectFor({ screen: "chat", destination: slug }),
      ).toBeNull();
    }
  });

  it("returns null for non-chat screens", () => {
    const routes: AppRoute[] = [
      { screen: "settings", section: "profile" },
      { screen: "share", token: "tok_1" },
      { screen: "admin-adapter-review-queue" },
    ];
    for (const route of routes) {
      expect(foldedRedirectFor(route)).toBeNull();
    }
  });

  it("returns a STABLE object reference per slug (safe as an effect dep)", () => {
    // The App keys its redirect effect on `foldedRedirectFor(route)`; the map
    // returns its own frozen objects so the effect does not churn per render.
    expect(foldedRedirectFor({ screen: "chat", destination: "agents" })).toBe(
      FOLDED_DESTINATION_REDIRECTS.agents,
    );
    expect(foldedRedirectFor({ screen: "chat", destination: "memory" })).toBe(
      FOLDED_DESTINATION_REDIRECTS.memory,
    );
  });
});

// ===========================================================================
// FR-4.30 — the solo rail exposes exactly the six destinations + labels
// ===========================================================================

describe("solo-profile rail contract (FR-4.30)", () => {
  const solo = destinationsForProfile("single_user_desktop");

  it("is exactly the six solo slugs in DESIGN-SPEC §1 order", () => {
    expect(solo.map((d) => d.slug)).toEqual([
      "run",
      "chats",
      "projects",
      "activity",
      "connectors",
      "tools",
    ]);
  });

  it("relabels connectors→Tools and tools→Skills", () => {
    expect(solo.map((d) => d.label)).toEqual([
      "Run",
      "Chats",
      "Projects",
      "Activity",
      "Tools",
      "Skills",
    ]);
  });
});

// ===========================================================================
// CopilotApp destination dispatch (render)
// ===========================================================================

describe("CopilotApp destination dispatch", () => {
  it.each([
    ["/", "run-cockpit"],
    ["/chats", "chats-stub"],
    ["/projects", "projects-stub"],
    ["/activity", "activity-stub"],
    ["/connectors", "connectors-stub"],
    ["/tools", "skills-stub"],
  ])("renders the mapped binder for %s", async (path, testid) => {
    renderAt(path);
    expect(await screen.findByTestId(testid)).toBeInTheDocument();
  });

  it("wires Chats onOpenRun → navigate to the Run destination", async () => {
    renderAt("/chats");
    await screen.findByTestId("chats-stub");

    const onOpenRun = captured.chats.onOpenRun as (id: string) => void;
    act(() => {
      onOpenRun("conv_1");
    });

    // Run is ROOT_DESTINATION, which round-trips to "/".
    expect(ROOT_DESTINATION).toBe("run");
    await waitFor(() => {
      expect(window.location.pathname).toBe("/");
    });
    expect(await screen.findByTestId("run-cockpit")).toBeInTheDocument();
  });

  it("wires Activity onOpenRetentionSettings → Settings → Privacy & data", async () => {
    renderAt("/activity");
    await screen.findByTestId("activity-stub");

    const onOpenRetentionSettings = captured.activity
      .onOpenRetentionSettings as () => void;
    act(() => {
      onOpenRetentionSettings();
    });

    await waitFor(() => {
      expect(window.location.pathname).toBe("/settings");
    });
    expect(window.location.hash).toBe("#privacy-data");
    expect(await screen.findByTestId("settings-stub")).toBeInTheDocument();
  });

  it("passes the Tools approval-policy link (onOpenApprovalSettings) to the gateway", async () => {
    renderAt("/connectors");
    await screen.findByTestId("connectors-stub");

    const onOpenApprovalSettings = captured.connectors
      .onOpenApprovalSettings as () => void;
    act(() => {
      onOpenApprovalSettings();
    });

    await waitFor(() => {
      expect(window.location.pathname).toBe("/settings");
    });
    expect(window.location.hash).toBe("#model-and-behavior");
  });

  it("passes onOpenSkillEditor to the Skills binder (Settings → Skills)", async () => {
    renderAt("/tools");
    await screen.findByTestId("skills-stub");

    const onOpenSkillEditor = captured.skills.onOpenSkillEditor as (
      id: string | null,
    ) => void;
    act(() => {
      onOpenSkillEditor(null);
    });

    await waitFor(() => {
      expect(window.location.pathname).toBe("/settings");
    });
    expect(window.location.hash).toBe("#skills");
  });
});

// ===========================================================================
// FR-4.31 — folded deep-links redirect (render)
// ===========================================================================

describe("folded deep-link redirects (FR-4.31)", () => {
  it("redirects /agents → Activity", async () => {
    renderAt("/agents");
    await waitFor(() => {
      expect(window.location.pathname).toBe("/activity");
    });
    expect(await screen.findByTestId("activity-stub")).toBeInTheDocument();
  });

  it("redirects /inbox → Activity", async () => {
    renderAt("/inbox");
    await waitFor(() => {
      expect(window.location.pathname).toBe("/activity");
    });
  });

  it.each(["/home", "/library", "/todos", "/routines"])(
    "redirects %s → Run (/)",
    async (path) => {
      renderAt(path);
      await waitFor(() => {
        expect(window.location.pathname).toBe("/");
      });
      expect(await screen.findByTestId("run-cockpit")).toBeInTheDocument();
    },
  );

  it("redirects /memory → Settings → Privacy & data", async () => {
    renderAt("/memory");
    await waitFor(() => {
      expect(window.location.pathname).toBe("/settings");
    });
    expect(window.location.hash).toBe("#privacy-data");
    expect(await screen.findByTestId("settings-stub")).toBeInTheDocument();
  });
});

// ===========================================================================
// PRD-05 — `runCockpitWeb` flag gates the `run` slug's surface
// ===========================================================================

describe("run-cockpit flag dispatch (PRD-05)", () => {
  it("flag OFF (default) renders the legacy ChatScreen under /", async () => {
    renderAt("/");
    // AC2 — the legacy path is unchanged: `run` mounts ChatScreen, not RunRoute.
    expect(await screen.findByTestId("run-cockpit")).toBeInTheDocument();
    expect(screen.queryByTestId("run-route-stub")).toBeNull();
  });

  it("flag ON mounts the real RunDestination binder (RunRoute) under /", async () => {
    vi.mocked(isRunCockpitWebEnabled).mockReturnValue(true);
    renderAt("/");
    // AC3 (dispatch half) — the flag swaps the legacy ChatScreen for RunRoute.
    expect(await screen.findByTestId("run-route-stub")).toBeInTheDocument();
    expect(screen.queryByTestId("run-cockpit")).toBeNull();
    // The binder receives the model-settings navigation seam.
    expect(typeof captured.run.onOpenModelSettings).toBe("function");
  });

  it("flag ON binder onOpenModelSettings navigates to Settings → Provider keys", async () => {
    vi.mocked(isRunCockpitWebEnabled).mockReturnValue(true);
    renderAt("/");
    await screen.findByTestId("run-route-stub");

    const onOpenModelSettings = captured.run.onOpenModelSettings as () => void;
    act(() => {
      onOpenModelSettings();
    });

    await waitFor(() => {
      expect(window.location.pathname).toBe("/settings");
    });
    expect(window.location.hash).toBe("#provider-keys");
  });
});
