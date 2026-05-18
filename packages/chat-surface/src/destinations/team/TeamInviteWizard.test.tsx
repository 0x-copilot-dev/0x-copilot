// TeamInviteWizard — form + RevealOnce success.

import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TeamInviteWizard } from "./TeamInviteWizard";

describe("TeamInviteWizard", () => {
  it("renders email + role radios + note + dialog ARIA", () => {
    render(
      <TeamInviteWizard
        onInvite={vi.fn().mockResolvedValue(null)}
        onCopy={vi.fn()}
      />,
    );
    const dialog = screen.getByTestId("team-invite-wizard");
    expect(dialog).toHaveAttribute("role", "dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByTestId("team-invite-email")).toBeInTheDocument();
    expect(screen.getByTestId("team-invite-role-admin")).toBeInTheDocument();
    expect(screen.getByTestId("team-invite-role-member")).toBeInTheDocument();
    expect(screen.getByTestId("team-invite-role-guest")).toBeInTheDocument();
    expect(screen.getByTestId("team-invite-note")).toBeInTheDocument();
  });

  it("submit is disabled when the email is invalid", () => {
    render(
      <TeamInviteWizard
        onInvite={vi.fn().mockResolvedValue(null)}
        onCopy={vi.fn()}
      />,
    );
    const submit = screen.getByTestId(
      "team-invite-submit",
    ) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    fireEvent.change(screen.getByTestId("team-invite-email"), {
      target: { value: "not-an-email" },
    });
    expect(submit.disabled).toBe(true);
    fireEvent.change(screen.getByTestId("team-invite-email"), {
      target: { value: "x@y.test" },
    });
    expect(submit.disabled).toBe(false);
  });

  it("submits InviteRequest with email + role + note", async () => {
    const onInvite = vi
      .fn()
      .mockResolvedValue({ invite_link: "https://atlas/i/abc" });
    render(<TeamInviteWizard onInvite={onInvite} onCopy={vi.fn()} />);
    fireEvent.change(screen.getByTestId("team-invite-email"), {
      target: { value: "newbie@acme.test" },
    });
    fireEvent.click(
      within(screen.getByTestId("team-invite-role-admin")).getByRole("radio"),
    );
    fireEvent.change(screen.getByTestId("team-invite-note"), {
      target: { value: "welcome" },
    });
    fireEvent.click(screen.getByTestId("team-invite-submit"));
    await waitFor(() => expect(onInvite).toHaveBeenCalledTimes(1));
    expect(onInvite).toHaveBeenCalledWith({
      email: "newbie@acme.test",
      role: "admin",
      note: "welcome",
    });
  });

  it("renders RevealOnce on success and dismisses to mask", async () => {
    const onInvite = vi
      .fn()
      .mockResolvedValue({ invite_link: "https://atlas/i/xyz" });
    const onCopy = vi.fn().mockResolvedValue(undefined);
    render(<TeamInviteWizard onInvite={onInvite} onCopy={onCopy} />);
    fireEvent.change(screen.getByTestId("team-invite-email"), {
      target: { value: "a@b.test" },
    });
    fireEvent.click(screen.getByTestId("team-invite-submit"));
    await waitFor(() =>
      expect(screen.getByTestId("team-invite-success")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("team-invite-link-clear")).toHaveTextContent(
      "https://atlas/i/xyz",
    );
    fireEvent.click(screen.getByTestId("team-invite-link-dismiss"));
    expect(screen.getByTestId("team-invite-link-masked")).toBeInTheDocument();
  });

  it("renders an error when the host returns null", async () => {
    const onInvite = vi.fn().mockResolvedValue(null);
    render(<TeamInviteWizard onInvite={onInvite} onCopy={vi.fn()} />);
    fireEvent.change(screen.getByTestId("team-invite-email"), {
      target: { value: "a@b.test" },
    });
    fireEvent.click(screen.getByTestId("team-invite-submit"));
    await waitFor(() =>
      expect(screen.getByTestId("team-invite-error")).toBeInTheDocument(),
    );
  });

  it("fires onCancel and onDone callbacks", async () => {
    const onCancel = vi.fn();
    const onDone = vi.fn();
    const onInvite = vi
      .fn()
      .mockResolvedValue({ invite_link: "https://atlas/i/abc" });
    render(
      <TeamInviteWizard
        onInvite={onInvite}
        onCopy={vi.fn()}
        onCancel={onCancel}
        onDone={onDone}
      />,
    );
    fireEvent.click(screen.getByTestId("team-invite-cancel"));
    expect(onCancel).toHaveBeenCalledTimes(1);
    fireEvent.change(screen.getByTestId("team-invite-email"), {
      target: { value: "a@b.test" },
    });
    fireEvent.click(screen.getByTestId("team-invite-submit"));
    await waitFor(() =>
      expect(screen.getByTestId("team-invite-done")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("team-invite-done"));
    expect(onDone).toHaveBeenCalledTimes(1);
  });
});
