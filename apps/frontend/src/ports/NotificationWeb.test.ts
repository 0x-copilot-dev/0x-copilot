import {
  __resetItemRefRegistryForTests,
  registerItemRefResolver,
  type ArtifactRoute,
} from "@0x-copilot/chat-surface";
import type { ConversationId } from "@0x-copilot/api-types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WebNotificationPort } from "./NotificationWeb";

interface NotificationStub {
  title: string;
  options?: NotificationOptions;
  onclick: ((this: Notification, ev: Event) => unknown) | null;
}

const instances: NotificationStub[] = [];

function installNotification(permission: NotificationPermission): void {
  class FakeNotification implements NotificationStub {
    public title: string;
    public options?: NotificationOptions;
    public onclick: ((this: Notification, ev: Event) => unknown) | null = null;
    static permission: NotificationPermission = permission;
    static requestPermission(): Promise<NotificationPermission> {
      return Promise.resolve("granted");
    }
    constructor(title: string, options?: NotificationOptions) {
      this.title = title;
      this.options = options;
      instances.push(this);
    }
  }
  (globalThis as { Notification?: unknown }).Notification = FakeNotification;
}

function uninstallNotification(): void {
  (globalThis as { Notification?: unknown }).Notification = undefined;
}

beforeEach(() => {
  instances.length = 0;
  __resetItemRefRegistryForTests();
});

afterEach(() => {
  uninstallNotification();
});

describe("WebNotificationPort", () => {
  it("isAvailable returns false when Notification is undefined", () => {
    uninstallNotification();
    const port = new WebNotificationPort({ navigate: vi.fn() });
    expect(port.isAvailable()).toBe(false);
  });

  it("isAvailable returns false when permission is not granted", () => {
    installNotification("denied");
    const port = new WebNotificationPort({ navigate: vi.fn() });
    expect(port.isAvailable()).toBe(false);
  });

  it("isAvailable returns true when permission is granted", () => {
    installNotification("granted");
    const port = new WebNotificationPort({ navigate: vi.fn() });
    expect(port.isAvailable()).toBe(true);
  });

  it("notify is a no-op when permission is not granted", () => {
    installNotification("denied");
    const port = new WebNotificationPort({ navigate: vi.fn() });
    port.notify({
      title: "Hi",
      body: "Body",
      destination: "chats",
    });
    expect(instances.length).toBe(0);
  });

  it("notify constructs a Notification with body + destination tag", () => {
    installNotification("granted");
    const port = new WebNotificationPort({ navigate: vi.fn() });
    port.notify({
      title: "New message",
      body: "Hello",
      destination: "inbox",
    });
    expect(instances.length).toBe(1);
    expect(instances[0].title).toBe("New message");
    expect(instances[0].options?.body).toBe("Hello");
    expect(instances[0].options?.tag).toBe("inbox");
  });

  it("click navigates via the resolved ItemRef route", async () => {
    installNotification("granted");
    const route: ArtifactRoute = {
      kind: "chat",
      conversationId: "conv_001",
    };
    registerItemRefResolver("chat", async () => ({
      label: "Convo",
      icon: null,
      route,
    }));
    const navigate = vi.fn();
    const port = new WebNotificationPort({ navigate });
    port.notify({
      title: "Reply",
      body: "Body",
      destination: "chats",
      ref: { kind: "chat", id: "conv_001" as ConversationId },
    });
    const notification = instances[0];
    expect(notification.onclick).not.toBeNull();
    notification.onclick?.call(
      notification as unknown as Notification,
      new Event("click"),
    );
    // resolveItemRef is async; wait a microtask + a macrotask so the
    // .then(navigate) chain settles.
    await new Promise((r) => setTimeout(r, 0));
    expect(navigate).toHaveBeenCalledWith(route);
  });

  it("click is a no-op when no ref is supplied", () => {
    installNotification("granted");
    const navigate = vi.fn();
    const port = new WebNotificationPort({ navigate });
    port.notify({
      title: "Reply",
      body: "Body",
      destination: "chats",
    });
    const notification = instances[0];
    expect(notification.onclick).toBeNull();
    expect(navigate).not.toHaveBeenCalled();
  });
});
