import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ProfilePage, type ProfilePagePerson } from "./ProfilePage";

const PERSON: ProfilePagePerson = {
  user_id: "user_test",
  display_name: "Sarah",
  avatar_url: null,
  anchor: { kind: "email", email: "sarah@acme.test", verified: true },
  authMethod: "google",
};

const WALLET_PERSON: ProfilePagePerson = {
  user_id: "user_wallet",
  display_name: "0x5aAe…eAed",
  avatar_url: null,
  anchor: {
    kind: "wallet",
    address: "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
    chainId: 8453,
    chainLabel: "Base",
  },
  authMethod: "siwe",
};

describe("<ProfilePage>", () => {
  it("renders display name, read-only email + Verified badge, and a sign-out CTA", () => {
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
    expect(screen.getByTestId("profile-verified-badge")).toBeInTheDocument();
    expect(screen.getByTestId("profile-signed-in-with")).toHaveTextContent(
      "Signed in with Google",
    );
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

  it("renders a wallet anchor honestly — address + chain, no fake email, no verify nag", () => {
    render(
      <ProfilePage
        person={WALLET_PERSON}
        onSaveDisplayName={() => undefined}
        onSignOut={() => undefined}
      />,
    );
    // The wallet address is shown (checksummed) — never an email input.
    const wallet = screen.getByTestId("profile-wallet-address");
    expect(wallet).toHaveValue("0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed");
    expect(wallet).toHaveAttribute("readonly");
    expect(screen.getByTestId("profile-wallet-chain")).toHaveTextContent(
      "Base",
    );
    expect(screen.getByTestId("profile-signed-in-with")).toHaveTextContent(
      "Signed in with a wallet",
    );
    // No email field and no "verify" nag for a structurally-unverifiable wallet.
    expect(screen.queryByTestId("profile-email")).toBeNull();
    expect(screen.queryByTestId("profile-verified-badge")).toBeNull();
    // And nothing that looks like the @wallet.invalid placeholder is rendered.
    expect(screen.queryByText(/wallet\.invalid/i)).toBeNull();
  });

  it("renders the display name read-only and hides Save when onSaveDisplayName is omitted", () => {
    render(<ProfilePage person={PERSON} onSignOut={() => undefined} />);
    expect(screen.getByTestId("profile-display-name")).toHaveAttribute(
      "readonly",
    );
    expect(screen.queryByTestId("profile-save")).toBeNull();
    // Sign out is still available.
    expect(screen.getByTestId("profile-signout")).toBeInTheDocument();
  });
});
