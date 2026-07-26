// @vitest-environment node
import { createPublicKey, verify } from "node:crypto";

import { describe, expect, it, vi } from "vitest";

import {
  DESKTOP_WORKSPACE_ATTESTATION_PATH,
  DesktopWorkspaceAttestationError,
  DesktopWorkspaceAttestationPublisher,
} from "./workspace-attestation";

const SAFE_ATTESTATION = {
  workspaceWriteIsolation: "enforced" as const,
  nativeWorkspacePrimitives: "available" as const,
};

describe("DesktopWorkspaceAttestationPublisher", () => {
  it("signs a path-free, canonical C2 capability statement", () => {
    const publisher = new DesktopWorkspaceAttestationPublisher({
      attestation: SAFE_ATTESTATION,
      now: () => 1_700_000_000_000,
      bootId: "dwa_abcdefghijklmnopqrstuvwxyz123456",
    });

    const bootstrap = publisher.bootstrap();
    const claims = JSON.parse(
      Buffer.from(bootstrap.payload, "base64url").toString("utf8"),
    ) as Record<string, unknown>;
    const publicKey = createPublicKey({
      key: Buffer.from(bootstrap.publicKey, "base64url"),
      format: "der",
      type: "spki",
    });

    expect(
      verify(
        null,
        Buffer.from(bootstrap.payload, "utf8"),
        publicKey,
        Buffer.from(bootstrap.signature, "base64url"),
      ),
    ).toBe(true);
    expect(claims).toEqual({
      v: 1,
      boot_id: "dwa_abcdefghijklmnopqrstuvwxyz123456",
      issued_at_ms: 1_700_000_000_000,
      expires_at_ms: 1_700_000_300_000,
      native_workspace_primitives: "available",
      unsafe_dev_workspace_tcb: false,
      workspace_write_isolation: "enforced",
    });
    expect(JSON.stringify(claims)).not.toMatch(/[/\\]/u);
  });

  it("publishes only through the facade with the main-only host token", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockResolvedValue(new Response(null, { status: 204 }));
    const publisher = new DesktopWorkspaceAttestationPublisher({
      attestation: SAFE_ATTESTATION,
      fetch,
    });

    await publisher.publish({
      facadeBaseUrl: "http://127.0.0.1:8200/",
      hostToken: "main-only-host-token",
    });

    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, init] = fetch.mock.calls[0] ?? [];
    expect(url).toBe(
      `http://127.0.0.1:8200${DESKTOP_WORKSPACE_ATTESTATION_PATH}`,
    );
    expect(init?.headers).toEqual({
      "content-type": "application/json",
      "x-enterprise-service-token": "main-only-host-token",
    });
    expect(JSON.parse(String(init?.body))).toEqual({
      payload: expect.any(String),
      signature: expect.any(String),
    });
  });

  it("fails closed without a supervised host token", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>();
    const publisher = new DesktopWorkspaceAttestationPublisher({
      attestation: SAFE_ATTESTATION,
      fetch,
    });

    await expect(
      publisher.publish({
        facadeBaseUrl: "http://127.0.0.1:8200",
        hostToken: "",
      }),
    ).rejects.toBeInstanceOf(DesktopWorkspaceAttestationError);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("reports unavailable native authority honestly instead of upgrading it", () => {
    const publisher = new DesktopWorkspaceAttestationPublisher({
      attestation: {
        workspaceWriteIsolation: "unavailable",
        nativeWorkspacePrimitives: "unavailable",
        unsafeDevWorkspaceTcb: true,
      },
    });
    const claims = JSON.parse(
      Buffer.from(publisher.envelope().payload, "base64url").toString("utf8"),
    ) as Record<string, unknown>;

    expect(claims).toMatchObject({
      workspace_write_isolation: "unavailable",
      native_workspace_primitives: "unavailable",
      unsafe_dev_workspace_tcb: true,
    });
  });
});
