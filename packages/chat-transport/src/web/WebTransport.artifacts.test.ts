import { describe, expect, it, vi } from "vitest";
import { WebTransport } from "./WebTransport";

describe("WebTransport artifact bytes", () => {
  it("returns the exact stream and metadata without JSON/base64 decoding", async () => {
    const bytes = new Uint8Array([0xef, 0xbb, 0xbf, 0x61, 0x0d, 0x0a]);
    const fetchImpl = vi.fn<
      (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
    >(
      async () =>
        new Response(bytes, {
          status: 200,
          headers: {
            "content-type": "text/csv",
            "content-length": String(bytes.byteLength),
            etag: '"sha256:abc"',
            "content-disposition": "attachment; filename=sample.csv",
          },
        }),
    );
    const transport = new WebTransport({ fetch: fetchImpl });
    const response = await transport.getArtifactContent({
      artifactId: "artifact_1",
      revision: 2,
    });
    expect(
      new Uint8Array(await new Response(response.body).arrayBuffer()),
    ).toEqual(bytes);
    expect(response).toMatchObject({
      contentType: "text/csv",
      contentLength: 6,
      etag: '"sha256:abc"',
      filename: "sample.csv",
    });
    expect(fetchImpl.mock.calls[0]![0]).toContain(
      "/v1/agent/artifacts/artifact_1/revisions/2/content",
    );
  });

  it("uploads a binary multipart revision with parent revision, digest and If-Match", async () => {
    const fetchImpl = vi.fn<
      (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
    >(
      async () =>
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    );
    const transport = new WebTransport({ fetch: fetchImpl });
    await transport.createArtifactRevision({
      artifactId: "artifact_1",
      parentRevision: 2,
      expectedDigest: "a".repeat(64),
      etag: '"sha256:old"',
      content: new Uint8Array([0, 255, 13, 10]),
      contentType: "application/octet-stream",
      filename: "exact.bin",
      idempotencyKey: "idem-1",
    });
    const init = fetchImpl.mock.calls[0]?.[1];
    if (init === undefined)
      throw new Error("fetch was not called with RequestInit");
    expect((init.headers as Record<string, string>)["if-match"]).toBe(
      '"sha256:old"',
    );
    expect((init.headers as Record<string, string>)["idempotency-key"]).toBe(
      "idem-1",
    );
    expect(init.body).toBeInstanceOf(FormData);
    const body = init.body as FormData;
    expect(body.get("parent_revision")).toBe("2");
    expect(body.get("expected_digest")).toBe("a".repeat(64));
    expect(body.get("content")).toBeInstanceOf(File);
  });
});
