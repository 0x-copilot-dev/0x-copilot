import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ProfilePage, type ProfilePagePerson } from "./ProfilePage";

const PERSON: ProfilePagePerson = {
  user_id: "user_test",
  email: "sarah@acme.test",
  display_name: "Sarah",
  avatar_url: null,
};

describe("<ProfilePage>", () => {
  it("renders display name, read-only email, and a sign-out CTA", () => {
    render(
      <ProfilePage
        person={PERSON}
        onSaveDisplayName={() => undefined}
        onSignOut={() => undefined}
      />,
    );
    expect(screen.getByTestId("profile-display-name")).toHaveValue("Sarah");
    const email = screen.getByTestId("profile-email");
    expect(email).toHaveValue("sarah@acme.test");
    expect(email).toHaveAttribute("readonly");
    expect(screen.getByTestId("profile-signout")).toBeInTheDocument();
  });

  it("renders initials when no avatar_url is set", () => {
    render(
      <ProfilePage
        person={PERSON}
        onSaveDisplayName={() => undefined}
        onSignOut={() => undefined}
      />,
    );
    expect(screen.getByTestId("profile-avatar")).toHaveTextContent("S");
  });

  it("renders the avatar image when avatar_url is set", () => {
    render(
      <ProfilePage
        person={{ ...PERSON, avatar_url: "https://example.test/a.png" }}
        onSaveDisplayName={() => undefined}
        onSignOut={() => undefined}
      />,
    );
    const img = screen.getByTestId("profile-avatar").querySelector("img");
    expect(img).not.toBeNull();
    expect(img).toHaveAttribute("src", "https://example.test/a.png");
  });

  it("save button is disabled until the display name changes", () => {
    render(
      <ProfilePage
        person={PERSON}
        onSaveDisplayName={() => undefined}
        onSignOut={() => undefined}
      />,
    );
    const save = screen.getByTestId("profile-save");
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByTestId("profile-display-name"), {
      target: { value: "Sarah K" },
    });
    expect(save).not.toBeDisabled();
  });

  it("invokes onSaveDisplayName with the trimmed value", () => {
    const onSaveDisplayName = vi.fn();
    render(
      <ProfilePage
        person={PERSON}
        onSaveDisplayName={onSaveDisplayName}
        onSignOut={() => undefined}
      />,
    );
    fireEvent.change(screen.getByTestId("profile-display-name"), {
      target: { value: "  Sarah Kim  " },
    });
    fireEvent.click(screen.getByTestId("profile-save"));
    expect(onSaveDisplayName).toHaveBeenCalledWith("Sarah Kim");
  });

  it("invokes onSignOut when the Sign out button is clicked", () => {
    const onSignOut = vi.fn();
    render(
      <ProfilePage
        person={PERSON}
        onSaveDisplayName={() => undefined}
        onSignOut={onSignOut}
      />,
    );
    fireEvent.click(screen.getByTestId("profile-signout"));
    expect(onSignOut).toHaveBeenCalled();
  });
});
