import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

describe("<ProfilePage> linked accounts (PRD FR-U1)", () => {
  const LINKED = [
    {
      kind: "wallet",
      id: "wid_1",
      address: "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
      chainName: "Base",
    },
    {
      kind: "oidc",
      id: "oid_1",
      provider: "google",
      email: "sarah@gmail.test",
    },
  ] as const;

  it("hides the panel entirely when no data is supplied", () => {
    render(<ProfilePage person={PERSON} onSignOut={() => undefined} />);
    expect(screen.queryByTestId("profile-linked-accounts")).toBeNull();
  });

  it("renders every linked identity with its kind-appropriate detail", () => {
    render(
      <ProfilePage
        person={PERSON}
        onSignOut={() => undefined}
        linkedIdentities={LINKED}
      />,
    );
    expect(screen.getByTestId("profile-linked-accounts")).toBeInTheDocument();
    const wallet = screen.getByTestId("profile-linked-wallet");
    expect(wallet.querySelector("input")).toHaveValue(
      "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
    );
    expect(wallet.textContent).toContain("Base");
    const oidc = screen.getByTestId("profile-linked-oidc");
    expect(oidc.querySelector("input")).toHaveValue("sarah@gmail.test");
  });

  it("shows link CTAs only when the host wires them, and hides Link Google once linked", () => {
    const onLinkWallet = vi.fn(async () => ({ status: "linked" }) as const);
    const { rerender } = render(
      <ProfilePage
        person={WALLET_PERSON}
        onSignOut={() => undefined}
        linkedIdentities={[]}
        onLinkWallet={onLinkWallet}
        onLinkGoogle={() => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("profile-link-wallet"));
    expect(onLinkWallet).toHaveBeenCalledTimes(1);
    expect(onLinkWallet).toHaveBeenCalledWith({ confirmMerge: false });
    // Wallet person + no Google yet → the "add an email" phrasing.
    expect(screen.getByTestId("profile-link-google").textContent).toContain(
      "Add an email",
    );
    // Once a Google identity is linked, the Google CTA disappears.
    rerender(
      <ProfilePage
        person={WALLET_PERSON}
        onSignOut={() => undefined}
        linkedIdentities={LINKED}
        onLinkWallet={onLinkWallet}
        onLinkGoogle={() => undefined}
      />,
    );
    expect(screen.queryByTestId("profile-link-google")).toBeNull();
    // No handlers → no CTAs at all.
    rerender(
      <ProfilePage
        person={WALLET_PERSON}
        onSignOut={() => undefined}
        linkedIdentities={[]}
      />,
    );
    expect(screen.queryByTestId("profile-link-wallet")).toBeNull();
    expect(screen.queryByTestId("profile-link-google")).toBeNull();
  });
});

describe("<ProfilePage> unlink (PRD FR-U1/L5)", () => {
  const LINKED = [
    {
      kind: "wallet",
      id: "wid_1",
      address: "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
      chainName: "Base",
    },
    {
      kind: "oidc",
      id: "oid_1",
      provider: "google",
      email: "sarah@gmail.test",
    },
  ] as const;

  it("renders no Unlink control until the host wires onUnlinkIdentity", () => {
    render(
      <ProfilePage
        person={PERSON}
        onSignOut={() => undefined}
        linkedIdentities={LINKED}
      />,
    );
    expect(screen.queryByTestId("profile-unlink-wid_1")).toBeNull();
  });

  it("calls onUnlinkIdentity(kind, id) for the clicked row", async () => {
    const onUnlinkIdentity = vi.fn(async () => undefined);
    render(
      <ProfilePage
        person={PERSON}
        onSignOut={() => undefined}
        linkedIdentities={LINKED}
        onUnlinkIdentity={onUnlinkIdentity}
      />,
    );
    fireEvent.click(screen.getByTestId("profile-unlink-oid_1"));
    expect(onUnlinkIdentity).toHaveBeenCalledWith("oidc", "oid_1");
  });

  it("surfaces the last-sign-in-method guard message next to the row", async () => {
    const onUnlinkIdentity = vi.fn(async () => {
      throw new Error(
        "This is your only way to sign in. Link another method before removing it.",
      );
    });
    render(
      <ProfilePage
        person={PERSON}
        onSignOut={() => undefined}
        linkedIdentities={[LINKED[0]]}
        onUnlinkIdentity={onUnlinkIdentity}
      />,
    );
    fireEvent.click(screen.getByTestId("profile-unlink-wid_1"));
    const err = await screen.findByTestId("profile-unlink-error-wid_1");
    expect(err).toHaveTextContent("only way to sign in");
  });
});

describe("<ProfilePage> merge-confirm dialog (PRD FR-U2)", () => {
  const person = WALLET_PERSON;

  it("opens the dialog on merge_required and re-links with consent on confirm", async () => {
    const onLinkWallet = vi
      .fn()
      // First attempt: the wallet belongs to another account.
      .mockResolvedValueOnce({
        status: "merge_required",
        message: "This wallet already belongs to another account.",
      })
      // Confirmed attempt (re-signed by the host): the merge completes.
      .mockResolvedValueOnce({ status: "merged" });

    render(
      <ProfilePage
        person={person}
        onSignOut={() => undefined}
        linkedIdentities={[]}
        onLinkWallet={onLinkWallet}
      />,
    );

    fireEvent.click(screen.getByTestId("profile-link-wallet"));
    // The explicit merge-confirm dialog appears with the server's reason.
    const message = await screen.findByTestId("profile-merge-message");
    expect(message).toHaveTextContent("already belongs to another account");

    fireEvent.click(screen.getByTestId("profile-merge-confirm"));
    // Second call carries the FR-U2 consent flag.
    expect(onLinkWallet).toHaveBeenLastCalledWith({ confirmMerge: true });

    // Dialog closes on a successful merge.
    await waitFor(() =>
      expect(screen.queryByTestId("profile-merge-message")).toBeNull(),
    );
    expect(onLinkWallet).toHaveBeenCalledTimes(2);
  });

  it("cancel closes the dialog without merging", async () => {
    const onLinkWallet = vi
      .fn()
      .mockResolvedValueOnce({ status: "merge_required" });
    render(
      <ProfilePage
        person={person}
        onSignOut={() => undefined}
        linkedIdentities={[]}
        onLinkWallet={onLinkWallet}
      />,
    );
    fireEvent.click(screen.getByTestId("profile-link-wallet"));
    await screen.findByTestId("profile-merge-message");
    fireEvent.click(screen.getByTestId("profile-merge-cancel"));
    await waitFor(() =>
      expect(screen.queryByTestId("profile-merge-message")).toBeNull(),
    );
    // Only the initial attempt ran — no merge without consent.
    expect(onLinkWallet).toHaveBeenCalledTimes(1);
  });

  it("shows an inline error when a plain link fails", async () => {
    const onLinkWallet = vi
      .fn()
      .mockResolvedValueOnce({ status: "error", message: "chain not allowed" });
    render(
      <ProfilePage
        person={person}
        onSignOut={() => undefined}
        linkedIdentities={[]}
        onLinkWallet={onLinkWallet}
      />,
    );
    fireEvent.click(screen.getByTestId("profile-link-wallet"));
    const err = await screen.findByTestId("profile-link-error");
    expect(err).toHaveTextContent("chain not allowed");
    expect(screen.queryByTestId("profile-merge-message")).toBeNull();
  });
});
