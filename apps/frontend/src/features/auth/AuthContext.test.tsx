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

describe("LoginScreen", () => {
  beforeEach(() => {
    try {
      window.localStorage?.clear?.();
    } catch {
      /* no localStorage in this test env */
    }
    vi.restoreAllMocks();
  });

  it("renders OIDC buttons + the local-password form when both are enabled", async () => {
    vi.spyOn(authApi, "fetchCurrentSession").mockRejectedValue(
      new Error("Request failed with 401"),
    );
    vi.spyOn(authApi, "listAuthProviders").mockResolvedValue([
      {
        provider_id: "google",
        kind: "oidc",
        display_name: "Google",
        enabled: true,
      },
      {
        provider_id: "local-default",
        kind: "local",
        display_name: "Email + password",
        enabled: true,
      },
    ]);
    render(
      <AuthProvider persistBearer={false}>
        <LoginScreen defaultOrgId="org_a" />
      </AuthProvider>,
    );
    expect(await screen.findByText("Continue with Google")).toBeInTheDocument();
    expect(screen.getByTestId("login-form")).toBeInTheDocument();
  });

  it("hides the local-password form when local provider is disabled", async () => {
    vi.spyOn(authApi, "fetchCurrentSession").mockRejectedValue(
      new Error("Request failed with 401"),
    );
    vi.spyOn(authApi, "listAuthProviders").mockResolvedValue([
      {
        provider_id: "okta",
        kind: "oidc",
        display_name: "Okta",
        enabled: true,
      },
    ]);
    render(
      <AuthProvider persistBearer={false}>
        <LoginScreen defaultOrgId="org_a" />
      </AuthProvider>,
    );
    expect(await screen.findByText("Continue with Okta")).toBeInTheDocument();
    expect(screen.queryByTestId("login-form")).not.toBeInTheDocument();
  });

  it("shows the local form when the providers endpoint returns nothing (dev fallback)", async () => {
    vi.spyOn(authApi, "fetchCurrentSession").mockRejectedValue(
      new Error("Request failed with 401"),
    );
    vi.spyOn(authApi, "listAuthProviders").mockResolvedValue([]);
    render(
      <AuthProvider persistBearer={false}>
        <LoginScreen defaultOrgId="org_a" />
      </AuthProvider>,
    );
    expect(await screen.findByTestId("login-form")).toBeInTheDocument();
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
