import type { ConversationId } from "@enterprise-search/api-types";
import { describe, expect, it, vi } from "vitest";

import type { NotificationPort, NotifyPayload } from "./NotificationPort";

describe("NotificationPort contract", () => {
  it("accepts a notify payload with optional ref + priority", () => {
    const notify = vi.fn();
    const isAvailable = vi.fn(() => true);
    const port: NotificationPort = { notify, isAvailable };
    const payload: NotifyPayload = {
      title: "Approval requested",
      body: "Acme renewal",
      destination: "inbox",
      ref: { kind: "chat", id: "conv_001" as ConversationId },
      priority: "high",
    };
    port.notify(payload);
    expect(notify).toHaveBeenCalledWith(payload);
  });

  it("supports an optional requestPermission method (web only)", async () => {
    const requestPermission = vi.fn().mockResolvedValue("granted" as const);
    const port: NotificationPort = {
      notify: () => undefined,
      isAvailable: () => false,
      requestPermission,
    };
    expect(port.requestPermission).toBeDefined();
    const result = await port.requestPermission?.();
    expect(result).toBe("granted");
  });

  it("allows desktop implementations to omit requestPermission", () => {
    const port: NotificationPort = {
      notify: () => undefined,
      isAvailable: () => true,
    };
    expect(port.requestPermission).toBeUndefined();
  });
});
