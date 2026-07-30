import { render, screen, waitFor } from "@testing-library/react";
import {
  ArtifactContentRefCodec,
  type ArtifactAuthor,
  type ArtifactDetailResponse,
  type ArtifactRevision,
} from "@0x-copilot/api-types";
import type {
  ArtifactCapableTransport,
  TypedRequest,
} from "@0x-copilot/chat-transport";
import { describe, expect, it, vi } from "vitest";
import {
  createSurfaceRegistry,
  SurfaceRegistryProvider,
  type SurfaceRegistry,
} from "../surfaces";
import { ArtifactSurface } from "./ArtifactSurface";

// PRD-03 D4 — a dataset revision is a table change, so the review hands "what
// changed" to the dataset renderer and stops rendering its own word diff. The
// renderer lives in `surface-renderers`, which this package must not import
// (the dependency runs the other way), so the seam under test is the payload on
// the mounted render state — asserted here through a stub adapter that echoes
// it, the same way a real renderer receives it.

const ARTIFACT_ID = "art_550e8400-e29b-41d4-a716-446655440000";
const TEXT: Record<number, string> = {
  1: "name,amount\r\nAda,12\r\n",
  2: "name,amount\r\nAda,15\r\n",
};

interface Store {
  readonly authors: Record<number, ArtifactAuthor>;
  head: number;
}

function makeRevision(store: Store, number: number): ArtifactRevision {
  return {
    artifact_id: ARTIFACT_ID,
    revision: number,
    ...(number > 1 ? { parent_revision: number - 1 } : {}),
    content_ref: ArtifactContentRefCodec.format(ARTIFACT_ID, number),
    content_digest: String(number % 10).repeat(64),
    byte_size: TEXT[number]?.length ?? 0,
    author: store.authors[number] ?? "user",
    source_ref: "message://source",
    created_at: "2026-01-01T00:00:00Z",
  };
}

function makeTransport(store: Store): ArtifactCapableTransport {
  const detailFor = (): ArtifactDetailResponse => ({
    artifact: {
      artifact_id: ARTIFACT_ID,
      org_id: "org_test",
      user_id: "user_test",
      conversation_id: "conv_test",
      run_id: "run_test",
      kind: "dataset",
      title: "Ledger",
      media_type: "text/csv",
      current_revision: store.head,
      created_by: "user",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
    current_revision: makeRevision(store, store.head),
    suggested_filename: "ledger.csv",
    range_supported: false,
  });
  return {
    request: async <TRes,>(request: TypedRequest): Promise<TRes> => {
      const match = /\/revisions\/(\d+)$/.exec(request.path);
      if (match === null) return detailFor() as TRes;
      return {
        revision: makeRevision(store, Number(match[1])),
        range_supported: false,
      } as TRes;
    },
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
      const text = TEXT[number] ?? "";
      return {
        body: new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(new TextEncoder().encode(text));
            controller.close();
          },
        }),
        contentType: "text/csv",
        contentLength: text.length,
        etag: `"${number}"`,
        filename: "ledger.csv",
      };
    }),
    createArtifactRevision: vi.fn(),
  };
}

/** Stands in for the dataset renderer: echoes the change payload it was given.
 *
 * Registers into an ISOLATED registry, never the global one. An earlier version
 * of this file registered globally and reset with `clearRegistry()` in
 * `afterEach`, which wiped the default adapters other files rely on and broke
 * `RunDestination.surfacesV2` — a failure that only appeared in a full-suite
 * run, and only because of file order.
 */
function echoRegistry(): SurfaceRegistry {
  const registry = createSurfaceRegistry();
  registry.registerAdapter({
    scheme: "artifact-dataset",
    matches: (uri) => uri.startsWith("artifact-dataset://"),
    renderCurrent: (state) => {
      const change = (state as { readonly datasetRevisionChange?: unknown })
        .datasetRevisionChange;
      return (
        <pre data-testid="echoed-change">
          {change === undefined ? "none" : JSON.stringify(change)}
        </pre>
      );
    },
    renderDiff: () => <pre data-testid="echoed-change">diff</pre>,
    metadata: { origin: "first-party", schemaVersion: 1 },
  });
  return registry;
}

describe("ArtifactSurface — dataset revision review (PRD-03 D4)", () => {
  it("hands the change to the dataset renderer and drops its own text diff", async () => {
    const registry = echoRegistry();
    const state: Store = { authors: { 1: "user", 2: "model" }, head: 1 };
    const client = makeTransport(state);
    const view = render(
      <SurfaceRegistryProvider registry={registry}>
        <ArtifactSurface
          uri={`artifact-dataset://${ARTIFACT_ID}@1`}
          transport={client}
        />
      </SurfaceRegistryProvider>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("artifact-revision-history")).toHaveTextContent(
        "1 loaded",
      ),
    );

    state.head = 2;
    view.rerender(
      <SurfaceRegistryProvider registry={registry}>
        <ArtifactSurface
          uri={`artifact-dataset://${ARTIFACT_ID}@2`}
          transport={client}
        />
      </SurfaceRegistryProvider>,
    );

    const review = await screen.findByTestId("artifact-revision-review");
    expect(review).toHaveTextContent(
      "The model revised this artifact: r1 → r2",
    );
    // The panel keeps the announcement and both actions, and defers the reading
    // of the change rather than showing a second, poorer one.
    await waitFor(() =>
      expect(review).toHaveTextContent(
        "What changed is shown in the dataset view above",
      ),
    );
    expect(screen.queryByLabelText("Revision change details")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Revert to r1" }),
    ).toBeInTheDocument();

    // The renderer received the base source to diff cells against, plus the
    // bounded text pair it falls back to when the content is not a grid.
    const echoed: unknown = JSON.parse(
      screen.getByTestId("echoed-change").textContent ?? "null",
    );
    expect(echoed).toEqual({
      baseRevision: 1,
      baseText: TEXT[1],
      textBefore: "Ada,12\r",
      textAfter: "Ada,15\r",
    });
  });

  it("attaches no change when no review is open", async () => {
    const registry = echoRegistry();
    const state: Store = { authors: { 1: "user", 2: "user" }, head: 2 };
    const client = makeTransport(state);
    render(
      <SurfaceRegistryProvider registry={registry}>
        <ArtifactSurface
          uri={`artifact-dataset://${ARTIFACT_ID}@2`}
          transport={client}
        />
      </SurfaceRegistryProvider>,
    );

    await waitFor(() =>
      expect(screen.getByTestId("echoed-change")).toHaveTextContent("none"),
    );
    expect(screen.queryByTestId("artifact-revision-review")).toBeNull();
  });
});
