import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ComposerConnectorsPort } from "@0x-copilot/chat-surface";

import { ChatToolsTrigger } from "./ChatToolsTrigger";

function fakeConnectorsPort(): ComposerConnectorsPort {
  return {
    listServers: vi.fn().mockResolvedValue([]),
    listCatalog: vi.fn().mockResolvedValue([]),
    installFromCatalog: vi.fn(),
    addCustomServer: vi.fn(),
    beginAuth: vi.fn(),
  };
}

describe("ChatToolsTrigger", () => {
  it("opens the shared portal-safe Tools pill", () => {
    render(
      <ChatToolsTrigger
        port={fakeConnectorsPort()}
        webSearchEnabled
        onToggleWebSearch={vi.fn()}
        activeConnectorIds={[]}
        onToggleConnector={vi.fn()}
        onConnectCatalog={vi.fn()}
        onAddCustom={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByTestId("first-run-tools-button"));
    expect(screen.getByTestId("composer-tools-popover")).toBeTruthy();
    expect(screen.getByTestId("first-run-tools-websearch")).toBeTruthy();
  });
});
