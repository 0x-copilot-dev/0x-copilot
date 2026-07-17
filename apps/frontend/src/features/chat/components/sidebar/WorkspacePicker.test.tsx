// PR 3.5 / G5 — WorkspacePicker contract tests.

import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { WorkspaceListResponse } from "@0x-copilot/api-types";

const mockListMyWorkspaces = vi.fn<() => Promise<WorkspaceListResponse>>();
vi.mock("../../../../api/meApi", () => ({
  listMyWorkspaces: () => mockListMyWorkspaces(),
}));

import { WorkspacePicker } from "./WorkspacePicker";

describe("WorkspacePicker", () => {
  it("renders a 'Only one workspace' placeholder for solo membership", async () => {
    mockListMyWorkspaces.mockResolvedValueOnce({
      workspaces: [
        {
          org_id: "org_acme",
          display_name: "Acme",
          slug: "acme",
          role: "admin",
          member_count: 47,
          last_active_at: "2026-05-05T15:51:02.110Z",
          is_current: true,
        },
      ],
    });
    render(<WorkspacePicker currentOrgId="org_acme" onSwitch={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByText(/Only one workspace/)).toBeInTheDocument(),
    );
  });

  it("renders multi-workspace rows with role + member count", async () => {
    mockListMyWorkspaces.mockResolvedValueOnce({
      workspaces: [
        {
          org_id: "org_personal",
          display_name: "Personal",
          slug: "personal",
          role: "owner",
          member_count: 1,
          last_active_at: "2026-05-04T08:14:00.000Z",
          is_current: false,
        },
        {
          org_id: "org_acme",
          display_name: "Acme",
          slug: "acme",
          role: "admin",
          member_count: 47,
          last_active_at: "2026-05-05T15:51:02.110Z",
          is_current: true,
        },
      ],
    });
    render(<WorkspacePicker currentOrgId="org_acme" onSwitch={vi.fn()} />);

    await waitFor(() =>
      expect(
        screen.getByRole("menuitemradio", { name: /Acme/ }),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText(/owner · 1 member/)).toBeInTheDocument();
    expect(screen.getByText(/admin · 47 members/)).toBeInTheDocument();
  });

  it("invokes onSwitch with the chosen orgId — proves the G4 fix end-to-end", async () => {
    mockListMyWorkspaces.mockResolvedValueOnce({
      workspaces: [
        {
          org_id: "org_personal",
          display_name: "Personal",
          slug: "personal",
          role: "owner",
          member_count: 1,
          last_active_at: "2026-05-04T08:14:00.000Z",
          is_current: false,
        },
        {
          org_id: "org_acme",
          display_name: "Acme",
          slug: "acme",
          role: "admin",
          member_count: 47,
          last_active_at: "2026-05-05T15:51:02.110Z",
          is_current: true,
        },
      ],
    });
    const onSwitch = vi.fn();
    const user = userEvent.setup();
    render(<WorkspacePicker currentOrgId="org_acme" onSwitch={onSwitch} />);
    await user.click(
      await screen.findByRole("menuitemradio", { name: /Personal/i }),
    );
    expect(onSwitch).toHaveBeenCalledWith("org_personal");
  });

  it("shows a retry control on fetch failure", async () => {
    mockListMyWorkspaces.mockRejectedValueOnce(
      new Error("Network unreachable"),
    );
    render(<WorkspacePicker currentOrgId="org_acme" onSwitch={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Network unreachable",
      ),
    );
    expect(screen.getByRole("button", { name: /Retry/i })).toBeInTheDocument();
  });
});
