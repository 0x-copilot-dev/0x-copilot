// ConnectorCard — access-mode segment wiring + click isolation.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ConnectorCard } from "./ConnectorCard";

const NOW = Date.parse("2026-05-17T12:00:00.000Z");

describe("ConnectorCard", () => {
  it("renders name + status pill", () => {
    render(
      <ConnectorCard
        id="conn_1"
        displayName="Gmail"
        status="connected"
        lastSyncIso="2026-05-17T11:50:00.000Z"
        now={NOW}
      />,
    );
    expect(screen.getByTestId("connector-card-name")).toHaveTextContent(
      "Gmail",
    );
  });

  it("renders the AccessModeSegment reflecting the current mode", () => {
    render(
      <ConnectorCard
        id="conn_1"
        displayName="Gmail"
        status="connected"
        lastSyncIso={null}
        accessMode="read_act"
        onAccessModeChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId("connector-card-access")).toBeInTheDocument();
    expect(
      screen.getByRole("radiogroup", { name: "Access mode for Gmail" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Read & act" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("omits the segment when no access mode is provided", () => {
    render(
      <ConnectorCard
        id="conn_1"
        displayName="Gmail"
        status="connected"
        lastSyncIso={null}
      />,
    );
    expect(screen.queryByTestId("connector-card-access")).toBeNull();
  });

  it("fires onAccessModeChange with the picked mode", () => {
    const onAccessModeChange = vi.fn();
    render(
      <ConnectorCard
        id="conn_1"
        displayName="Gmail"
        status="connected"
        lastSyncIso={null}
        accessMode="read"
        onAccessModeChange={onAccessModeChange}
      />,
    );
    fireEvent.click(screen.getByRole("radio", { name: "Off" }));
    expect(onAccessModeChange).toHaveBeenCalledWith("off");
  });

  it("changing the segment does not trigger the card open handler", () => {
    const onClick = vi.fn();
    const onAccessModeChange = vi.fn();
    render(
      <ConnectorCard
        id="conn_1"
        displayName="Gmail"
        status="connected"
        lastSyncIso={null}
        accessMode="read"
        onAccessModeChange={onAccessModeChange}
        onClick={onClick}
      />,
    );
    fireEvent.click(screen.getByRole("radio", { name: "Read & act" }));
    expect(onAccessModeChange).toHaveBeenCalledWith("read_act");
    expect(onClick).not.toHaveBeenCalled();
  });

  it("renders a Reconnect action wired to its callback", () => {
    const onReconnect = vi.fn();
    render(
      <ConnectorCard
        id="conn_1"
        displayName="Gmail"
        status="expired"
        lastSyncIso={null}
        action={{ label: "Reconnect", onClick: onReconnect }}
      />,
    );
    fireEvent.click(screen.getByTestId("connector-card-action"));
    expect(onReconnect).toHaveBeenCalledTimes(1);
  });
});
