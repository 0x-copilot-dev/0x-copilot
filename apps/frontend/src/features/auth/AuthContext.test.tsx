/**
 * Tests for AuthContext + LoginScreen + MfaPrompt (A9).
 *
 * Mocks the auth API client so the state machine can be exercised
 * without a backend. Each test resets ``localStorage`` + ``fetch`` so
 * the in-memory bearer doesn't bleed across cases.
 */

import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider, useAuth } from "./AuthContext";
import { LoginScreen } from "./LoginScreen";
import { MfaPrompt } from "./MfaPrompt";
import * as authApi from "../../api/authApi";

function StatusProbe(): ReactElement {
  const auth = useAuth();
  return (
    <div data-testid="status">
      {auth.status}
      {auth.identity && `:${auth.identity.user_id}`}
      {auth.error && `:err=${auth.error}`}
    </div>
  );
}

describe("AuthContext", () => {
  beforeEach(() => {
    try {
      window.localStorage?.clear?.();
    } catch {
      /* no localStorage in this test env */
    }
    vi.restoreAllMocks();
  });

  it("flips from loading to anonymous on a 401 from /v1/auth/session", async () => {
    vi.spyOn(authApi, "fetchCurrentSession").mockRejectedValue(
      new Error("Request failed with 401"),
    );
    render(
      <AuthProvider persistBearer={false}>
        <StatusProbe />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId("status").textContent).toBe("anonymous");
    });
  });

  it("flips to authenticated when /v1/auth/session returns identity", async () => {
    vi.spyOn(authApi, "fetchCurrentSession").mockResolvedValue({
      identity: {
        org_id: "org_a",
        user_id: "usr_a",
        roles: ["employee"],
        permission_scopes: ["runtime:use"],
      },
    });
    render(
      <AuthProvider persistBearer={false}>
        <StatusProbe />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId("status").textContent).toBe(
        "authenticated:usr_a",
      );
    });
  });

  it("flips to error (not anonymous) on a non-401 backend failure", async () => {
    vi.spyOn(authApi, "fetchCurrentSession").mockRejectedValue(
      new Error("network down"),
    );
    render(
      <AuthProvider persistBearer={false}>
        <StatusProbe />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId("status").textContent).toContain("error");
      expect(screen.getByTestId("status").textContent).toContain(
        "err=network down",
      );
    });
  });

  it("login() with requires_mfa=true transitions to mfa_pending", async () => {
    vi.spyOn(authApi, "fetchCurrentSession").mockRejectedValue(
      new Error("Request failed with 401"),
    );
    vi.spyOn(authApi, "loginWithPassword").mockResolvedValue({
      user_id: "usr_a",
      session_id: "sid_a",
      bearer_token: "stub.bearer",
      expires_at: "2099-01-01T00:00:00Z",
      requires_mfa: true,
    });

    function LoginHarness(): ReactElement {
      const auth = useAuth();
      return (
        <>
          <StatusProbe />
          <button
            type="button"
            onClick={() =>
              void auth.login({
                orgId: "org_a",
                email: "alice@x",
                password: "p",
              })
            }
          >
            login
          </button>
        </>
      );
    }
    render(
      <AuthProvider persistBearer={false}>
        <LoginHarness />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId("status").textContent).toBe("anonymous");
    });
    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: "login" }));
    });
    await waitFor(() => {
      expect(screen.getByTestId("status").textContent).toBe("mfa_pending");
    });
  });
});

describe("LoginScreen — email-first (PR 5.1)", () => {
  beforeEach(() => {
    try {
      window.localStorage?.clear?.();
    } catch {
      /* no localStorage in this test env */
    }
    vi.restoreAllMocks();
  });

  it("renders the brand pane + autofocused email field", async () => {
    vi.spyOn(authApi, "fetchCurrentSession").mockRejectedValue(
      new Error("Request failed with 401"),
    );
    render(
      <AuthProvider persistBearer={false}>
        <LoginScreen />
      </AuthProvider>,
    );
    expect(await screen.findByTestId("login-screen")).toBeInTheDocument();
    expect(screen.getAllByText(/Atlas/).length).toBeGreaterThan(0);
    expect(screen.getByText(/SOC 2 Type II/)).toBeInTheDocument();
    expect(screen.getByTestId("login-email-input")).toBeInTheDocument();
  });

  it("submits an SSO email and routes to the OIDC start URL", async () => {
    vi.spyOn(authApi, "fetchCurrentSession").mockRejectedValue(
      new Error("Request failed with 401"),
    );
    vi.spyOn(authApi, "discoverAuth").mockResolvedValue({
      kind: "sso",
      domain: "acme.com",
      org_id: "org_acme",
      org_display_name: "Acme Inc.",
      org_logo_url: null,
      member_count: 12483,
      provider_id: "prv_okta",
      provider_kind: "oidc",
      provider_display_name: "Okta",
      sso_enforced: false,
      magic_link_supported: true,
      message: null,
    });
    const assignSpy = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: {
        assign: assignSpy,
        origin: "http://localhost",
        pathname: "/",
        search: "",
        href: "http://localhost/",
      },
    });
    render(
      <AuthProvider persistBearer={false}>
        <LoginScreen />
      </AuthProvider>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("login-email-input")).toBeInTheDocument(),
    );
    await userEvent.type(
      screen.getByTestId("login-email-input"),
      "sarah@acme.com",
    );
    // Submit synchronously (skips the 450ms debounce by using the form
    // submit path which calls discover directly).
    await act(async () => {
      await userEvent.click(screen.getByTestId("login-submit"));
    });
    await waitFor(() => {
      expect(assignSpy).toHaveBeenCalled();
    });
    const url = String(assignSpy.mock.calls[0]?.[0] ?? "");
    expect(url).toContain("/v1/auth/oidc/prv_okta/start");
    expect(url).toContain("org_id=org_acme");
  });

  it("submits a personal email and shows the magic-link sent card", async () => {
    vi.spyOn(authApi, "fetchCurrentSession").mockRejectedValue(
      new Error("Request failed with 401"),
    );
    vi.spyOn(authApi, "discoverAuth").mockResolvedValue({
      kind: "personal",
      domain: "gmail.com",
      org_id: null,
      org_display_name: null,
      org_logo_url: null,
      member_count: null,
      provider_id: null,
      provider_kind: null,
      provider_display_name: "Google",
      sso_enforced: false,
      magic_link_supported: true,
      message: null,
    });
    const startSpy = vi
      .spyOn(authApi, "startMagicLink")
      .mockResolvedValue({ status: "queued", expires_in_seconds: 900 });
    render(
      <AuthProvider persistBearer={false}>
        <LoginScreen />
      </AuthProvider>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("login-email-input")).toBeInTheDocument(),
    );
    await userEvent.type(
      screen.getByTestId("login-email-input"),
      "me@gmail.com",
    );
    await act(async () => {
      await userEvent.click(screen.getByTestId("login-submit"));
    });
    await waitFor(() => {
      expect(startSpy).toHaveBeenCalledWith({
        email: "me@gmail.com",
        return_to: undefined,
      });
    });
    expect(await screen.findByText(/Check your email/)).toBeInTheDocument();
  });

  it("renders the workspace picker rows when consume returns multiple workspaces", async () => {
    vi.spyOn(authApi, "fetchCurrentSession").mockRejectedValue(
      new Error("Request failed with 401"),
    );
    vi.spyOn(authApi, "consumeMagicLink").mockResolvedValue({
      outcome: "workspace_pick_required",
      user_id: "usr_x",
      pick_token: "pick_xyz",
      expires_in_seconds: 300,
      workspaces: [
        {
          org_id: "org_acme",
          display_name: "Acme Inc.",
          logo_url: null,
          role: "Admin",
          member_count: 12483,
          last_active_at: null,
        },
        {
          org_id: "org_acme_eu",
          display_name: "Acme — EU",
          logo_url: null,
          role: "Member",
          member_count: 1240,
          last_active_at: null,
        },
      ],
    });
    Object.defineProperty(window, "location", {
      configurable: true,
      value: {
        assign: vi.fn(),
        origin: "http://localhost",
        pathname: "/auth/magic-link/callback",
        search: "?token=plain",
        href: "http://localhost/auth/magic-link/callback?token=plain",
      },
    });
    render(
      <AuthProvider persistBearer={false}>
        <LoginScreen />
      </AuthProvider>,
    );
    expect(await screen.findByText(/Acme Inc\./)).toBeInTheDocument();
    expect(screen.getByText(/Acme — EU/)).toBeInTheDocument();
    expect(screen.getByTestId("login-pick-org_acme")).toBeInTheDocument();
  });

  it("workspace pick → selectWorkspace exchanges the pick_token", async () => {
    vi.spyOn(authApi, "fetchCurrentSession").mockRejectedValue(
      new Error("Request failed with 401"),
    );
    vi.spyOn(authApi, "consumeMagicLink").mockResolvedValue({
      outcome: "workspace_pick_required",
      user_id: "usr_x",
      pick_token: "pick_xyz",
      expires_in_seconds: 300,
      workspaces: [
        {
          org_id: "org_acme",
          display_name: "Acme Inc.",
          logo_url: null,
          role: "Admin",
          member_count: 12,
          last_active_at: null,
        },
      ],
    });
    const selectSpy = vi.spyOn(authApi, "selectWorkspace").mockResolvedValue({
      bearer_token: "atl.bearer",
      session_id: "sid_a",
      user_id: "usr_x",
      org_id: "org_acme",
      requires_mfa: false,
      expires_at: "2099-01-01T00:00:00Z",
    });
    Object.defineProperty(window, "location", {
      configurable: true,
      value: {
        assign: vi.fn(),
        origin: "http://localhost",
        pathname: "/auth/magic-link/callback",
        search: "?token=plain",
        href: "http://localhost/auth/magic-link/callback?token=plain",
      },
    });
    render(
      <AuthProvider persistBearer={false}>
        <LoginScreen />
      </AuthProvider>,
    );
    const row = await screen.findByTestId("login-pick-org_acme");
    await act(async () => {
      await userEvent.click(row);
    });
    await waitFor(() => {
      expect(selectSpy).toHaveBeenCalledWith({
        pick_token: "pick_xyz",
        org_id: "org_acme",
      });
    });
  });
});

describe("MfaPrompt", () => {
  beforeEach(() => {
    try {
      window.localStorage?.clear?.();
    } catch {
      /* no localStorage in this test env */
    }
    vi.restoreAllMocks();
  });

  it("renders the placeholder when no MFA is pending", async () => {
    vi.spyOn(authApi, "fetchCurrentSession").mockResolvedValue({
      identity: {
        org_id: "org_a",
        user_id: "usr_a",
        roles: ["employee"],
        permission_scopes: ["runtime:use"],
      },
    });
    render(
      <AuthProvider persistBearer={false}>
        <MfaPrompt />
      </AuthProvider>,
    );
    expect(await screen.findByTestId("mfa-prompt-idle")).toBeInTheDocument();
  });
});
