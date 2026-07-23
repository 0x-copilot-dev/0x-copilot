// ViewUpgradeToast (PRD-B3) — the non-modal "View upgraded" notice.

import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ViewUpgradeToast } from "./ViewUpgradeToast";

describe("ViewUpgradeToast", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("shows the ledger id and fires onKeepGeneric on click", () => {
    const onKeepGeneric = vi.fn();
    render(
      <ViewUpgradeToast
        surfaceId="s1"
        ledgerId="r7f3·042"
        onKeepGeneric={onKeepGeneric}
        onDismiss={vi.fn()}
        autoDismissMs={0}
      />,
    );
    expect(screen.getByTestId("tc-view-upgrade-ledger-id").textContent).toBe(
      "r7f3·042",
    );
    fireEvent.click(screen.getByTestId("tc-view-upgrade-keep-generic"));
    expect(onKeepGeneric).toHaveBeenCalledWith("s1");
  });

  it("auto-dismisses after the timeout", () => {
    const onDismiss = vi.fn();
    render(
      <ViewUpgradeToast
        surfaceId="s1"
        ledgerId="r7f3·042"
        onKeepGeneric={vi.fn()}
        onDismiss={onDismiss}
        autoDismissMs={5000}
      />,
    );
    expect(onDismiss).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});
