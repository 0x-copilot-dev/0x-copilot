import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TenantId, TriggerId, Webhook } from "@0x-copilot/api-types";

const webhooksApiMocks = vi.hoisted(() => ({
  fetchWebhooks: vi.fn(),
  fetchWebhook: vi.fn(),
  createWebhook: vi.fn(),
  patchWebhook: vi.fn(),
  deleteWebhook: vi.fn(),
  rotateWebhookSecret: vi.fn(),
  testFireWebhook: vi.fn(),
}));
vi.mock("../../../api/webhooksApi", async () => {
  const actual = await vi.importActual<
    typeof import("../../../api/webhooksApi")
  >("../../../api/webhooksApi");
  return {
    ...actual,
    fetchWebhooks: webhooksApiMocks.fetchWebhooks,
    fetchWebhook: webhooksApiMocks.fetchWebhook,
    createWebhook: webhooksApiMocks.createWebhook,
    patchWebhook: webhooksApiMocks.patchWebhook,
    deleteWebhook: webhooksApiMocks.deleteWebhook,
    rotateWebhookSecret: webhooksApiMocks.rotateWebhookSecret,
    testFireWebhook: webhooksApiMocks.testFireWebhook,
  };
});

import { WebhooksRoute } from "../WebhooksRoute";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

function webhook(overrides: Partial<Webhook> = {}): Webhook {
  return {
    id: "wh_1" as TriggerId,
    tenant_id: "tenant_1" as TenantId,
    url: "https://example.com/atlas-hook",
    secret_strategy: "rotating",
    hmac_algo: "hmac-sha256",
    ip_allowlist: [],
    status: "active",
    last_fire_at: null,
    created_at: "2026-05-01T00:00:00Z",
    rotates_at: null,
    ...overrides,
  };
}

describe("WebhooksRoute", () => {
  beforeEach(() => {
    webhooksApiMocks.fetchWebhooks.mockReset();
    webhooksApiMocks.createWebhook.mockReset();
    webhooksApiMocks.rotateWebhookSecret.mockReset();
    webhooksApiMocks.deleteWebhook.mockReset();
    webhooksApiMocks.testFireWebhook.mockReset();
    webhooksApiMocks.patchWebhook.mockReset();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the empty state when there are no webhooks", async () => {
    webhooksApiMocks.fetchWebhooks.mockResolvedValueOnce({
      items: [],
      next_cursor: null,
    });

    render(<WebhooksRoute identity={IDENTITY} onClose={() => undefined} />);

    await waitFor(() => {
      expect(screen.getByTestId("webhooks-route-empty")).toBeInTheDocument();
    });
  });

  it("creates a webhook, reveals the secret once, then drops the secret on dismiss; subsequent reads NEVER carry plaintext", async () => {
    webhooksApiMocks.fetchWebhooks.mockResolvedValueOnce({
      items: [],
      next_cursor: null,
    });
    webhooksApiMocks.createWebhook.mockResolvedValueOnce({
      webhook: webhook({ id: "wh_new" as TriggerId }),
      secret_plaintext: "PLAINTEXT_S3CRET",
    });
    // Subsequent fetches (after the secret was revealed) come back
    // WITHOUT a plaintext channel. The wire shape is the redacted
    // `Webhook` only. We assert below that nothing in the rendered UI
    // ever surfaces the plaintext after dismissal.
    webhooksApiMocks.fetchWebhooks.mockResolvedValueOnce({
      items: [webhook({ id: "wh_new" as TriggerId })],
      next_cursor: null,
    });

    render(<WebhooksRoute identity={IDENTITY} onClose={() => undefined} />);

    await waitFor(() => {
      expect(screen.getByTestId("webhooks-route-add")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("webhooks-route-add"));

    // Submit the wizard
    const wizard = screen.getByTestId("webhook-create-wizard");
    fireEvent.change(within(wizard).getByTestId("webhook-create-wizard-url"), {
      target: { value: "https://example.com/atlas-hook" },
    });
    fireEvent.click(within(wizard).getByTestId("webhook-create-wizard-submit"));

    await waitFor(() => {
      expect(webhooksApiMocks.createWebhook).toHaveBeenCalledWith(IDENTITY, {
        url: "https://example.com/atlas-hook",
        secret_strategy: "rotating",
      });
    });

    // Reveal payload visible — secret string in the DOM
    await waitFor(() => {
      expect(
        screen.getByTestId("webhook-create-wizard-reveal"),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("webhook-create-wizard-secret-reveal-clear"),
    ).toHaveTextContent("PLAINTEXT_S3CRET");

    // Dismiss the reveal banner — secret must be dropped.
    fireEvent.click(
      screen.getByTestId("webhook-create-wizard-secret-reveal-dismiss"),
    );

    // After dismiss the reveal block is unmounted (the wizard returns
    // to the form view). The clear-text node is gone and there is no
    // RevealOnce instance left.
    await waitFor(() => {
      expect(
        screen.queryByTestId("webhook-create-wizard-secret-reveal-clear"),
      ).not.toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("webhook-create-wizard-reveal"),
    ).not.toBeInTheDocument();

    // Belt-and-braces: the plaintext string must not be anywhere in
    // the DOM after the dismiss; not in any data- attribute either.
    expect(document.body.innerHTML).not.toContain("PLAINTEXT_S3CRET");

    // The plaintext MUST NOT be persisted anywhere outside component
    // state. Verify that no Storage entries were touched.
    const localStorage = window.localStorage;
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key === null) continue;
      const value = localStorage.getItem(key);
      expect(value).not.toContain("PLAINTEXT_S3CRET");
    }
    const sessionStorage = window.sessionStorage;
    for (let i = 0; i < sessionStorage.length; i++) {
      const key = sessionStorage.key(i);
      if (key === null) continue;
      const value = sessionStorage.getItem(key);
      expect(value).not.toContain("PLAINTEXT_S3CRET");
    }

    // The wizard fell back to the form view after dismiss. Close via
    // the Cancel button — the wizardOpen state flips to false and the
    // cleanup effect re-clears the reveal state (belt-and-braces).
    fireEvent.click(screen.getByTestId("webhook-create-wizard-cancel"));
    await waitFor(() => {
      expect(
        screen.queryByTestId("webhook-create-wizard"),
      ).not.toBeInTheDocument();
    });
    expect(document.body.innerHTML).not.toContain("PLAINTEXT_S3CRET");
  });

  it("clears the reveal payload when the wizard unmounts", async () => {
    webhooksApiMocks.fetchWebhooks.mockResolvedValueOnce({
      items: [],
      next_cursor: null,
    });
    webhooksApiMocks.createWebhook.mockResolvedValueOnce({
      webhook: webhook({ id: "wh_new" as TriggerId }),
      secret_plaintext: "ANOTHER_SECRET",
    });

    render(<WebhooksRoute identity={IDENTITY} onClose={() => undefined} />);

    await waitFor(() => {
      expect(screen.getByTestId("webhooks-route-add")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("webhooks-route-add"));
    const wizard = screen.getByTestId("webhook-create-wizard");
    fireEvent.change(within(wizard).getByTestId("webhook-create-wizard-url"), {
      target: { value: "https://example.com/x" },
    });
    fireEvent.click(within(wizard).getByTestId("webhook-create-wizard-submit"));

    await waitFor(() => {
      expect(
        screen.getByTestId("webhook-create-wizard-reveal"),
      ).toBeInTheDocument();
    });

    // Without dismissing first, close the wizard. The unmount path
    // MUST also clear the secret from state.
    fireEvent.click(screen.getByTestId("webhook-create-wizard-close"));

    await waitFor(() => {
      expect(
        screen.queryByTestId("webhook-create-wizard"),
      ).not.toBeInTheDocument();
    });
    expect(document.body.innerHTML).not.toContain("ANOTHER_SECRET");
  });

  it("rotate produces a fresh reveal payload (with a grace secret when one exists)", async () => {
    webhooksApiMocks.fetchWebhooks.mockResolvedValueOnce({
      items: [webhook({ id: "wh_old" as TriggerId })],
      next_cursor: null,
    });
    webhooksApiMocks.rotateWebhookSecret.mockResolvedValueOnce({
      webhook: webhook({ id: "wh_old" as TriggerId }),
      secret_plaintext: "NEW_SECRET",
      grace_secret_plaintext: "OLD_SECRET",
    });

    render(<WebhooksRoute identity={IDENTITY} onClose={() => undefined} />);

    await waitFor(() => {
      expect(screen.getByTestId("webhooks-route-row")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("webhooks-route-rotate"));

    await waitFor(() => {
      expect(
        screen.getByTestId("webhook-create-wizard-secret-reveal-clear"),
      ).toHaveTextContent("NEW_SECRET");
    });
    expect(
      screen.getByTestId("webhook-create-wizard-grace-reveal-clear"),
    ).toHaveTextContent("OLD_SECRET");
  });

  it("surfaces a pending-error banner on a failed create", async () => {
    webhooksApiMocks.fetchWebhooks.mockResolvedValueOnce({
      items: [],
      next_cursor: null,
    });
    webhooksApiMocks.createWebhook.mockRejectedValueOnce(new Error("nope"));

    render(<WebhooksRoute identity={IDENTITY} onClose={() => undefined} />);

    await waitFor(() => {
      expect(screen.getByTestId("webhooks-route-add")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("webhooks-route-add"));
    const wizard = screen.getByTestId("webhook-create-wizard");
    fireEvent.change(within(wizard).getByTestId("webhook-create-wizard-url"), {
      target: { value: "https://example.com/atlas-hook" },
    });
    fireEvent.click(within(wizard).getByTestId("webhook-create-wizard-submit"));

    await waitFor(() => {
      expect(
        screen.getByTestId("webhooks-route-pending-error"),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("webhooks-route-pending-error").textContent,
    ).toContain("nope");
  });
});
