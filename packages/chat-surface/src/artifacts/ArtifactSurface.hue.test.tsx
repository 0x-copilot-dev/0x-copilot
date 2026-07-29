// One artifact's identity, shown twice.
//
// `publish_artifact`'s `accent` reaches the canvas tab through `TcTab.hue`, and
// reaches the card through `ArtifactSurface` → `TcSurfaceMount`. Those are two
// renders of ONE fact, so the tests here render both from the same artifact and
// the same chosen accent and assert they agree. The divergence this seam exists
// to prevent is a card that keeps the URI-derived default while its tab shows
// the author's choice — which no "the prop was passed" assertion catches.

import { render, screen } from "@testing-library/react";
import {
  ArtifactContentRefCodec,
  type ArtifactDetailResponse,
  type ArtifactKind,
  type ArtifactRevision,
} from "@0x-copilot/api-types";
import type {
  ArtifactCapableTransport,
  TypedRequest,
} from "@0x-copilot/chat-transport";
import { describe, expect, it } from "vitest";

import { TcTabs } from "../thread-canvas/TcTabs";
import { ArtifactSurface } from "./ArtifactSurface";
import { artifactUri } from "./uri";

const ARTIFACT_ID = "art_550e8400-e29b-41d4-a716-446655440000";
const CONTENT = "region,forecast\nEMEA,412\n";

function revision(): ArtifactRevision {
  return {
    artifact_id: ARTIFACT_ID,
    revision: 1,
    content_ref: ArtifactContentRefCodec.format(ARTIFACT_ID, 1),
    content_digest: "1".repeat(64),
    byte_size: CONTENT.length,
    author: "model",
    source_ref: "message://source",
    created_at: "2026-01-01T00:00:00Z",
  };
}

function makeTransport(kind: ArtifactKind): ArtifactCapableTransport {
  const head = revision();
  const detail: ArtifactDetailResponse = {
    artifact: {
      artifact_id: ARTIFACT_ID,
      org_id: "org_test",
      user_id: "user_test",
      conversation_id: "conv_test",
      run_id: "run_test",
      kind,
      title: "forecast",
      media_type: "text/csv",
      current_revision: 1,
      created_by: "model",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
    current_revision: head,
    suggested_filename: "forecast.csv",
    range_supported: false,
  };
  return {
    request: async <TRes,>(request: TypedRequest): Promise<TRes> =>
      (/\/revisions\/\d+$/.test(request.path)
        ? { revision: head, range_supported: false }
        : detail) as TRes,
    subscribeServerSentEvents: () => ({ close: () => {} }),
    getSession: () => ({ bearer: null }),
    capabilities: () => ({
      substrate: "web",
      nativeSecretStorage: false,
      fileSystemAccess: false,
      clipboardWrite: false,
      openExternal: false,
    }),
    getArtifactContent: async () => ({
      body: new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(CONTENT));
          controller.close();
        },
      }),
      contentType: "text/csv",
      contentLength: CONTENT.length,
      etag: '"1"',
      filename: "forecast.csv",
    }),
    createArtifactRevision: async () => {
      throw new Error("this test never writes a revision");
    },
  };
}

/**
 * Render the artifact's tab and the artifact's surface from the same record and
 * the same accent, the way the Run cockpit composes them, and report what each
 * one claims the surface's source is.
 */
async function huesFor(
  kind: ArtifactKind,
  accent?: string,
): Promise<{ readonly tab: string | null; readonly mount: string | null }> {
  const uri = artifactUri(kind, ARTIFACT_ID, 1);
  const choice = accent === undefined ? {} : { hue: accent };
  render(
    <>
      <TcTabs
        tabs={[{ uri, title: "forecast · r1", ...choice }]}
        activeUri={uri}
        onActivate={() => {}}
        onClose={() => {}}
      />
      <ArtifactSurface uri={uri} transport={makeTransport(kind)} {...choice} />
    </>,
  );
  // The card mounts only once the artifact record has loaded — the same record
  // the caller read the accent from.
  const mount = await screen.findByTestId("tc-surface-mount");
  return {
    tab: screen.getByRole("tab").getAttribute("data-surface-hue"),
    mount: mount.getAttribute("data-surface-hue"),
  };
}

describe("ArtifactSurface source hue", () => {
  it("gives the card the accent the author chose, matching its tab", async () => {
    const { tab, mount } = await huesFor("dataset", "ember");
    // Equality is the requirement; the pinned value stops it from holding
    // vacuously because both sides dropped the choice.
    expect(mount).toBe(tab);
    expect(mount).toBe("ember");
  });

  it("derives the same hue from the artifact kind when no accent was chosen", async () => {
    const { tab, mount } = await huesFor("dataset");
    expect(mount).toBe(tab);
    expect(mount).toBe("sky");
  });

  // An accent is a model-authored argument, so a malformed one is a reachable
  // case. Both halves must degrade the same way — to the kind's own hue, never
  // to a blank identity and never to the raw string reaching an attribute.
  it("degrades a malformed accent identically on tab and card", async () => {
    const hostile = "sky; background: url(x)";
    const { tab, mount } = await huesFor("dataset", hostile);
    expect(mount).toBe(tab);
    expect(mount).toBe("sky");
    expect(mount).not.toBe(hostile);
  });

  // A file artifact is opaque bytes with no implied identity. It is where a
  // chosen accent matters most, and where a dropped one would be invisible to a
  // test that only ever checked the URI-derived default.
  it("shows no identity for a file artifact when nothing was chosen", async () => {
    const { tab, mount } = await huesFor("file");
    expect(mount).toBe(tab);
    expect(mount).toBe("none");
  });

  it("carries a chosen accent for a file artifact, whose default is none", async () => {
    const { tab, mount } = await huesFor("file", "amber");
    expect(mount).toBe(tab);
    expect(mount).toBe("amber");
  });
});
