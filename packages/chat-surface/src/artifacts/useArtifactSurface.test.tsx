import { renderHook, waitFor } from "@testing-library/react";
import {
  ArtifactContentRefCodec,
  type ArtifactDetailResponse,
  type ArtifactRevision,
} from "@0x-copilot/api-types";
import type {
  ArtifactCapableTransport,
  TypedRequest,
} from "@0x-copilot/chat-transport";
import { describe, expect, it, vi } from "vitest";
import { useArtifactSurface } from "./useArtifactSurface";

const ARTIFACT_ID = "art_550e8400-e29b-41d4-a716-446655440000";

function revision(number: number): ArtifactRevision {
  return {
    artifact_id: ARTIFACT_ID,
    revision: number,
    ...(number > 1 ? { parent_revision: number - 1 } : {}),
    content_ref: ArtifactContentRefCodec.format(ARTIFACT_ID, number),
    content_digest: (number % 16).toString(16).repeat(64),
    byte_size: 10,
    author: "user",
    created_at: "2026-01-01T00:00:00Z",
  };
}

function transport(currentNumber = 3): ArtifactCapableTransport {
  const current = revision(currentNumber);
  const detail: ArtifactDetailResponse = {
    artifact: {
      artifact_id: ARTIFACT_ID,
      org_id: "org_test",
      user_id: "user_test",
      conversation_id: "conv_test",
      run_id: "run_test",
      kind: "code",
      title: "Example",
      media_type: "text/plain",
      current_revision: current.revision,
      created_by: "user",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
    current_revision: current,
    suggested_filename: "example.txt",
    range_supported: false,
  };
  const request = async <TRes,>(request: TypedRequest): Promise<TRes> => {
    const match = /\/revisions\/(\d+)$/.exec(request.path);
    const response =
      match === null
        ? detail
        : { revision: revision(Number(match[1])), range_supported: false };
    return response as TRes;
  };
  return {
    request,
    subscribeServerSentEvents: () => ({ close: () => {} }),
    getSession: () => ({ bearer: null }),
    capabilities: () => ({
      substrate: "web",
      nativeSecretStorage: false,
      fileSystemAccess: false,
      clipboardWrite: false,
      openExternal: false,
    }),
    getArtifactContent: vi.fn(async ({ revision: number }) => ({
      body: new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(
            new TextEncoder().encode(`\ufeffrevision ${number}`),
          );
          controller.close();
        },
      }),
      contentType: "text/plain",
      contentLength: 10,
      etag: `"${number}"`,
      filename: "example.txt",
    })),
    createArtifactRevision: vi.fn(),
  };
}

describe("useArtifactSurface", () => {
  it("fetches an explicitly selected historical revision instead of silently substituting the head", async () => {
    const client = transport();
    const { result } = renderHook(() =>
      useArtifactSurface(client, ARTIFACT_ID, 1, true),
    );
    await waitFor(() => expect(result.current.state?.revision).toBe(1));
    expect(result.current.latestRevision).toBe(3);
    expect(result.current.state?.text).toBe("\ufeffrevision 1");
  });

  it("keeps a deep-linked historical revision actionable outside the newest history page", async () => {
    const client = transport(30);
    const { result } = renderHook(() =>
      useArtifactSurface(client, ARTIFACT_ID, 1, true),
    );
    await waitFor(() =>
      expect(result.current.revisions.some((item) => item.revision === 1)).toBe(
        true,
      ),
    );
    expect(result.current.hasOlderHistory).toBe(true);
  });
});
