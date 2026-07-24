import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  ArtifactContentRefCodec,
  type ArtifactDetailResponse,
  type ArtifactMutationResponse,
  type ArtifactRevision,
} from "@0x-copilot/api-types";
import type {
  ArtifactCapableTransport,
  TypedRequest,
} from "@0x-copilot/chat-transport";
import { describe, expect, it, vi } from "vitest";
import { ArtifactSurface } from "./ArtifactSurface";

const ARTIFACT_ID = "art_550e8400-e29b-41d4-a716-446655440000";

function revision(number: number): ArtifactRevision {
  return {
    artifact_id: ARTIFACT_ID,
    revision: number,
    ...(number > 1 ? { parent_revision: number - 1 } : {}),
    content_ref: ArtifactContentRefCodec.format(ARTIFACT_ID, number),
    content_digest: String(number).repeat(64),
    byte_size: number === 1 ? 15 : 19,
    author: "user",
    source_ref: "message://source",
    created_at: "2026-01-01T00:00:00Z",
  };
}

function makeTransport(): {
  readonly client: ArtifactCapableTransport;
  readonly createRevision: ReturnType<typeof vi.fn>;
} {
  const head = revision(2);
  const detail: ArtifactDetailResponse = {
    artifact: {
      artifact_id: ARTIFACT_ID,
      org_id: "org_test",
      user_id: "user_test",
      conversation_id: "conv_test",
      run_id: "run_test",
      kind: "code",
      title: "example",
      media_type: "text/plain",
      current_revision: 2,
      created_by: "user",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
    current_revision: head,
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
  const createRevision = vi.fn(
    async (): Promise<ArtifactMutationResponse> => ({
      ...detail,
      artifact: { ...detail.artifact, current_revision: 3 },
      current_revision: revision(3),
      replayed: false,
    }),
  );
  const client: ArtifactCapableTransport = {
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
    getArtifactContent: vi.fn(async ({ revision: number }) => {
      const text = number === 1 ? "title\nold line\n" : "title\nnew line\n";
      return {
        body: new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(new TextEncoder().encode(text));
            controller.close();
          },
        }),
        contentType: "text/plain",
        contentLength: text.length,
        etag: `"${number}"`,
        filename: "example.txt",
      };
    }),
    createArtifactRevision: createRevision,
  };
  return { client, createRevision };
}

describe("ArtifactSurface revision controls", () => {
  it("compares a historical revision and restores its exact bytes as a new revision at the current head", async () => {
    const { client, createRevision } = makeTransport();
    render(
      <ArtifactSurface
        uri={`artifact-code://${ARTIFACT_ID}@1`}
        transport={client}
      />,
    );

    const compare = await screen.findByRole("button", {
      name: "Compare to current",
    });
    fireEvent.click(compare);
    await screen.findByLabelText("Text change details");
    expect(screen.getByLabelText("Text change details")).toHaveTextContent(
      "Removed: old line",
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Restore as new revision" }),
    );
    await waitFor(() => expect(createRevision).toHaveBeenCalledTimes(1));
    const [request] = createRevision.mock.calls[0] as [
      {
        readonly artifactId: string;
        readonly parentRevision: number;
        readonly expectedDigest: string;
        readonly content: Uint8Array;
      },
    ];
    expect(request).toMatchObject({
      artifactId: ARTIFACT_ID,
      parentRevision: 2,
      expectedDigest: "2".repeat(64),
    });
    expect(Array.from(request.content)).toEqual(
      Array.from(new TextEncoder().encode("title\nold line\n")),
    );
  });
});
