import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ProjectMembersTab, type ProjectMember } from "./ProjectMembersTab";

const OWNER_ID = "user-owner";

const MEMBERS: ReadonlyArray<ProjectMember> = [
  {
    userId: OWNER_ID,
    displayName: "Sarah Chen",
    email: "sarah@acme.com",
    role: "owner",
    joinedAt: new Date(Date.now() - 86_400_000).toISOString(),
  },
  {
    userId: "user-marcus",
    displayName: "Marcus Wells",
    email: "marcus@acme.com",
    role: "editor",
    joinedAt: new Date(Date.now() - 3_600_000).toISOString(),
  },
  {
    userId: "user-priya",
    displayName: "Priya Singh",
    role: "viewer",
    joinedAt: new Date(Date.now() - 600_000).toISOString(),
  },
];

describe("ProjectMembersTab", () => {
  it("renders a skeleton list while members is null", () => {
    render(
      <ProjectMembersTab
        members={null}
        canManage={false}
        ownerUserId={OWNER_ID}
      />,
    );
    expect(screen.getByTestId("project-members-tab")).toHaveAttribute(
      "data-state",
      "loading",
    );
    expect(screen.getAllByTestId("project-members-skeleton")).toHaveLength(3);
  });

  it("renders an empty state when there are no members", () => {
    render(
      <ProjectMembersTab
        members={[]}
        canManage={false}
        ownerUserId={OWNER_ID}
      />,
    );
    expect(screen.getByTestId("project-members-empty")).toBeInTheDocument();
  });

  it("renders one row per member with role information", () => {
    render(
      <ProjectMembersTab
        members={MEMBERS}
        canManage={false}
        ownerUserId={OWNER_ID}
      />,
    );
    const rows = screen.getAllByTestId("project-member-row");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveAttribute("data-user-id", OWNER_ID);
    expect(rows[0]).toHaveAttribute("data-role", "owner");
    // Read-only: owner is rendered as a pill, not a select.
    const pills = screen.getAllByTestId("project-member-role-pill");
    expect(pills).toHaveLength(3);
  });

  it("hides the add-member trigger when canManage is false or onAddMember is missing", () => {
    const onAddMember = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(
      <ProjectMembersTab
        members={MEMBERS}
        canManage={false}
        ownerUserId={OWNER_ID}
        onAddMember={onAddMember}
      />,
    );
    expect(
      screen.queryByTestId("project-members-add-trigger"),
    ).not.toBeInTheDocument();
    rerender(
      <ProjectMembersTab
        members={MEMBERS}
        canManage={true}
        ownerUserId={OWNER_ID}
      />,
    );
    expect(
      screen.queryByTestId("project-members-add-trigger"),
    ).not.toBeInTheDocument();
  });

  it("opens the add-member dialog and submits a single member with selected role", async () => {
    const onAddMember = vi.fn().mockResolvedValue(undefined);
    render(
      <ProjectMembersTab
        members={MEMBERS}
        canManage={true}
        ownerUserId={OWNER_ID}
        onAddMember={onAddMember}
      />,
    );
    const user = userEvent.setup();
    await user.click(screen.getByTestId("project-members-add-trigger"));
    expect(screen.getByTestId("project-add-member-dialog")).toBeInTheDocument();
    const input = screen.getByTestId("project-add-member-input");
    await user.type(input, "new@acme.com");
    const select = screen.getByTestId(
      "project-add-member-role",
    ) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "viewer" } });
    await user.click(screen.getByTestId("project-add-member-submit"));
    expect(onAddMember).toHaveBeenCalledWith("new@acme.com", "viewer");
  });

  it("shows an inline error when onAddMember rejects", async () => {
    const onAddMember = vi.fn().mockRejectedValue(new Error("user not found"));
    render(
      <ProjectMembersTab
        members={MEMBERS}
        canManage={true}
        ownerUserId={OWNER_ID}
        onAddMember={onAddMember}
      />,
    );
    const user = userEvent.setup();
    await user.click(screen.getByTestId("project-members-add-trigger"));
    await user.type(
      screen.getByTestId("project-add-member-input"),
      "ghost@acme.com",
    );
    await user.click(screen.getByTestId("project-add-member-submit"));
    expect(
      await screen.findByTestId("project-add-member-error"),
    ).toHaveTextContent("user not found");
  });

  it("allows changing a non-owner role via the select", async () => {
    const onChangeMemberRole = vi.fn().mockResolvedValue(undefined);
    render(
      <ProjectMembersTab
        members={MEMBERS}
        canManage={true}
        ownerUserId={OWNER_ID}
        onChangeMemberRole={onChangeMemberRole}
      />,
    );
    const selects = screen.getAllByTestId("project-member-role-select");
    // Two non-owner members → two selects.
    expect(selects).toHaveLength(2);
    await act(async () => {
      fireEvent.change(selects[0]!, { target: { value: "viewer" } });
    });
    expect(onChangeMemberRole).toHaveBeenCalledWith("user-marcus", "viewer");
  });

  it("calls onRemoveMember when the row remove button is clicked", async () => {
    const onRemoveMember = vi.fn().mockResolvedValue(undefined);
    render(
      <ProjectMembersTab
        members={MEMBERS}
        canManage={true}
        ownerUserId={OWNER_ID}
        onRemoveMember={onRemoveMember}
      />,
    );
    const removes = screen.getAllByTestId("project-member-remove");
    expect(removes).toHaveLength(2); // owner row has no remove button
    await act(async () => {
      fireEvent.click(removes[0]!);
    });
    expect(onRemoveMember).toHaveBeenCalledWith("user-marcus");
  });

  it("never offers role-change or remove for the owner row", () => {
    render(
      <ProjectMembersTab
        members={MEMBERS}
        canManage={true}
        ownerUserId={OWNER_ID}
        onChangeMemberRole={vi.fn().mockResolvedValue(undefined)}
        onRemoveMember={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    const rows = screen.getAllByTestId("project-member-row");
    const ownerRow = rows.find(
      (r) => r.getAttribute("data-user-id") === OWNER_ID,
    )!;
    expect(
      ownerRow.querySelector('[data-testid="project-member-role-select"]'),
    ).toBeNull();
    expect(
      ownerRow.querySelector('[data-testid="project-member-remove"]'),
    ).toBeNull();
  });
});
