// @vitest-environment node
import { describe, expect, it } from "vitest";

import {
  LOCAL_BROKER_AUDIENCE,
  LocalServiceIdentityRegistry,
} from "./local-service-identity";

describe("LocalServiceIdentityRegistry", () => {
  it("mints a distinct credential for every service and broker audience", () => {
    let byte = 0;
    const identities = new LocalServiceIdentityRegistry({
      randomBytes: (size) => Buffer.alloc(size, ++byte),
    });
    const aiCapability = identities.forBroker(
      "ai-backend",
      LOCAL_BROKER_AUDIENCE.capability,
    );
    const allChannels = [
      ...(["backend", "ai-backend", "backend-facade"] as const).flatMap(
        (service) => [
          identities.forBroker(service, LOCAL_BROKER_AUDIENCE.browser),
          identities.forBroker(service, LOCAL_BROKER_AUDIENCE.capability),
        ],
      ),
    ];
    expect(new Set(allChannels.map((channel) => channel.credential)).size).toBe(
      allChannels.length,
    );
    expect(identities.forService("ai-backend").audience).toBe(
      "desktop-local:ai-backend",
    );
    expect(
      identities.verifies(
        "ai-backend",
        LOCAL_BROKER_AUDIENCE.capability,
        aiCapability.credential,
      ),
    ).toBe(true);
    expect(
      identities.verifies(
        "ai-backend",
        LOCAL_BROKER_AUDIENCE.browser,
        aiCapability.credential,
      ),
    ).toBe(false);
  });

  it("rejects a missing or swapped identity registry", () => {
    expect(
      () =>
        new LocalServiceIdentityRegistry({
          identities: [
            {
              service: "ai-backend",
              audience: "desktop-local:ai-backend",
            },
          ],
          channelCredentials: [],
        }),
    ).toThrow(/missing/i);
  });

  it("fails closed rather than issuing the same credential to sibling services", () => {
    expect(
      () =>
        new LocalServiceIdentityRegistry({
          randomBytes: (size) => Buffer.alloc(size, 1),
        }),
    ).toThrow(/distinct/i);
  });
});
