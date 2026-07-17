// WebhookCreateWizard — 5-step navigation, copy-once reveal, §9.4 snippet.
//
// The §9.4 verification snippet must appear verbatim in the rendered
// output. The snippet text is exported as `WEBHOOK_VERIFICATION_SNIPPET`
// and the test compares against a hard-coded duplicate (taken from the
// sub-PRD §9.4) so this test fails the moment either copy drifts.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { TenantId, TriggerId } from "@0x-copilot/api-types";
import type { Webhook, WebhookCreateResponse } from "@0x-copilot/api-types";

import {
  WEBHOOK_VERIFICATION_SNIPPET,
  WebhookCreateWizard,
} from "./WebhookCreateWizard";

// The canonical Python snippet from connectors-prd §9.4. Hard-coded
// here so the test fails if either side drifts.
const CANONICAL_SNIPPET = `# Receiver-side verification (Python)
import hmac, hashlib, time
def verify(body: bytes, sig_header: str, ts_header: str, secret: bytes) -> bool:
    if abs(time.time() - int(ts_header)) > 300:
        return False
    algo, signature = sig_header.split("=", 1)
    if algo != "hmac-sha256":
        return False
    expected = hmac.new(secret, body + ts_header.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)`;

function makeWebhook(): Webhook {
  return {
    id: "wh_1" as TriggerId,
    tenant_id: "tnt_1" as TenantId,
    url: "https://example.com/hook",
    secret_strategy: "rotating",
    hmac_algo: "hmac-sha256",
    ip_allowlist: [],
    status: "active",
    last_fire_at: null,
    created_at: "2026-05-17T12:00:00.000Z",
    rotates_at: "2026-08-15T12:00:00.000Z",
  };
}

function makeCreateResponse(
  secret = "wh_supersecret123",
): WebhookCreateResponse {
  return {
    webhook: makeWebhook(),
    secret_plaintext: secret,
  };
}

describe("WebhookCreateWizard", () => {
  it("renders the URL step first with five steps in the stepper", () => {
    render(
      <WebhookCreateWizard
        onCreate={async () => null}
        onCopy={async () => {}}
      />,
    );
    expect(
      screen.getByRole("region", { name: /Add webhook/ }),
    ).toBeInTheDocument();
    for (let i = 0; i < 5; i++) {
      expect(
        screen.getByTestId(`webhook-wizard-step-${i}`),
      ).toBeInTheDocument();
    }
    expect(screen.getByTestId("webhook-wizard-url-step")).toBeInTheDocument();
  });

  it("blocks Next until a valid URL is typed", () => {
    render(
      <WebhookCreateWizard
        onCreate={async () => null}
        onCopy={async () => {}}
      />,
    );
    expect(screen.getByTestId("webhook-wizard-next")).toBeDisabled();
    fireEvent.change(screen.getByTestId("webhook-wizard-url-input"), {
      target: { value: "https://example.com/hook" },
    });
    expect(screen.getByTestId("webhook-wizard-next")).not.toBeDisabled();
  });

  it("advances through all 5 steps to the test-fire step", () => {
    render(
      <WebhookCreateWizard
        onCreate={async () => null}
        onCopy={async () => {}}
      />,
    );
    fireEvent.change(screen.getByTestId("webhook-wizard-url-input"), {
      target: { value: "https://example.com/hook" },
    });
    fireEvent.click(screen.getByTestId("webhook-wizard-next")); // -> secret strategy
    expect(
      screen.getByTestId("webhook-wizard-secret-strategy"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("webhook-wizard-next")); // -> hmac
    expect(screen.getByTestId("webhook-wizard-hmac-step")).toBeInTheDocument();
    expect(screen.getByTestId("webhook-wizard-hmac-algo")).toHaveTextContent(
      "hmac-sha256",
    );
    fireEvent.click(screen.getByTestId("webhook-wizard-next")); // -> ip allowlist
    expect(screen.getByTestId("webhook-wizard-ip-step")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("webhook-wizard-next")); // -> test fire
    expect(
      screen.getByTestId("webhook-wizard-test-fire-step"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("webhook-wizard-create")).toBeInTheDocument();
  });

  it("renders the §9.4 verification snippet verbatim after create", async () => {
    expect(WEBHOOK_VERIFICATION_SNIPPET).toBe(CANONICAL_SNIPPET);

    const onCreate = vi.fn(async () => makeCreateResponse());
    render(<WebhookCreateWizard onCreate={onCreate} onCopy={async () => {}} />);
    fireEvent.change(screen.getByTestId("webhook-wizard-url-input"), {
      target: { value: "https://example.com/hook" },
    });
    fireEvent.click(screen.getByTestId("webhook-wizard-next"));
    fireEvent.click(screen.getByTestId("webhook-wizard-next"));
    fireEvent.click(screen.getByTestId("webhook-wizard-next"));
    fireEvent.click(screen.getByTestId("webhook-wizard-next"));
    fireEvent.click(screen.getByTestId("webhook-wizard-create"));

    await waitFor(() => {
      expect(onCreate).toHaveBeenCalledTimes(1);
    });
    const snippet = await screen.findByTestId(
      "webhook-wizard-verification-snippet",
    );
    expect(snippet.textContent).toBe(CANONICAL_SNIPPET);
  });

  it("reveals the secret exactly once and dismisses it on the dismiss CTA", async () => {
    const onCreate = vi.fn(async () => makeCreateResponse("wh_topsecret"));
    render(<WebhookCreateWizard onCreate={onCreate} onCopy={async () => {}} />);
    fireEvent.change(screen.getByTestId("webhook-wizard-url-input"), {
      target: { value: "https://example.com/hook" },
    });
    fireEvent.click(screen.getByTestId("webhook-wizard-next"));
    fireEvent.click(screen.getByTestId("webhook-wizard-next"));
    fireEvent.click(screen.getByTestId("webhook-wizard-next"));
    fireEvent.click(screen.getByTestId("webhook-wizard-next"));
    fireEvent.click(screen.getByTestId("webhook-wizard-create"));

    const clear = await screen.findByTestId("webhook-wizard-secret-clear");
    expect(clear).toHaveTextContent("wh_topsecret");

    fireEvent.click(screen.getByTestId("webhook-wizard-secret-dismiss"));
    // After dismiss the masked placeholder is shown — the plaintext is
    // no longer visible.
    await waitFor(() => {
      expect(
        screen.getByTestId("webhook-wizard-secret-masked"),
      ).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("webhook-wizard-secret-clear"),
    ).not.toBeInTheDocument();
  });

  it("Back button returns to the previous step", () => {
    render(
      <WebhookCreateWizard
        onCreate={async () => null}
        onCopy={async () => {}}
      />,
    );
    fireEvent.change(screen.getByTestId("webhook-wizard-url-input"), {
      target: { value: "https://example.com/hook" },
    });
    fireEvent.click(screen.getByTestId("webhook-wizard-next"));
    expect(
      screen.getByTestId("webhook-wizard-secret-strategy"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("webhook-wizard-back"));
    expect(screen.getByTestId("webhook-wizard-url-step")).toBeInTheDocument();
  });
});
