import { describe, expect, it } from "vitest";

import { createArtifactPromotionPort } from "./ArtifactPromotionPort";
import type { Transport } from "./Transport";

describe("createArtifactPromotionPort", () => {
  it("delegates exact source promotion through the shared transport", async () => {
    const received: unknown[] = [];
    const transport = {
      request: async <T>(request: unknown): Promise<T> => {
        received.push(request);
        return {} as T;
      },
      subscribeServerSentEvents: () => ({ close() {} }),
      getSession: () => ({ bearer: null }),
      capabilities: () => ({
        substrate: "web" as const,
        nativeSecretStorage: false,
        fileSystemAccess: false,
        clipboardWrite: false,
        openExternal: false,
      }),
    } satisfies Transport;

    await createArtifactPromotionPort(transport).promote({
      runId: "run_1",
      sourceRef: "payload://evt_1",
      kind: "dataset",
      idempotencyKey: "promote-evt-1",
    });

    expect(received).toHaveLength(1);
    expect(received[0]).toMatchObject({
      method: "POST",
      path: "/v1/agent/artifacts:promote",
      body: { run_id: "run_1", source_ref: "payload://evt_1" },
    });
  });
});
