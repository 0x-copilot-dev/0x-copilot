// SkillsGateway — pane-routing tests (PR-E.3 Decision D2).
//
// Mirrors the SkillsRoute test seams: `./useSkills` is mocked (it backs both
// the catalog route's internal call and the manage pane's call — the panes
// are exclusive, so exactly one is live at a time), and `../../api/agentApi`
// is mocked so the catalog's Run wiring never touches the transport.

import { fireEvent, render, screen } from "@testing-library/react";
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

import { SkillsGateway } from "./SkillsGateway";

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

function mockSkillState(overrides: { skills?: Skill[] } = {}): void {
  useSkillsMock.useSkills.mockReturnValue({
    skills: overrides.skills ?? [],
    loading: false,
    error: null,
    refresh: vi.fn().mockResolvedValue(undefined),
    create: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    setEnabled: vi.fn(),
  });
}

// ===========================================================================

describe("SkillsGateway", () => {
  beforeEach(() => {
    useSkillsMock.useSkills.mockReset();
    agentApiMocks.createConversation.mockReset();
    agentApiMocks.createRun.mockReset();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the catalog pane by default", () => {
    mockSkillState();
    render(<SkillsGateway identity={IDENTITY} />);

    expect(screen.getByTestId("skills-route")).toBeInTheDocument();
    expect(screen.queryByTestId("skills-manage-pane")).not.toBeInTheDocument();
  });

  it("flips to the manage pane via New skill, and Back returns to the catalog", () => {
    mockSkillState();
    render(<SkillsGateway identity={IDENTITY} />);

    // Catalog → manage: the destination's "New skill" CTA routes through
    // SkillsRoute's onOpenSkillEditor.
    fireEvent.click(screen.getAllByRole("button", { name: "New skill" })[0]);
    expect(screen.getByTestId("skills-manage-pane")).toBeInTheDocument();
    expect(screen.queryByTestId("skills-route")).not.toBeInTheDocument();
    // The manage pane is the full SkillsSettings editor (create form).
    expect(
      screen.getByRole("button", { name: "Add skill" }),
    ).toBeInTheDocument();

    // Manage → catalog via the back affordance.
    fireEvent.click(screen.getByTestId("skills-manage-back"));
    expect(screen.getByTestId("skills-route")).toBeInTheDocument();
    expect(screen.queryByTestId("skills-manage-pane")).not.toBeInTheDocument();
  });

  it("flips to the manage pane when a card's Edit is clicked", () => {
    mockSkillState({ skills: [skill()] });
    render(<SkillsGateway identity={IDENTITY} />);

    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    expect(screen.getByTestId("skills-manage-pane")).toBeInTheDocument();
  });
});
