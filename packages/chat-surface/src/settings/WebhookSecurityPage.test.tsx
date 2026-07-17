import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { WebhookSecurityDefaults } from "@0x-copilot/api-types";

import {
  WebhookSecurityPage,
  clampMaxSecretAgeDays,
  MAX_SECRET_AGE_DAY_VALUES,
} from "./WebhookSecurityPage";

const DEFAULTS: WebhookSecurityDefaults = {
  default_hmac_on: true,
  require_ip_allowlist: false,
  max_secret_age_days: 90,
  updated_at: "2026-05-18T00:00:00Z",
  updated_by_user_id: null,
};

describe("clampMaxSecretAgeDays", () => {
  it("clamps an arbitrary day count to the nearest allowed value", () => {
    expect(clampMaxSecretAgeDays(0)).toBe(MAX_SECRET_AGE_DAY_VALUES[0]);
    expect(clampMaxSecretAgeDays(45)).toBe(30);
    expect(clampMaxSecretAgeDays(46)).toBe(60);
    expect(clampMaxSecretAgeDays(120)).toBe(90);
    expect(clampMaxSecretAgeDays(10_000)).toBe(365);
  });

  it("returns an allowed value unchanged", () => {
    for (const v of MAX_SECRET_AGE_DAY_VALUES) {
      expect(clampMaxSecretAgeDays(v)).toBe(v);
    }
  });
});

describe("<WebhookSecurityPage>", () => {
  it("renders nothing for non-admin callers", () => {
    const { container } = render(
      <WebhookSecurityPage
        defaults={DEFAULTS}
        isAdmin={false}
        onSave={() => undefined}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders fieldsets with legends (ARIA)", () => {
    render(
      <WebhookSecurityPage
        defaults={DEFAULTS}
        isAdmin={true}
        onSave={() => undefined}
      />,
    );
    expect(
      screen.getByRole("group", { name: "Webhook signing" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("group", { name: "Secret rotation" }),
    ).toBeInTheDocument();
  });

  it("clamps an out-of-range max_secret_age_days from the wire", () => {
    render(
      <WebhookSecurityPage
        defaults={{ ...DEFAULTS, max_secret_age_days: 200 }}
        isAdmin={true}
        onSave={() => undefined}
      />,
    );
    // 200 is closer to 180 than 365.
    expect(screen.getByTestId("webhook-security-age-value")).toHaveTextContent(
      "180 days",
    );
  });

  it("PATCH only carries changed fields", () => {
    const onSave = vi.fn();
    render(
      <WebhookSecurityPage
        defaults={DEFAULTS}
        isAdmin={true}
        onSave={onSave}
      />,
    );
    // Flip require_ip_allowlist on.
    fireEvent.click(screen.getByTestId("webhook-security-require-ip"));
    fireEvent.click(screen.getByTestId("webhook-security-save"));
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave.mock.calls[0]?.[0]).toEqual({ require_ip_allowlist: true });
  });

  it("Save with no changes does not fire onSave", () => {
    const onSave = vi.fn();
    render(
      <WebhookSecurityPage
        defaults={DEFAULTS}
        isAdmin={true}
        onSave={onSave}
      />,
    );
    fireEvent.click(screen.getByTestId("webhook-security-save"));
    expect(onSave).not.toHaveBeenCalled();
  });

  it("renders the host-provided HMAC algo label", () => {
    render(
      <WebhookSecurityPage
        defaults={DEFAULTS}
        isAdmin={true}
        onSave={() => undefined}
        hmacAlgo="hmac-sha512"
      />,
    );
    expect(screen.getByText(/HMAC \(hmac-sha512\)/)).toBeInTheDocument();
  });
});
