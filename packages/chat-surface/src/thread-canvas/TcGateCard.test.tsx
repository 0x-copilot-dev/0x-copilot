import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { TcGateCard } from "./TcGateCard";
import type { LedgerGate } from "./ledgerProjection";

function gate(overrides: Partial<LedgerGate> = {}): LedgerGate {
  return {
    gateId: "mcp_auth:run_7f3:seed:linear",
    serverId: "seed:linear",
    connector: "linear",
    purpose: "to run create_issue on Linear",
    scopes: ["docs:read", "docs:write"],
    authState: "missing",
    opClass: "write",
    ledgerId: "r7f3·042",
    createdSeq: 42,
    lastSeq: 42,
    resolved: false,
    outcome: null,
    writePolicy: null,
    ...overrides,
  };
}

describe("TcGateCard", () => {
  it("renders connector, purpose, scopes and the ledger id", () => {
    render(
      <TcGateCard
        gate={gate()}
        onConnect={vi.fn()}
        onSkip={vi.fn()}
        onPolicyChange={vi.fn()}
        writePolicy="ask_first"
      />,
    );
    expect(screen.getByTestId("tc-gate-connector")).toHaveTextContent("linear");
    expect(screen.getByTestId("tc-gate-purpose")).toHaveTextContent(
      "create_issue",
    );
    expect(screen.getByTestId("tc-gate-scopes")).toHaveTextContent("docs:read");
    expect(screen.getByTestId("tc-gate-ledger-id")).toHaveTextContent(
      "r7f3·042",
    );
    expect(screen.getByTestId("tc-gate-parked")).toHaveTextContent("parked");
  });

  it("shows the write-policy radio (not the read pledge) for a write gate", () => {
    render(
      <TcGateCard
        gate={gate({ opClass: "write" })}
        onConnect={vi.fn()}
        onSkip={vi.fn()}
        onPolicyChange={vi.fn()}
        writePolicy="ask_first"
      />,
    );
    expect(screen.getByTestId("tc-gate-policy")).toBeTruthy();
    expect(screen.queryByTestId("tc-gate-readonly-pledge")).toBeNull();
    // Default selection is ask_first.
    expect(screen.getByTestId("tc-gate-policy-ask")).toBeChecked();
    expect(screen.getByTestId("tc-gate-policy-allow")).not.toBeChecked();
  });

  it("shows the read-only pledge (not the radio) for a read gate", () => {
    render(
      <TcGateCard
        gate={gate({ opClass: "read" })}
        onConnect={vi.fn()}
        onSkip={vi.fn()}
        onPolicyChange={vi.fn()}
        writePolicy="ask_first"
      />,
    );
    expect(screen.getByTestId("tc-gate-readonly-pledge")).toBeTruthy();
    expect(screen.queryByTestId("tc-gate-policy")).toBeNull();
  });

  it("fires connect/skip with the server id and policy-change with the choice", () => {
    const onConnect = vi.fn();
    const onSkip = vi.fn();
    const onPolicyChange = vi.fn();
    render(
      <TcGateCard
        gate={gate()}
        onConnect={onConnect}
        onSkip={onSkip}
        onPolicyChange={onPolicyChange}
        writePolicy="ask_first"
      />,
    );
    fireEvent.click(screen.getByTestId("tc-gate-connect"));
    expect(onConnect).toHaveBeenCalledWith("seed:linear");
    fireEvent.click(screen.getByTestId("tc-gate-skip"));
    expect(onSkip).toHaveBeenCalledWith("seed:linear");
    fireEvent.click(screen.getByTestId("tc-gate-policy-allow"));
    expect(onPolicyChange).toHaveBeenCalledWith("allow_always");
  });

  it("disables actions when busy", () => {
    render(
      <TcGateCard
        gate={gate()}
        onConnect={vi.fn()}
        onSkip={vi.fn()}
        onPolicyChange={vi.fn()}
        writePolicy="ask_first"
        busy
      />,
    );
    expect(screen.getByTestId("tc-gate-connect")).toBeDisabled();
    expect(screen.getByTestId("tc-gate-skip")).toBeDisabled();
  });
});
