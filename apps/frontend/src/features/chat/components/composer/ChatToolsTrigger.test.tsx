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
  it("does not trap the Tools panel below its click-out scrim", () => {
    const onOpenChange = vi.fn();
    const { rerender } = render(
      <ChatToolsTrigger
        port={fakeConnectorsPort()}
        open={false}
        onOpenChange={onOpenChange}
        webSearchEnabled
        onToggleWebSearch={vi.fn()}
        activeConnectorIds={[]}
        onToggleConnector={vi.fn()}
        onConnectCatalog={vi.fn()}
        onAddCustom={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByTestId("first-run-tools-button"));
    expect(onOpenChange).toHaveBeenCalledWith(true);

    rerender(
      <ChatToolsTrigger
        port={fakeConnectorsPort()}
        open
        onOpenChange={onOpenChange}
        webSearchEnabled
        onToggleWebSearch={vi.fn()}
        activeConnectorIds={[]}
        onToggleConnector={vi.fn()}
        onConnectCatalog={vi.fn()}
        onAddCustom={vi.fn()}
      />,
    );

    const panel = screen.getByTestId("first-run-tools-popover");
    // `.ui-pop` owns z-index 71; an ancestor z-index would create a lower
    // stacking context and leave the fixed z-index-70 scrim over the panel.
    expect(panel.parentElement?.style.zIndex).toBe("");
  });
});
