import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ComposerConnectorsPort } from "@0x-copilot/chat-surface";

import { ChatToolsMenu } from "./ChatToolsTrigger";

function fakeConnectorsPort(): ComposerConnectorsPort {
  return {
    listServers: vi.fn().mockResolvedValue([]),
    listCatalog: vi.fn().mockResolvedValue([]),
    installFromCatalog: vi.fn(),
    addCustomServer: vi.fn(),
    beginAuth: vi.fn(),
  };
}

describe("ChatToolsMenu", () => {
  it("renders only the shared + menu body, with an explicit Back control", () => {
    render(
      <ChatToolsMenu
        port={fakeConnectorsPort()}
        webSearchEnabled
        onToggleWebSearch={vi.fn()}
        activeConnectorIds={[]}
        onToggleConnector={vi.fn()}
        onConnectCatalog={vi.fn()}
        onAddCustom={vi.fn()}
        onBack={vi.fn()}
      />,
    );

    expect(screen.getByTestId("first-run-tools-websearch")).toBeTruthy();
    expect(screen.getByTestId("first-run-tools-back")).toBeTruthy();
    expect(screen.queryByTestId("first-run-tools-button")).toBeNull();
    expect(screen.queryByTestId("first-run-tools-popover")).toBeNull();
  });
});
