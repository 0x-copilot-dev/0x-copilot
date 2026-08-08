import { render, screen, waitFor } from "@testing-library/react";
import {
  ArtifactContentRefCodec,
  type ArtifactDetailResponse,
  type ArtifactKind,
  type ArtifactMutationResponse,
  type ArtifactRevision,
} from "@0x-copilot/api-types";
import type {
  ArtifactCapableTransport,
  TypedRequest,
} from "@0x-copilot/chat-transport";
import { afterEach, describe, expect, it, vi } from "vitest";
import { registerAdapter, unregisterAdapter } from "../surfaces";
import { ArtifactSurface } from "./ArtifactSurface";

const ARTIFACT_ID = "art_550e8400-e29b-41d4-a716-446655440000";
const SOURCE =
  "# Issues\n\n| Issue | Status |\n| --- | --- |\n| PAR-9 | Cool |\n";

/**
 * What the host attaches to a surface it is willing to let a user write to.
 * Duck-typed on purpose: it is a live function over the host's transport, not
 * a wire field — see `surface-renderers`' `hostEditorActions`.
 */
interface DocumentEditorGrant {
  readonly disabled: boolean;
  readonly saveRevision: (
    source: string,
  ) => Promise<"saved" | "conflict" | "error">;
}

function grantOf(state: unknown): DocumentEditorGrant | undefined {
  const record = state as { readonly documentEditor?: DocumentEditorGrant };
  return record?.documentEditor;
}

function revision(number: number): ArtifactRevision {
  return {
    artifact_id: ARTIFACT_ID,
    revision: number,
    ...(number > 1 ? { parent_revision: number - 1 } : {}),
    content_ref: ArtifactContentRefCodec.format(ARTIFACT_ID, number),
    content_digest: String(number).repeat(64),
    byte_size: SOURCE.length,
    author: "model",
    source_ref: "message://source",
    created_at: "2026-08-06T00:00:00Z",
  };
}

function makeTransport(kind: ArtifactKind): {
  readonly client: ArtifactCapableTransport;
  readonly createRevision: ReturnType<typeof vi.fn>;
} {
  const detail: ArtifactDetailResponse = {
    artifact: {
      artifact_id: ARTIFACT_ID,
      org_id: "org_test",
      user_id: "user_test",
      conversation_id: "conv_test",
      run_id: "run_test",
      kind,
      title: "My Assigned Linear Issues",
      media_type: "text/markdown",
      current_revision: 2,
      created_by: "model",
      created_at: "2026-08-06T00:00:00Z",
      updated_at: "2026-08-06T00:00:00Z",
    },
    current_revision: revision(2),
    suggested_filename: "issues.md",
    range_supported: false,
  };
  const request = async <TRes,>(item: TypedRequest): Promise<TRes> => {
    const match = /\/revisions\/(\d+)$/.exec(item.path);
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
  return {
    createRevision,
    client: {
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
      getArtifactContent: vi.fn(async () => ({
        body: new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(new TextEncoder().encode(SOURCE));
            controller.close();
          },
        }),
        contentType: "text/markdown",
        contentLength: SOURCE.length,
        etag: '"2"',
        filename: "issues.md",
      })),
      createArtifactRevision: createRevision,
    },
  };
}

/**
 * Stands in for `surface-renderers`' real renderer, which this package must not
 * import (the dependency runs the other way). It captures the state the mount
 * hands an adapter, which is exactly the seam the grant travels over.
 */
function captureAdapter(scheme: string): { readonly state: () => unknown } {
  let captured: unknown = null;
  registerAdapter({
    scheme,
    matches: (uri) => uri.startsWith(`${scheme}://`),
    renderCurrent: (state) => {
      captured = state;
      return <div data-testid={`captured-${scheme}`} />;
    },
    renderDiff: () => <div />,
    metadata: { origin: "first-party", schemaVersion: 1 },
  });
  return { state: () => captured };
}

afterEach(() => {
  unregisterAdapter("artifact-document");
  unregisterAdapter("artifact-code");
});

describe("ArtifactSurface in-place editing", () => {
  it("grants the document surface an editor whose save is the same revision path, and mounts no raw-markdown textarea", async () => {
    const adapter = captureAdapter("artifact-document");
    const { client, createRevision } = makeTransport("document");
    const { container } = render(
      <ArtifactSurface
        uri={`artifact-document://${ARTIFACT_ID}@2`}
        transport={client}
      />,
    );
    await screen.findByTestId("captured-artifact-document");

    // The deleted surface: a raw `<textarea>` of the whole document, mounted
    // BELOW the render. Nothing paints one now, under any state.
    expect(container.querySelector("textarea")).toBeNull();
    expect(screen.queryByTestId("artifact-editor")).toBeNull();

    const grant = grantOf(adapter.state());
    expect(grant?.disabled).toBe(false);
    // The revision history — the r1/r2/r3 trail and its Restore — is untouched
    // by the deletion; only the textarea went.
    expect(screen.getByTestId("artifact-revision-history")).toBeInTheDocument();
    await screen.findByRole("button", { name: "Restore as new revision" });

    // The grant IS the old save path: one call appends a revision at the head.
    await grant!.saveRevision("# Issues\n\n| Issue | Status |\n");
    await waitFor(() => expect(createRevision).toHaveBeenCalledTimes(1));
    const [request] = createRevision.mock.calls[0] as [
      {
        readonly artifactId: string;
        readonly parentRevision: number;
        readonly content: Uint8Array;
      },
    ];
    expect(request).toMatchObject({
      artifactId: ARTIFACT_ID,
      parentRevision: 2,
    });
    expect(new TextDecoder().decode(request.content)).toBe(
      "# Issues\n\n| Issue | Status |\n",
    );
  });

  it("grants nothing to a code artifact, so the origin gate is explicit rather than incidental", async () => {
    const adapter = captureAdapter("artifact-code");
    const { client } = makeTransport("code");
    const { container } = render(
      <ArtifactSurface
        uri={`artifact-code://${ARTIFACT_ID}@2`}
        transport={client}
      />,
    );
    await screen.findByTestId("captured-artifact-code");

    expect(grantOf(adapter.state())).toBeUndefined();
    expect(container.querySelector("textarea")).toBeNull();
  });

  it("scopes the grant's field names when the host asked for a prefix", async () => {
    const adapter = captureAdapter("artifact-document");
    const { client } = makeTransport("document");
    render(
      <ArtifactSurface
        uri={`artifact-document://${ARTIFACT_ID}@2`}
        transport={client}
        idPrefix={`inline-${ARTIFACT_ID}`}
      />,
    );
    await screen.findByTestId("captured-artifact-document");

    expect(
      (adapter.state() as { readonly documentEditor?: { idPrefix?: string } })
        .documentEditor?.idPrefix,
    ).toBe(`inline-${ARTIFACT_ID}`);
  });
});
