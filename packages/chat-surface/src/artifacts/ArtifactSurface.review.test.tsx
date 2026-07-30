import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  ArtifactContentRefCodec,
  type ArtifactAuthor,
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

// PRD-03 D1 — a revision the reader did not make must announce itself as a
// diff with keep/revert, never as a silent content swap. The arrival is driven
// the way the real host drives it: `projectArtifactTabs` re-emits the tab uri
// at the new revision, so the surface re-renders with `…@n`.

const ARTIFACT_ID = "art_550e8400-e29b-41d4-a716-446655440000";
const TEXT: Record<number, string> = {
  1: "title\nold line\nshared\n",
  2: "title\nnew line\nshared\n",
  3: "title\nold line\nshared\n",
};

interface Store {
  readonly authors: Record<number, ArtifactAuthor>;
  readonly sizes: Record<number, number>;
  /** Revision content — the shared fixture, with per-test overrides applied. */
  readonly texts: Record<number, string>;
  head: number;
}

function makeRevision(store: Store, number: number): ArtifactRevision {
  return {
    artifact_id: ARTIFACT_ID,
    revision: number,
    ...(number > 1 ? { parent_revision: number - 1 } : {}),
    content_ref: ArtifactContentRefCodec.format(ARTIFACT_ID, number),
    content_digest: String(number % 10).repeat(64),
    byte_size: store.sizes[number] ?? store.texts[number]?.length ?? 0,
    author: store.authors[number] ?? "user",
    source_ref: "message://source",
    created_at: "2026-01-01T00:00:00Z",
  };
}

function makeTransport(store: Store): {
  readonly client: ArtifactCapableTransport;
  readonly createRevision: ReturnType<typeof vi.fn>;
} {
  const detailFor = (): ArtifactDetailResponse => ({
    artifact: {
      artifact_id: ARTIFACT_ID,
      org_id: "org_test",
      user_id: "user_test",
      conversation_id: "conv_test",
      run_id: "run_test",
      kind: "code",
      title: "example",
      media_type: "text/plain",
      current_revision: store.head,
      created_by: "user",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
    current_revision: makeRevision(store, store.head),
    suggested_filename: "example.txt",
    range_supported: false,
  });
  const request = async <TRes,>(request: TypedRequest): Promise<TRes> => {
    const match = /\/revisions\/(\d+)$/.exec(request.path);
    if (match === null) return detailFor() as TRes;
    return {
      revision: makeRevision(store, Number(match[1])),
      range_supported: false,
    } as TRes;
  };
  const createRevision = vi.fn(async (): Promise<ArtifactMutationResponse> => {
    store.head = 3;
    const detail = detailFor();
    return {
      ...detail,
      current_revision: detail.current_revision,
      replayed: false,
    };
  });
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
      const text = store.texts[number] ?? "";
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

function store(
  authors: Record<number, ArtifactAuthor>,
  sizes: Record<number, number> = {},
  texts: Record<number, string> = {},
): Store {
  return { authors, sizes, texts: { ...TEXT, ...texts }, head: 1 };
}

/** Mounts at r1, then lands r2 the way the host does — a new tab uri. */
async function landRevisionTwo(
  state: Store,
  client: ArtifactCapableTransport,
): Promise<void> {
  const view = render(
    <ArtifactSurface
      uri={`artifact-code://${ARTIFACT_ID}@1`}
      transport={client}
    />,
  );
  await screen.findByTestId("artifact-frame");
  await waitFor(() =>
    expect(screen.getByTestId("artifact-revision-history")).toHaveTextContent(
      "1 loaded",
    ),
  );
  state.head = 2;
  view.rerender(
    <ArtifactSurface
      uri={`artifact-code://${ARTIFACT_ID}@2`}
      transport={client}
    />,
  );
}

/** The revision the surface header says is on screen. */
function shownRevision(): string {
  return screen.getByText(/^r\d+ · /).textContent?.split(" ")[0] ?? "";
}

describe("ArtifactSurface — agent revision review (PRD-03)", () => {
  it("raises the r1→r2 diff when a model revision lands, rather than swapping content silently", async () => {
    const state = store({ 1: "user", 2: "model" });
    const { client } = makeTransport(state);

    await landRevisionTwo(state, client);

    const review = await screen.findByTestId("artifact-revision-review");
    expect(review).toHaveTextContent(
      "The model revised this artifact: r1 → r2",
    );
    const details = await screen.findByLabelText("Revision change details");
    // The change itself is on screen — what left and what arrived — not just
    // the new bytes.
    expect(
      details.querySelector("[data-testid='diff-delete']"),
    ).toHaveTextContent("old");
    expect(
      details.querySelector("[data-testid='diff-insert']"),
    ).toHaveTextContent("new");
    expect(review).toHaveTextContent("1 removed line; 1 added line");
    expect(
      screen.getByRole("button", { name: "Keep this revision" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Revert to r1" }),
    ).toBeInTheDocument();
  });

  it("names a subagent revision as one", async () => {
    const state = store({ 1: "user", 2: "subagent" });
    const { client } = makeTransport(state);

    await landRevisionTwo(state, client);

    expect(
      await screen.findByTestId("artifact-revision-review"),
    ).toHaveTextContent("A subagent revised this artifact: r1 → r2");
  });

  it("keeps the revision and appends nothing when the reader keeps it", async () => {
    const state = store({ 1: "user", 2: "model" });
    const { client, createRevision } = makeTransport(state);

    await landRevisionTwo(state, client);
    fireEvent.click(
      await screen.findByRole("button", { name: "Keep this revision" }),
    );

    await waitFor(() =>
      expect(screen.queryByTestId("artifact-revision-review")).toBeNull(),
    );
    expect(createRevision).not.toHaveBeenCalled();
    // The kept revision is still the one on screen, and no revision followed it.
    expect(shownRevision()).toBe("r2");
    expect(screen.getByTestId("artifact-revision-history")).toHaveTextContent(
      "2 loaded",
    );
  });

  it("appends a further revision equal to the parent when the reader reverts, leaving every revision retrievable", async () => {
    const state = store({ 1: "user", 2: "model", 3: "user" });
    const { client, createRevision } = makeTransport(state);

    await landRevisionTwo(state, client);
    fireEvent.click(
      await screen.findByRole("button", { name: "Revert to r1" }),
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
    // Appended on top of the reviewed revision, carrying the parent's bytes —
    // r2 is superseded, never rewritten.
    expect(request).toMatchObject({
      artifactId: ARTIFACT_ID,
      parentRevision: 2,
    });
    // NOT the parent revision's digest. The server hashes the incoming bytes,
    // so sending r2's digest here failed every write with a 422.
    expect(request.expectedDigest).not.toBe("2".repeat(64));
    expect(Array.from(request.content)).toEqual(
      Array.from(new TextEncoder().encode(TEXT[1]!)),
    );

    // r1, r2 and r3 all remain retrievable from history, and the review closes
    // once the appended revision is the one on screen.
    await waitFor(() =>
      expect(screen.getByTestId("artifact-revision-history")).toHaveTextContent(
        "3 loaded",
      ),
    );
    const history = screen.getByTestId("artifact-revision-history");
    for (const label of ["r1", "r2", "r3"]) {
      expect(
        screen.getByRole("button", { name: label, pressed: label === "r3" }),
      ).toBeInTheDocument();
    }
    expect(history).toHaveTextContent("model");
    expect(screen.queryByTestId("artifact-revision-review")).toBeNull();
  });

  it("raises no review for a user-authored revision", async () => {
    const state = store({ 1: "user", 2: "user" });
    const { client } = makeTransport(state);

    await landRevisionTwo(state, client);

    // Wait for the new revision to be on screen, then assert the absence.
    await waitFor(() => expect(shownRevision()).toBe("r2"));
    expect(screen.queryByTestId("artifact-revision-review")).toBeNull();
  });

  it("raises no review when the reader navigates to the model revision themselves", async () => {
    const state = store({ 1: "user", 2: "model" });
    state.head = 2;
    const { client } = makeTransport(state);
    render(
      <ArtifactSurface
        uri={`artifact-code://${ARTIFACT_ID}@1`}
        transport={client}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("artifact-revision-history")).toHaveTextContent(
        "2 loaded",
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "r2" }));

    await waitFor(() => expect(shownRevision()).toBe("r2"));
    expect(screen.queryByTestId("artifact-revision-review")).toBeNull();
  });

  it("raises no review for a landed revision that a newer head has already superseded", async () => {
    // The comparison is always base→head, so a landed revision that is not the
    // head has no honest r(n-1)→r(n) reading: labelling an r1→r3 diff "r1 → r2"
    // would be a lie, and Revert would aim at the wrong parent.
    const state = store({ 1: "user", 2: "model", 3: "model" });
    const { client } = makeTransport(state);
    const view = render(
      <ArtifactSurface
        uri={`artifact-code://${ARTIFACT_ID}@1`}
        transport={client}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("artifact-revision-history")).toHaveTextContent(
        "1 loaded",
      ),
    );

    state.head = 3;
    view.rerender(
      <ArtifactSurface
        uri={`artifact-code://${ARTIFACT_ID}@2`}
        transport={client}
      />,
    );

    await waitFor(() => expect(shownRevision()).toBe("r2"));
    expect(screen.queryByTestId("artifact-revision-review")).toBeNull();
  });

  it("raises no review when the reader opens an artifact already sitting at a model revision", async () => {
    // First paint is not an arrival: nothing was swapped underneath the reader,
    // they asked for this revision. Without the guard the panel would announce
    // a change against a revision that was never on screen.
    const state = store({ 1: "user", 2: "model" });
    state.head = 2;
    const { client } = makeTransport(state);
    render(
      <ArtifactSurface
        uri={`artifact-code://${ARTIFACT_ID}@2`}
        transport={client}
      />,
    );

    await waitFor(() => expect(shownRevision()).toBe("r2"));
    await waitFor(() =>
      expect(screen.getByTestId("artifact-revision-history")).toHaveTextContent(
        "2 loaded",
      ),
    );
    expect(screen.queryByTestId("artifact-revision-review")).toBeNull();
  });

  it("raises the r1→r3 review when a turn writes two revisions and the head skips past the one on screen", async () => {
    // The multi-revision jump. `parent_revision` (2) is not the revision on
    // screen (1), so keying the rule on the direct child let this land
    // silently — the defect the review exists to prevent. The base is the
    // revision that WAS on screen, at whatever distance.
    const state = store(
      { 1: "user", 2: "model", 3: "model" },
      {},
      { 3: "title\njumped line\nshared\n" },
    );
    const { client } = makeTransport(state);
    const view = render(
      <ArtifactSurface
        uri={`artifact-code://${ARTIFACT_ID}@1`}
        transport={client}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("artifact-revision-history")).toHaveTextContent(
        "1 loaded",
      ),
    );

    state.head = 3;
    view.rerender(
      <ArtifactSurface
        uri={`artifact-code://${ARTIFACT_ID}@3`}
        transport={client}
      />,
    );

    const review = await screen.findByTestId("artifact-revision-review");
    expect(review).toHaveTextContent(
      "The model revised this artifact: r1 → r3",
    );
    // Diffed against r1 — what the reader actually had — not against r2, which
    // they never saw.
    const details = await screen.findByLabelText("Revision change details");
    expect(
      details.querySelector("[data-testid='diff-delete']"),
    ).toHaveTextContent("old");
    expect(
      details.querySelector("[data-testid='diff-insert']"),
    ).toHaveTextContent("jumped");
    expect(
      screen.getByRole("button", { name: "Revert to r1" }),
    ).toBeInTheDocument();
  });

  it("leaves the existing too_large restore path in place for an oversized parent", async () => {
    // Over the 10 MiB code restore limit, so the bounded restore refuses before
    // allocating, and over the 5 MiB preview limit, so the diff is unavailable.
    const state = store({ 1: "user", 2: "model" }, { 1: 12 * 1024 * 1024 });
    const { client, createRevision } = makeTransport(state);

    await landRevisionTwo(state, client);
    const review = await screen.findByTestId("artifact-revision-review");
    await waitFor(() =>
      expect(review).toHaveTextContent(
        "This change cannot be shown as bounded UTF-8 text",
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "Revert to r1" }));

    expect(
      await screen.findByText(/too large for a bounded in-browser restore/),
    ).toBeInTheDocument();
    expect(createRevision).not.toHaveBeenCalled();
    // Nothing was appended, so the reviewed revision is still on screen.
    expect(screen.getByTestId("artifact-revision-review")).toBeInTheDocument();
  });
});
