import { describe, expect, it } from "vitest";

import { promoteArtifact } from "./artifactPromotion";
import type { Transport } from "./transport";

describe("promoteArtifact", () => {
  it("sends only a logical source reference and metadata", async () => {
    const requests: unknown[] = [];
    const transport = {
      request: async <T>(request: unknown): Promise<T> => {
        requests.push(request);
        return { artifact: "art_1" } as T;
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

    await promoteArtifact<{ artifact: string }>(transport, {
      runId: "run_1",
      sourceRef: "message://msg_1",
      kind: "code",
      title: "example.py",
      mediaType: "text/x-python",
      idempotencyKey: "promote-msg-1",
    });

    expect(requests).toEqual([
      {
        method: "POST",
        path: "/v1/agent/artifacts:promote",
        headers: { "Idempotency-Key": "promote-msg-1" },
        body: {
          run_id: "run_1",
          source_ref: "message://msg_1",
          kind: "code",
          title: "example.py",
          media_type: "text/x-python",
        },
      },
    ]);
  });
});
