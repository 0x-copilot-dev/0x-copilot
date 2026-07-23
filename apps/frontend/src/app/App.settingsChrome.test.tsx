// PRD-12 D2/D3 / DoD 14 — web reaches the SAME Settings layout desktop ships.
//
// Unlike `App.test.tsx` (which stubs ChatShell to a passthrough to assert the
// dispatched body), this file renders the REAL `ChatShell` so it can prove that
// for the `settings` route the shell suppresses BOTH the topbar and the 224px
// context column — the chrome web used to render but desktop never did. It also
// pins the `isSettingsScreen` predicate the App feeds into `buildWebShellBinding`.
//
// Only the leaf binders + data ports are stubbed; the rail/topbar/context-panel
// chrome is the real shell.

import { render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Transport } from "@0x-copilot/chat-transport";

import { isSettingsScreen } from "./routes";

function stub(testId: string) {
  return () => <div data-testid={testId} />;
}

vi.mock("../features/chat/ChatScreen", () => ({ ChatScreen: stub("run") }));
vi.mock("../features/chats/ChatsArchiveRoute", () => ({
  ChatsArchiveRoute: stub("chats"),
}));
vi.mock("../features/activity/ActivityRoute", () => ({
  ActivityRoute: stub("activity"),
}));
vi.mock("../features/skills/SkillsRoute", () => ({
  SkillsRoute: stub("skills"),
}));
vi.mock("../features/projects/ProjectsRoute", () => ({
  ProjectsRoute: stub("projects"),
}));
vi.mock("../features/connectors/ConnectorsGateway", () => ({
  ConnectorsGateway: stub("connectors"),
}));
vi.mock("../features/team/TeamGateway", () => ({ TeamGateway: stub("team") }));
vi.mock("../features/run/RunRoute", () => ({ RunRoute: stub("run-route") }));
vi.mock("./featureFlags", () => ({
  RUN_COCKPIT_WEB_FLAG_KEY: "enterprise.flags.run-cockpit-web",
  isRunCockpitWebEnabled: vi.fn(() => false),
}));
vi.mock("../features/palette/PaletteHost", () => ({ PaletteHost: () => null }));
vi.mock("../features/settings/SettingsBinder", () => ({
  SettingsBinder: stub("settings-body"),
}));
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

// A minimal REAL Transport shape so the shell's `useActiveRunCount` can call
// `request` without crashing; it never resolves (the badge stays dark).
const fakeTransport: Transport = {
  request: () => new Promise(() => {}),
  subscribeServerSentEvents: () => ({ close: () => {} }),
  getSession: () => ({ bearer: null }),
  capabilities: () => ({
    substrate: "web",
    nativeSecretStorage: false,
    fileSystemAccess: false,
    clipboardWrite: false,
    openExternal: false,
  }),
};
vi.mock("../api/transport", () => ({ getAppTransport: () => fakeTransport }));
vi.mock("../features/onboarding/firstRunStore", () => ({
  createWebFirstRunStore: () => ({
    isComplete: () => true,
    markComplete: vi.fn(),
  }),
}));

import { CopilotApp } from "./App";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

function renderAt(path: string) {
  window.history.replaceState(null, "", path);
  return render(<CopilotApp identity={IDENTITY} roles={[]} />);
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
});
afterEach(() => {
  vi.clearAllMocks();
});

describe("isSettingsScreen (PRD-12 D2/D3)", () => {
  it("is true for the settings + settings-p12 screens and false for chat", () => {
    expect(isSettingsScreen({ screen: "settings", section: "profile" })).toBe(
      true,
    );
    expect(
      isSettingsScreen({
        screen: "settings-p12",
        subPath: "notification-defaults",
      }),
    ).toBe(true);
    expect(isSettingsScreen({ screen: "chat", destination: "run" })).toBe(
      false,
    );
  });
});

describe("Settings chrome on web (DoD 14)", () => {
  it("renders Settings full-bleed: no topbar and no context panel inside the shell", async () => {
    renderAt("/settings");
    // The Settings body is dispatched inside the real shell.
    const shell = await screen.findByTestId("settings-body");
    const shellRoot = shell.closest('[data-component="chat-shell"]');
    expect(shellRoot).not.toBeNull();
    // PRD-09's suppression sets (consumed unchanged) drop the topbar + the 224px
    // context column while `settingsActive` — the layout desktop already ships.
    await waitFor(() => {
      expect(shellRoot!.querySelector('[data-component="topbar"]')).toBeNull();
      expect(
        shellRoot!.querySelector('[data-component="context-panel"]'),
      ).toBeNull();
    });
  });
});
