// FR-5.24 — Developer tokens. Create shows the secret ONCE; the list shows
// name + masked prefix + last-used + Revoke; create/revoke route through the
// injected port. Loading / empty / error states are honest.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ApiKeySummary,
  CreateApiKeyResponse,
} from "@0x-copilot/api-types";

import { DeveloperTokensPage } from "./DeveloperTokensPage";
import type { DeveloperTokensPort } from "./data/developerTokens";

function summary(overrides: Partial<ApiKeySummary> = {}): ApiKeySummary {
  return {
    id: "tok_1",
    label: "laptop CLI",
    key_prefix: "atlas_pk_abcd",
    scopes: [],
    last_used_at: null,
    created_at: "2026-07-01T10:00:00Z",
    rotated_from_id: null,
    kind: "personal",
    ...overrides,
  };
}

function makePort(
  overrides: Partial<DeveloperTokensPort> = {},
): DeveloperTokensPort {
  return {
    list: vi.fn<DeveloperTokensPort["list"]>().mockResolvedValue([]),
    create: vi.fn<DeveloperTokensPort["create"]>().mockImplementation(
      async (label: string): Promise<CreateApiKeyResponse> => ({
        key: summary({ id: "tok_new", label }),
        plaintext: "atlas_pk_super_secret_value",
      }),
    ),
    revoke: vi.fn<DeveloperTokensPort["revoke"]>().mockResolvedValue(undefined),
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("<DeveloperTokensPage>", () => {
  it("shows the empty state and the shown-once note", async () => {
    render(<DeveloperTokensPage port={makePort()} />);
    await screen.findByTestId("developer-tokens-empty");
    expect(
      screen.getByTestId("developer-tokens-once-note"),
    ).toBeInTheDocument();
  });

  it("lists a stored token with masked prefix, last-used, and Revoke", async () => {
    const port = makePort({
      list: vi
        .fn()
        .mockResolvedValue([summary({ key_prefix: "atlas_pk_zzzz" })]),
    });
    render(<DeveloperTokensPage port={port} />);
    await screen.findByTestId("developer-token-row-tok_1");
    expect(screen.getByText(/atlas_pk_zzzz…/)).toBeInTheDocument();
    expect(screen.getByText(/Never used/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /revoke laptop cli/i }),
    ).toBeInTheDocument();
  });

  it("creates a token, reveals the secret once, and toasts", async () => {
    const port = makePort();
    const onToast = vi.fn();
    render(<DeveloperTokensPage port={port} onToast={onToast} />);
    await screen.findByTestId("developer-tokens-empty");

    fireEvent.change(screen.getByTestId("developer-tokens-label"), {
      target: { value: "ci runner" },
    });
    fireEvent.click(screen.getByTestId("developer-tokens-create"));

    await waitFor(() => expect(port.create).toHaveBeenCalledWith("ci runner"));
    // Secret revealed exactly once.
    expect(screen.getByTestId("developer-tokens-secret")).toHaveTextContent(
      "atlas_pk_super_secret_value",
    );
    expect(onToast).toHaveBeenCalledWith("Created “ci runner”.");

    // Dismissing the reveal drops the plaintext from the DOM.
    fireEvent.click(screen.getByTestId("developer-tokens-reveal-done"));
    expect(screen.queryByTestId("developer-tokens-secret")).toBeNull();
    // The new token stays in the list.
    expect(
      screen.getByTestId("developer-token-row-tok_new"),
    ).toBeInTheDocument();
  });

  it("blocks an empty create with an inline error and never calls the port", () => {
    const port = makePort();
    render(<DeveloperTokensPage port={port} />);
    fireEvent.click(screen.getByTestId("developer-tokens-create"));
    expect(screen.getByTestId("developer-tokens-create-error")).toHaveAttribute(
      "role",
      "alert",
    );
    expect(port.create).not.toHaveBeenCalled();
  });

  it("revokes a token via the port and drops the row", async () => {
    const port = makePort({
      list: vi.fn().mockResolvedValue([summary()]),
    });
    const onToast = vi.fn();
    render(<DeveloperTokensPage port={port} onToast={onToast} />);

    fireEvent.click(await screen.findByTestId("developer-token-revoke-tok_1"));
    await waitFor(() => expect(port.revoke).toHaveBeenCalledWith("tok_1"));
    await waitFor(() =>
      expect(screen.queryByTestId("developer-token-row-tok_1")).toBeNull(),
    );
    expect(onToast).toHaveBeenCalledWith("Revoked “laptop CLI”.");
  });

  it("surfaces a load error with a Retry that re-lists", async () => {
    const list = vi
      .fn<DeveloperTokensPort["list"]>()
      .mockRejectedValueOnce(new Error("tokens unavailable"))
      .mockResolvedValue([]);
    render(<DeveloperTokensPage port={makePort({ list })} />);

    const alert = await screen.findByTestId("developer-tokens-error");
    expect(alert).toHaveAttribute("role", "alert");
    expect(alert).toHaveTextContent("tokens unavailable");

    fireEvent.click(screen.getByTestId("developer-tokens-retry"));
    await screen.findByTestId("developer-tokens-empty");
    expect(list).toHaveBeenCalledTimes(2);
  });
});
