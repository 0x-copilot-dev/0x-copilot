// @vitest-environment jsdom
// PR-5.9 — the desktop Settings mount wires every settings slug to its
// chat-surface section body, and the profile gate keeps the team sections off
// the solo desktop profile (with the solo footer shown).

import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DeploymentProfileProvider } from "@0x-copilot/chat-surface";
import type {
  RendererSession,
  Transport,
  TypedRequest,
} from "@0x-copilot/chat-transport";

import { SettingsMount } from "./SettingsMount";

const SESSION: RendererSession = {
  workspaceId: "org_local",
  expiresAt: Date.now() + 60_000,
  displayName: "Parth",
  email: "parth@local.test",
};

function fakeTransport(): Transport {
  const request = (async (req: TypedRequest) => {
    if (req.path === "/v1/settings/provider-keys") return { keys: [] };
    if (req.path === "/v1/me/api-keys") return { keys: [] };
    // D4 — the Model & behavior spend card reads the caller's monthly cap.
    if (req.path === "/v1/budgets/me") return { currency: "USD", budgets: [] };
    if (req.path === "/v1/me/profile") {
      return {
        user_id: "usr_local",
        email: "parth@local.test",
        email_verified_at: null,
        display_name: "Parth",
        title: null,
        timezone: null,
        locale: null,
        working_hours: null,
        avatar_url: null,
        bio: null,
        updated_at: "2026-01-01T00:00:00Z",
      };
    }
    return {};
  }) as Transport["request"];
  return {
    request,
    subscribeServerSentEvents: () => ({ close: () => undefined }),
    getSession: () => ({ bearer: null }),
    capabilities: () => ({
      substrate: "desktop-webview",
      nativeSecretStorage: true,
      fileSystemAccess: false,
      clipboardWrite: false,
      openExternal: false,
    }),
  };
}

function mount(onSignOut: () => void = () => undefined): void {
  render(
    <DeploymentProfileProvider profile="single_user_desktop">
      <SettingsMount
        transport={fakeTransport()}
        session={SESSION}
        onSignOut={onSignOut}
      />
    </DeploymentProfileProvider>,
  );
}

function clickSlug(slug: string): void {
  const tab = document.querySelector(`[data-slug="${slug}"]`);
  expect(tab).not.toBeNull();
  fireEvent.click(tab as Element);
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("<SettingsMount>", () => {
  it("renders the Profile section by default with the solo footer and no team nav", () => {
    mount();
    // Default section body is Profile.
    expect(screen.getByTestId("profile-page")).toBeTruthy();
    // Solo footer shows; team sections are gated out entirely.
    expect(screen.getByTestId("settings-solo-footer")).toBeTruthy();
    for (const teamSlug of ["workspace", "members", "billing", "audit"]) {
      expect(document.querySelector(`[data-slug="${teamSlug}"]`)).toBeNull();
    }
  });

  it("wires the Account + Models & keys sections", async () => {
    mount();

    clickSlug("appearance");
    expect(screen.getByTestId("appearance-page")).toBeTruthy();

    clickSlug("shortcuts");
    expect(screen.getByTestId("shortcuts-page")).toBeTruthy();

    clickSlug("provider-keys");
    // Provider keys reads through the real Transport-backed port (async).
    expect(await screen.findByTestId("provider-keys-page")).toBeTruthy();

    clickSlug("local-models");
    // Stubbed "not running" status → honest setup steps, not a fake list.
    expect(screen.getByTestId("local-models-setup")).toBeTruthy();

    clickSlug("model-behavior");
    expect(screen.getByTestId("model-behavior-page")).toBeTruthy();
  });

  it("delegates the Profile Sign out button to the host onSignOut handler", () => {
    const onSignOut = vi.fn();
    mount(onSignOut);
    // Profile is the default section. The Sign out CTA is presentational —
    // it must invoke the host-supplied handler (which performs the real
    // session clear via the authSignOut IPC), not swallow the click.
    fireEvent.click(screen.getByTestId("profile-signout"));
    expect(onSignOut).toHaveBeenCalledTimes(1);
  });

  it("wires Data & privacy and Notifications", () => {
    mount();

    clickSlug("privacy");
    expect(screen.getByTestId("privacy-page")).toBeTruthy();

    clickSlug("notifications");
    expect(screen.getByTestId("notifications-page")).toBeTruthy();
  });

  it("wires the Advanced group once expanded (App lock + Developer tokens)", async () => {
    mount();

    // Advanced is collapsible and starts collapsed — expand it first.
    fireEvent.click(screen.getByTestId("settings-group-toggle-advanced"));

    clickSlug("app-lock");
    expect(screen.getByTestId("app-lock-page")).toBeTruthy();
    // Desktop reports native secret storage → Touch ID is available (enabled).
    const touchId = screen.getByTestId(
      "app-lock-require-touch-id",
    ) as HTMLInputElement;
    expect(touchId.disabled).toBe(false);

    clickSlug("developer-tokens");
    expect(await screen.findByTestId("developer-tokens-page")).toBeTruthy();
  });
});
