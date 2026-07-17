// WebhookDetailView — rotate triggers RevealOnce on secret + grace secret.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { TenantId, TriggerId } from "@0x-copilot/api-types";
import type { Webhook, WebhookRotateResponse } from "@0x-copilot/api-types";

import { WebhookDetailView } from "./WebhookDetailView";

const NOW = Date.parse("2026-05-17T12:00:00.000Z");

function makeWebhook(): Webhook {
  return {
    id: "wh_1" as TriggerId,
    tenant_id: "tnt_1" as TenantId,
    url: "https://example.com/hook",
    secret_strategy: "rotating",
    hmac_algo: "hmac-sha256",
    ip_allowlist: ["10.0.0.0/8"],
    status: "active",
    last_fire_at: "2026-05-17T11:00:00.000Z",
    last_status_code: 200,
    created_at: "2026-05-17T10:00:00.000Z",
    rotates_at: "2026-08-15T10:00:00.000Z",
  };
}

function makeRotateResponse(
  graceSecret: string | null = null,
): WebhookRotateResponse {
  return {
    webhook: makeWebhook(),
    secret_plaintext: "wh_new_secret_value",
    grace_secret_plaintext: graceSecret,
  };
}

describe("WebhookDetailView", () => {
  it("renders the URL, status pill, and lifecycle buttons", () => {
    render(
      <WebhookDetailView
        webhook={makeWebhook()}
        onCopy={async () => {}}
        onPauseToggle={() => {}}
        onDelete={() => {}}
        now={NOW}
      />,
    );
    expect(screen.getByTestId("webhook-detail-url")).toHaveTextContent(
      "https://example.com/hook",
    );
    expect(screen.getByTestId("webhook-detail-pause-toggle")).toHaveTextContent(
      "Pause",
    );
    expect(screen.getByTestId("webhook-detail-delete")).toBeInTheDocument();
  });

  it("rotate triggers RevealOnce on the new plaintext", async () => {
    const onRotate = vi.fn(async () => makeRotateResponse(null));
    render(
      <WebhookDetailView
        webhook={makeWebhook()}
        onCopy={async () => {}}
        onRotate={onRotate}
        now={NOW}
      />,
    );

    // Before rotate: secret reveal is inactive (masked placeholder).
    expect(
      screen.getByTestId("webhook-detail-secret-masked"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("webhook-detail-secret-clear"),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("webhook-detail-rotate"));

    await waitFor(() => {
      expect(onRotate).toHaveBeenCalledTimes(1);
    });
    const clear = await screen.findByTestId("webhook-detail-secret-clear");
    expect(clear).toHaveTextContent("wh_new_secret_value");
  });

  it("rotate reveals the grace secret when present", async () => {
    const onRotate = vi.fn(async () =>
      makeRotateResponse("wh_grace_secret_value"),
    );
    render(
      <WebhookDetailView
        webhook={makeWebhook()}
        onCopy={async () => {}}
        onRotate={onRotate}
        now={NOW}
      />,
    );
    fireEvent.click(screen.getByTestId("webhook-detail-rotate"));
    await waitFor(() => {
      expect(onRotate).toHaveBeenCalledTimes(1);
    });
    expect(
      await screen.findByTestId("webhook-detail-grace"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("webhook-detail-grace-secret-clear"),
    ).toHaveTextContent("wh_grace_secret_value");
  });

  it("rotate omits the grace block when grace_secret_plaintext is null", async () => {
    const onRotate = vi.fn(async () => makeRotateResponse(null));
    render(
      <WebhookDetailView
        webhook={makeWebhook()}
        onCopy={async () => {}}
        onRotate={onRotate}
        now={NOW}
      />,
    );
    fireEvent.click(screen.getByTestId("webhook-detail-rotate"));
    await waitFor(() => {
      expect(onRotate).toHaveBeenCalledTimes(1);
    });
    await screen.findByTestId("webhook-detail-secret-clear");
    expect(
      screen.queryByTestId("webhook-detail-grace"),
    ).not.toBeInTheDocument();
  });

  it("test-fire renders the result envelope", async () => {
    render(
      <WebhookDetailView
        webhook={makeWebhook()}
        onCopy={async () => {}}
        onTestFire={async () => ({
          response_status: 200,
          response_ok: true,
        })}
        now={NOW}
      />,
    );
    fireEvent.click(screen.getByTestId("webhook-detail-test-fire"));
    const result = await screen.findByTestId("webhook-detail-test-fire-result");
    expect(result).toHaveAttribute("data-response-ok", "true");
  });
});
