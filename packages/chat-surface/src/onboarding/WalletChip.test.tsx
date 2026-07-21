// WalletChip — truncation, jade dot, null-when-no-wallet, chainName tooltip
// (PRD-P4 §1 acceptance).

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { WalletChip, truncateAddress } from "./WalletChip";

// A full EIP-55 checksummed address (the server never pre-truncates).
const FULL = "0x7f3C4b1e2a9d8c7b6a5f4e3d2c1b0a9f8e7d6c5b";

describe("truncateAddress", () => {
  it("collapses a full EIP-55 address to the SPEC `0x{4}…{4}` form", () => {
    expect(truncateAddress(FULL)).toBe("0x7f3C…6c5b");
    // `0x` + first 4 hex chars … last 4 hex chars.
    expect(truncateAddress(FULL)).toMatch(/^0x[0-9a-fA-F]{4}…[0-9a-fA-F]{4}$/);
  });
});

describe("<WalletChip>", () => {
  it("renders the truncated address (never the full one)", () => {
    render(<WalletChip address={FULL} />);
    const chip = screen.getByTestId("first-run-wallet-chip");
    expect(chip.textContent).toContain("0x7f3C…6c5b");
    expect(chip.textContent).not.toContain(FULL);
  });

  it("shows the jade status dot when connected (default)", () => {
    render(<WalletChip address={FULL} />);
    expect(screen.getByTestId("first-run-wallet-dot")).toBeTruthy();
  });

  it("omits the dot when `connected={false}`", () => {
    render(<WalletChip address={FULL} connected={false} />);
    expect(screen.getByTestId("first-run-wallet-chip")).toBeTruthy();
    expect(screen.queryByTestId("first-run-wallet-dot")).toBeNull();
  });

  it("renders nothing for email/Google accounts (address === null)", () => {
    const { container } = render(<WalletChip address={null} />);
    expect(screen.queryByTestId("first-run-wallet-chip")).toBeNull();
    expect(container.firstChild).toBeNull();
  });

  it("puts the chain name (and full address) in the tooltip", () => {
    render(<WalletChip address={FULL} chainName="Base" />);
    const title = screen
      .getByTestId("first-run-wallet-chip")
      .getAttribute("title");
    expect(title).toContain("Base");
    // Tooltip reveals the full, untruncated address.
    expect(title).toContain(FULL);
  });

  it("falls back to the bare address in the tooltip when no chain name", () => {
    render(<WalletChip address={FULL} />);
    expect(
      screen.getByTestId("first-run-wallet-chip").getAttribute("title"),
    ).toBe(FULL);
  });
});
