// SkillsRoute — host-binder tests (phase-4 PRD FR-4.26 / FR-4.27 / FR-4.28).
//
// The presentational `<SkillsDestination>` (PR-4.9) is rendered for real so
// the tests click the actual Run / Edit / New buttons. Two seams are mocked:
//
//   - `./useSkills` — controls the list state (loading / error / empty /
//     ready) without touching the real `/v1/skills` fetch (covered by
//     skillsApi-level tests).
//   - `../../api/agentApi` — the run-start transport (`createConversation` +
//     `createRun`). "MOCKED transport": Run starts a run then navigates.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Skill } from "@0x-copilot/api-types";

// --- Hoisted mocks --------------------------------------------------------

const useSkillsMock = vi.hoisted(() => ({ useSkills: vi.fn() }));
vi.mock("./useSkills", () => ({ useSkills: useSkillsMock.useSkills }));

const agentApiMocks = vi.hoisted(() => ({
  createConversation: vi.fn(),
  createRun: vi.fn(),
}));
vi.mock("../../api/agentApi", () => ({
  createConversation: agentApiMocks.createConversation,
  createRun: agentApiMocks.createRun,
}));

import { SkillsRoute } from "./SkillsRoute";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

// --- Fixtures -------------------------------------------------------------

function skill(overrides: Partial<Skill> = {}): Skill {
  return {
    skill_id: "skill_1",
    name: "weekly-report",
    display_name: "Weekly report",
    description: "Compile the weekly report.",
    markdown: "# Weekly report",
    virtual_path: "/skills/weekly-report",
    enabled: true,
    scope: "user",
    source_type: "user",
    version: 1,
    allowed_tools: [],
    compatibility: [],
    metadata: {},
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-17T12:00:00Z",
    ...overrides,
  };
}

function mockSkillState(
  overrides: {
    skills?: Skill[];
    loading?: boolean;
    error?: string | null;
    refresh?: () => Promise<void>;
  } = {},
): { refresh: () => Promise<void> } {
  const refresh = overrides.refresh ?? vi.fn().mockResolvedValue(undefined);
  useSkillsMock.useSkills.mockReturnValue({
    skills: overrides.skills ?? [],
    loading: overrides.loading ?? false,
    error: overrides.error ?? null,
    refresh,
    create: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    setEnabled: vi.fn(),
  });
  return { refresh };
}

// ===========================================================================

describe("SkillsRoute", () => {
  beforeEach(() => {
    useSkillsMock.useSkills.mockReset();
    agentApiMocks.createConversation.mockReset();
    agentApiMocks.createRun.mockReset();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the loading skeleton while the list is loading", () => {
    mockSkillState({ loading: true });
    render(<SkillsRoute identity={IDENTITY} />);

    expect(screen.getByTestId("skills-route")).toHaveAttribute(
      "data-state",
      "loading",
    );
    expect(
      screen.getAllByTestId("skills-skeleton-card").length,
    ).toBeGreaterThan(0);
  });

  it("renders the ready grid with the skill name", () => {
    mockSkillState({ skills: [skill()] });
    render(<SkillsRoute identity={IDENTITY} />);

    expect(screen.getByTestId("skills-route")).toHaveAttribute(
      "data-state",
      "ok",
    );
    expect(screen.getByText("Weekly report")).toBeInTheDocument();
  });

  it("Run starts a run (conversation + run) and navigates to it", async () => {
    mockSkillState({ skills: [skill()] });
    agentApiMocks.createConversation.mockResolvedValue({
      conversation_id: "conv_9",
    });
    agentApiMocks.createRun.mockResolvedValue({
      run_id: "run_9",
      conversation_id: "conv_9",
    });
    const onOpenRun = vi.fn();

    render(
      <SkillsRoute
        identity={IDENTITY}
        onOpenRun={onOpenRun}
        onOpenSkillEditor={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByTestId("skill-card-run"));

    await waitFor(() => {
      expect(onOpenRun).toHaveBeenCalledWith("conv_9");
    });
    expect(agentApiMocks.createConversation).toHaveBeenCalledTimes(1);
    // The run instructs the model to use the skill by display name.
    expect(agentApiMocks.createRun).toHaveBeenCalledWith(
      "conv_9",
      expect.stringContaining("Weekly report"),
      IDENTITY,
    );
  });

  it("surfaces a run-start error and does not navigate", async () => {
    mockSkillState({ skills: [skill()] });
    agentApiMocks.createConversation.mockRejectedValue(new Error("boom-run"));
    const onOpenRun = vi.fn();

    render(<SkillsRoute identity={IDENTITY} onOpenRun={onOpenRun} />);

    fireEvent.click(screen.getByTestId("skill-card-run"));

    await waitFor(() => {
      expect(screen.getByTestId("skills-route-run-error")).toBeInTheDocument();
    });
    expect(onOpenRun).not.toHaveBeenCalled();
    expect(agentApiMocks.createRun).not.toHaveBeenCalled();
  });

  it("Edit opens the editor for the given skill id", () => {
    mockSkillState({ skills: [skill({ skill_id: "skill_42" })] });
    const onOpenSkillEditor = vi.fn();

    render(
      <SkillsRoute identity={IDENTITY} onOpenSkillEditor={onOpenSkillEditor} />,
    );

    fireEvent.click(screen.getByTestId("skill-card-edit"));
    expect(onOpenSkillEditor).toHaveBeenCalledWith("skill_42");
  });

  it("New skill opens the editor for a fresh skill (null id)", () => {
    mockSkillState({ skills: [skill()] });
    const onOpenSkillEditor = vi.fn();

    render(
      <SkillsRoute identity={IDENTITY} onOpenSkillEditor={onOpenSkillEditor} />,
    );

    fireEvent.click(screen.getByTestId("page-header-primary-action"));
    expect(onOpenSkillEditor).toHaveBeenCalledWith(null);
  });

  it("renders the empty state when there are zero skills", () => {
    mockSkillState({ skills: [] });
    render(<SkillsRoute identity={IDENTITY} />);

    expect(screen.getByTestId("skills-route")).toHaveAttribute(
      "data-state",
      "ok",
    );
    expect(screen.getByText("No skills yet")).toBeInTheDocument();
  });

  it("renders the error state and retries via the hook's refresh", () => {
    // Distinct from the destination's fixed "Could not load skills" title so
    // the assertion targets only the error body.
    const { refresh } = mockSkillState({ error: "Network hiccup" });
    render(<SkillsRoute identity={IDENTITY} />);

    expect(screen.getByTestId("skills-route")).toHaveAttribute(
      "data-state",
      "error",
    );
    expect(screen.getByText("Network hiccup")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("empty-state-action"));
    expect(refresh).toHaveBeenCalledTimes(1);
  });
});
