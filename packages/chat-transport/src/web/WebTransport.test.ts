import { describe, expect, it, vi } from "vitest";

import { TransportHttpError, UnauthorizedError } from "../types";
import { WebTransport } from "./WebTransport";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("WebTransport error mapping", () => {
  it("resolves a JSON body on 2xx", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(200, { ok: true }));
    const transport = new WebTransport({ fetch: fetchImpl });
    await expect(
      transport.request({ method: "GET", path: "/foo" }),
    ).resolves.toEqual({ ok: true });
  });

  it("preserves a structured FastAPI detail as TransportHttpError", async () => {
    const detail = {
      code: "merge_required",
      safe_message: "This wallet already belongs to another account.",
    };
    const fetchImpl = vi.fn(async () => jsonResponse(409, { detail }));
    const transport = new WebTransport({ fetch: fetchImpl });
    const err = await transport
      .request({ method: "POST", path: "/v1/me/identities/wallet" })
      .then(
        () => null,
        (e: unknown) => e,
      );
    expect(err).toBeInstanceOf(TransportHttpError);
    const httpErr = err as TransportHttpError;
    expect(httpErr.status).toBe(409);
    expect(httpErr.code).toBe("merge_required");
    // The human-readable message is the detail's safe_message, not raw JSON.
    expect(httpErr.message).toBe(detail.safe_message);
    expect(httpErr.detail).toEqual(detail);
  });

  it("uses a string detail verbatim as the message", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(400, { detail: "bad request" }),
    );
    const transport = new WebTransport({ fetch: fetchImpl });
    const err = await transport.request({ method: "GET", path: "/x" }).then(
      () => null,
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(TransportHttpError);
    expect((err as TransportHttpError).message).toBe("bad request");
    expect((err as TransportHttpError).code).toBeNull();
  });

  it("throws UnauthorizedError on 401 and notifies the handler", async () => {
    const onUnauthorized = vi.fn();
    const fetchImpl = vi.fn(async () =>
      jsonResponse(401, { detail: "expired" }),
    );
    const transport = new WebTransport({ fetch: fetchImpl, onUnauthorized });
    await expect(
      transport.request({ method: "GET", path: "/x" }),
    ).rejects.toBeInstanceOf(UnauthorizedError);
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it("falls through to the raw body for a non-JSON error", async () => {
    const fetchImpl = vi.fn(
      async () => new Response("upstream timeout", { status: 504 }),
    );
    const transport = new WebTransport({ fetch: fetchImpl });
    const err = await transport.request({ method: "GET", path: "/x" }).then(
      () => null,
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(TransportHttpError);
    expect((err as TransportHttpError).status).toBe(504);
    expect((err as TransportHttpError).message).toBe("upstream timeout");
    expect((err as TransportHttpError).detail).toBeNull();
  });
});
