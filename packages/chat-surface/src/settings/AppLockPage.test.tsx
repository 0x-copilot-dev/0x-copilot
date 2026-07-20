// FR-5.23 / US-5.8 — Key storage & app lock. The keychain note, the
// encrypt-history / Touch-ID / lock-after controls, and the disabled-with-hint
// Touch-ID behaviour when the platform can't provide it.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  APP_LOCK_KEYCHAIN_NOTE,
  AppLockPage,
  type AppLockValue,
} from "./AppLockPage";

const BASE: AppLockValue = {
  encryptHistory: false,
  requireTouchId: false,
  lockAfter: "15m",
};

describe("<AppLockPage>", () => {
  it("shows the keychain note and all three controls", () => {
    render(<AppLockPage value={BASE} onChange={vi.fn()} />);
    expect(screen.getByText(APP_LOCK_KEYCHAIN_NOTE)).toBeInTheDocument();
    expect(screen.getByTestId("app-lock-encrypt-history")).toBeInTheDocument();
    expect(screen.getByTestId("app-lock-require-touch-id")).toBeInTheDocument();
    const lockAfter = screen.getByTestId(
      "app-lock-lock-after",
    ) as HTMLSelectElement;
    expect(lockAfter.value).toBe("15m");
  });

  it("reports edits through onChange (encrypt + lock-after)", () => {
    const onChange = vi.fn();
    render(<AppLockPage value={BASE} onChange={onChange} />);

    fireEvent.click(screen.getByTestId("app-lock-encrypt-history"));
    expect(onChange).toHaveBeenCalledWith({ encryptHistory: true });

    fireEvent.change(screen.getByTestId("app-lock-lock-after"), {
      target: { value: "never" },
    });
    expect(onChange).toHaveBeenCalledWith({ lockAfter: "never" });
  });

  it("enables the Touch-ID toggle and reports it when supported", () => {
    const onChange = vi.fn();
    render(
      <AppLockPage value={BASE} onChange={onChange} touchIdAvailable={true} />,
    );
    const toggle = screen.getByTestId(
      "app-lock-require-touch-id",
    ) as HTMLInputElement;
    expect(toggle).not.toBeDisabled();
    fireEvent.click(toggle);
    expect(onChange).toHaveBeenCalledWith({ requireTouchId: true });
    expect(screen.queryByTestId("app-lock-touch-id-hint")).toBeNull();
  });

  it("disables Touch-ID with a visible hint when unsupported (FR-5.23)", () => {
    render(
      <AppLockPage
        value={{ ...BASE, requireTouchId: true }}
        onChange={vi.fn()}
        touchIdAvailable={false}
      />,
    );
    const toggle = screen.getByTestId(
      "app-lock-require-touch-id",
    ) as HTMLInputElement;
    expect(toggle).toBeDisabled();
    // An unavailable capability can't read as "required".
    expect(toggle.checked).toBe(false);
    // The hint is real text (not color-only) and is described by the control.
    const hint = screen.getByTestId("app-lock-touch-id-hint");
    expect(hint).toBeInTheDocument();
    expect(toggle.getAttribute("aria-describedby")).toBe(hint.id);
  });

  it("renders a load error with a Retry that calls back", () => {
    const onRetry = vi.fn();
    render(
      <AppLockPage
        value={BASE}
        onChange={vi.fn()}
        error="settings unavailable"
        onRetry={onRetry}
      />,
    );
    const alert = screen.getByTestId("app-lock-error");
    expect(alert).toHaveAttribute("role", "alert");
    fireEvent.click(screen.getByTestId("app-lock-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
  it("renders no keychain-protection row when the host omits the block", () => {
    render(<AppLockPage value={BASE} onChange={vi.fn()} />);
    expect(screen.queryByTestId("app-lock-keychain-protection")).toBeNull();
  });

  it("renders the keychain-protection toggle and reports flips", () => {
    const onFlip = vi.fn();
    render(
      <AppLockPage
        value={BASE}
        onChange={vi.fn()}
        keychainProtection={{ enabled: false, available: true }}
        onKeychainProtectionChange={onFlip}
      />,
    );
    const toggle = screen.getByTestId(
      "app-lock-keychain-protection",
    ) as HTMLInputElement;
    expect(toggle.checked).toBe(false);
    expect(toggle.disabled).toBe(false);
    fireEvent.click(toggle);
    expect(onFlip).toHaveBeenCalledWith(true);
  });

  it("disables the keychain toggle when unavailable or busy", () => {
    const { rerender } = render(
      <AppLockPage
        value={BASE}
        onChange={vi.fn()}
        keychainProtection={{ enabled: false, available: false }}
        onKeychainProtectionChange={vi.fn()}
      />,
    );
    expect(
      (screen.getByTestId("app-lock-keychain-protection") as HTMLInputElement)
        .disabled,
    ).toBe(true);
    rerender(
      <AppLockPage
        value={BASE}
        onChange={vi.fn()}
        keychainProtection={{ enabled: true, available: true, busy: true }}
        onKeychainProtectionChange={vi.fn()}
      />,
    );
    const toggle = screen.getByTestId(
      "app-lock-keychain-protection",
    ) as HTMLInputElement;
    expect(toggle.disabled).toBe(true);
    expect(toggle.checked).toBe(true);
  });
});
