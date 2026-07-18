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
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider, useAuth } from "./AuthContext";
import { LoginScreen } from "./LoginScreen";
import { MfaPrompt } from "./MfaPrompt";
import * as authApi from "../../api/authApi";
import * as devIdpApi from "../../api/devIdpApi";
import {
  EIP6963_ANNOUNCE_EVENT,
  EIP6963_REQUEST_EVENT,
  type Eip1193RequestArguments,
} from "./eip6963";
import { UnauthorizedError } from "../../api/http";

/** Install a fake EIP-6963 wallet that announces on request. Mirrors the
 * helper in WalletSignIn.test.tsx so the login card's wallet picker can be
 * driven end-to-end. Returns a teardown. */
function installFakeWallet(): () => void {
  const request = vi.fn(
    async ({ method }: Eip1193RequestArguments): Promise<unknown> => {
      if (method === "eth_requestAccounts")
        return ["0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359"];
      if (method === "eth_chainId") return "0x1";
      throw new Error(`unexpected wallet method: ${method}`);
    },
  );
  const onRequest = (): void => {
    window.dispatchEvent(
      new CustomEvent(EIP6963_ANNOUNCE_EVENT, {
        detail: {
          info: {
            uuid: "u-test",
            name: "TestWallet",
            icon: "",
            rdns: "dev.testwallet",
          },
          provider: { request },
        },
      }),
    );
  };
  window.addEventListener(EIP6963_REQUEST_EVENT, onRequest);
  return () => window.removeEventListener(EIP6963_REQUEST_EVENT, onRequest);
}

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
      new UnauthorizedError("Missing bearer token"),
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
      new UnauthorizedError("Missing bearer token"),
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

  it("adoptSession() sets the bearer and refreshes into authenticated", async () => {
    // First probe (mount) 401s; the probe after adoption succeeds — the
    // SIWE verify handoff mints the bearer out-of-band.
    const probe = vi
      .spyOn(authApi, "fetchCurrentSession")
      .mockRejectedValueOnce(new UnauthorizedError("Missing bearer token"))
      .mockResolvedValue({
        identity: {
          org_id: "org_a",
          user_id: "usr_wallet",
          roles: ["employee"],
          permission_scopes: ["runtime:use"],
        },
      });

    function AdoptHarness(): ReactElement {
      const auth = useAuth();
      return (
        <>
          <StatusProbe />
          <button
            type="button"
            onClick={() =>
              void auth.adoptSession({
                bearer_token: "stub.wallet.bearer",
                session_id: "sid_w",
                user_id: "usr_wallet",
                requires_mfa: false,
              })
            }
          >
            adopt
          </button>
        </>
      );
    }
    render(
      <AuthProvider persistBearer={false}>
        <AdoptHarness />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId("status").textContent).toBe("anonymous");
    });
    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: "adopt" }));
    });
    await waitFor(() => {
      expect(screen.getByTestId("status").textContent).toBe(
        "authenticated:usr_wallet",
      );
    });
    expect(probe.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("adoptSession() with requires_mfa=true parks in mfa_pending", async () => {
    vi.spyOn(authApi, "fetchCurrentSession").mockRejectedValue(
      new UnauthorizedError("Missing bearer token"),
    );

    function AdoptHarness(): ReactElement {
      const auth = useAuth();
      return (
        <>
          <StatusProbe />
          <button
            type="button"
            onClick={() =>
              void auth.adoptSession({
                bearer_token: "stub.wallet.bearer",
                session_id: "sid_w",
                user_id: "usr_wallet",
                requires_mfa: true,
              })
            }
          >
            adopt
          </button>
        </>
      );
    }
    render(
      <AuthProvider persistBearer={false}>
        <AdoptHarness />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId("status").textContent).toBe("anonymous");
    });
    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: "adopt" }));
    });
    await waitFor(() => {
      expect(screen.getByTestId("status").textContent).toBe("mfa_pending");
    });
  });
});

describe("LoginScreen — v2 wallet-first pick view", () => {
  const teardowns: Array<() => void> = [];

  beforeEach(() => {
    try {
      window.localStorage?.clear?.();
    } catch {
      /* no localStorage in this test env */
    }
    vi.restoreAllMocks();
    // No Google advertised by default; each case that needs it overrides.
    vi.spyOn(authApi, "listAuthProviders").mockResolvedValue([]);
    vi.spyOn(authApi, "fetchCurrentSession").mockRejectedValue(
      new UnauthorizedError("Missing bearer token"),
    );
    // Anchor to "/" so initialStep resolves to the choose view (earlier
    // describes replace window.location with a magic-link-callback mock).
    Object.defineProperty(window, "location", {
      configurable: true,
      value: {
        assign: vi.fn(),
        origin: "http://localhost",
        pathname: "/",
        search: "",
        href: "http://localhost/",
      },
    });
  });

  afterEach(() => {
    while (teardowns.length > 0) {
      teardowns.pop()?.();
    }
  });

  it("renders the three options and never renders the email form", async () => {
    render(
      <AuthProvider persistBearer={false}>
        <LoginScreen />
      </AuthProvider>,
    );
    expect(await screen.findByTestId("login-screen")).toBeInTheDocument();
    expect(screen.getByTestId("login-option-wallet")).toBeInTheDocument();
    expect(screen.getByTestId("login-option-local")).toBeInTheDocument();
    expect(screen.getByText(/Welcome to/)).toBeInTheDocument();
    // Email is unplugged from the UI (code retained in emailLogin.tsx).
    expect(screen.queryByTestId("login-email-input")).toBeNull();
    expect(screen.queryByTestId("login-email-form")).toBeNull();
    expect(screen.queryByTestId("login-submit")).toBeNull();
    // Google hidden until advertised.
    expect(screen.queryByTestId("login-google")).toBeNull();
  });

  it("wallet option opens the EIP-6963-discovered picker", async () => {
    teardowns.push(installFakeWallet());
    render(
      <AuthProvider persistBearer={false}>
        <LoginScreen />
      </AuthProvider>,
    );
    await userEvent.click(await screen.findByTestId("login-option-wallet"));
    // The discovered wallet (announced over EIP-6963) shows up as a row.
    expect(
      await screen.findByTestId("wallet-provider-dev.testwallet"),
    ).toBeInTheDocument();
    expect(screen.getByText(/Choose a wallet/)).toBeInTheDocument();
  });

  it("wallet option shows the honest empty state when none discovered", async () => {
    render(
      <AuthProvider persistBearer={false}>
        <LoginScreen />
      </AuthProvider>,
    );
    await userEvent.click(await screen.findByTestId("login-option-wallet"));
    expect(await screen.findByTestId("wallet-empty")).toBeInTheDocument();
    expect(screen.getByText(/No wallet detected/)).toBeInTheDocument();
  });

  it("use-locally mints a dev-persona bearer via the local path", async () => {
    const mintSpy = vi.spyOn(devIdpApi, "mintDevBearer").mockResolvedValue({
      bearer: "dev.bearer",
      expires_at: "2099-01-01T00:00:00Z",
      persona_slug: "sarah_acme",
      identity: {
        org_id: "org_acme",
        user_id: "usr_local",
        display_name: "Sarah",
        primary_email: "sarah@acme.com",
        roles: ["employee"],
        permission_scopes: ["runtime:use"],
      },
    });
    render(
      <AuthProvider persistBearer={false}>
        <StatusProbe />
        <LoginScreen />
      </AuthProvider>,
    );
    const local = await screen.findByTestId("login-option-local");
    // Ignore any mount-time dev re-auth; assert the click itself drives it.
    mintSpy.mockClear();
    await act(async () => {
      await userEvent.click(local);
    });
    await waitFor(() => expect(mintSpy).toHaveBeenCalledWith("sarah_acme"));
  });

  it("renders the workspace picker rows when consume returns multiple workspaces", async () => {
    vi.spyOn(authApi, "fetchCurrentSession").mockRejectedValue(
      new UnauthorizedError("Missing bearer token"),
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
      new UnauthorizedError("Missing bearer token"),
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

describe("LoginScreen — Continue with Google", () => {
  const GOOGLE_PROVIDER = {
    provider_id: "google",
    kind: "oidc",
    display_name: "Google",
    enabled: true,
  } as const;

  beforeEach(() => {
    try {
      window.localStorage?.clear?.();
    } catch {
      /* no localStorage in this test env */
    }
    vi.restoreAllMocks();
    vi.spyOn(authApi, "fetchCurrentSession").mockRejectedValue(
      new UnauthorizedError("Missing bearer token"),
    );
    // Earlier describes replace window.location with a magic-link-callback
    // mock that leaks across tests in this file; anchor back to "/" so the
    // screen boots into the email step.
    Object.defineProperty(window, "location", {
      configurable: true,
      value: {
        assign: vi.fn(),
        origin: "http://localhost",
        pathname: "/",
        search: "",
        href: "http://localhost/",
      },
    });
  });

  it("renders the option when the providers list advertises google", async () => {
    vi.spyOn(authApi, "listAuthProviders").mockResolvedValue([GOOGLE_PROVIDER]);
    render(
      <AuthProvider persistBearer={false}>
        <LoginScreen />
      </AuthProvider>,
    );
    expect(await screen.findByTestId("login-google")).toBeInTheDocument();
    expect(screen.getByText("Continue with Google")).toBeInTheDocument();
    // The wallet-first options stay alongside the Google entry point.
    expect(screen.getByTestId("login-option-wallet")).toBeInTheDocument();
    expect(screen.getByTestId("login-option-local")).toBeInTheDocument();
    // Email is unplugged from the UI regardless of Google availability.
    expect(screen.queryByTestId("login-email-input")).toBeNull();
  });

  it("stays hidden when the providers list has no google entry", async () => {
    const providersSpy = vi
      .spyOn(authApi, "listAuthProviders")
      .mockResolvedValue([
        {
          provider_id: "prv_okta",
          kind: "oidc",
          display_name: "Okta",
          enabled: true,
        },
      ]);
    render(
      <AuthProvider persistBearer={false}>
        <LoginScreen />
      </AuthProvider>,
    );
    await waitFor(() => expect(providersSpy).toHaveBeenCalled());
    await act(async () => {
      /* flush the resolved providers promise */
    });
    expect(screen.getByTestId("login-option-wallet")).toBeInTheDocument();
    expect(screen.queryByTestId("login-google")).toBeNull();
  });

  it("stays hidden when the google entry is disabled", async () => {
    const providersSpy = vi
      .spyOn(authApi, "listAuthProviders")
      .mockResolvedValue([{ ...GOOGLE_PROVIDER, enabled: false }]);
    render(
      <AuthProvider persistBearer={false}>
        <LoginScreen />
      </AuthProvider>,
    );
    await waitFor(() => expect(providersSpy).toHaveBeenCalled());
    await act(async () => {
      /* flush the resolved providers promise */
    });
    expect(screen.queryByTestId("login-google")).toBeNull();
  });

  it("degrades silently to no button when the providers fetch fails", async () => {
    const providersSpy = vi
      .spyOn(authApi, "listAuthProviders")
      .mockRejectedValue(new Error("providers endpoint unavailable"));
    render(
      <AuthProvider persistBearer={false}>
        <LoginScreen />
      </AuthProvider>,
    );
    await waitFor(() => expect(providersSpy).toHaveBeenCalled());
    await act(async () => {
      /* flush the rejected providers promise */
    });
    expect(screen.getByTestId("login-option-wallet")).toBeInTheDocument();
    expect(screen.queryByTestId("login-google")).toBeNull();
  });

  it("click navigates to the google OIDC start URL with return_to", async () => {
    vi.spyOn(authApi, "listAuthProviders").mockResolvedValue([GOOGLE_PROVIDER]);
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
        <LoginScreen returnTo="/inbox" />
      </AuthProvider>,
    );
    const button = await screen.findByTestId("login-google");
    await act(async () => {
      await userEvent.click(button);
    });
    expect(assignSpy).toHaveBeenCalledTimes(1);
    const url = String(assignSpy.mock.calls[0]?.[0] ?? "");
    expect(url).toContain("/v1/auth/oidc/google/start");
    expect(url).toContain(
      `redirect_uri=${encodeURIComponent("http://localhost/v1/auth/oidc/callback")}`,
    );
    expect(url).toContain("return_to=%2Finbox");
    // No org is known pre-auth on the Google path.
    expect(url).not.toContain("org_id=");
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
