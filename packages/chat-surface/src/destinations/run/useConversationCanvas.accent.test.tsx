// The accent must survive the live/archived merge.
//
// The defect this covers: `RunDestination` rebuilds every artifact the CURRENT
// run's lifecycle fold saw as a `ConversationCanvasSubject` with a hardcoded
// `accent: null`, because `artifact.created` carries no accent on the wire. The
// merge then replaced the whole archived subject with the live one, so the
// stored accent was erased the moment the publishing run's own events were
// folded — i.e. in the ordinary case. The author-chosen colour was visible only
// from a DIFFERENT run in the same conversation, which is the opposite of what
// anyone would test by hand.
//
// The general rule this encodes: live wins for facts the fold can observe; it
// must not win for facts the fold provably cannot know, where its value is
// silence rather than a choice.

import { renderHook, waitFor } from "@testing-library/react";
import { type ReactElement, type ReactNode } from "react";
import { describe, expect, it } from "vitest";

import type { ConversationId } from "@0x-copilot/api-types";
import type { Transport } from "@0x-copilot/chat-transport";

import { TransportProvider } from "../../providers/TransportProvider";
import {
  useConversationCanvas,
  type ConversationCanvasSubject,
} from "./useConversationCanvas";

const CONVERSATION = "conv-1" as ConversationId;
const KEY = "artifact:art_forecast";

/** The archived record — the ONLY place a chosen accent can come from. */
const wireSubject = (accent: string | null, title = "Q3 forecast") => ({
  subject_key: KEY,
  kind: "artifact",
  subject_id: "art_forecast",
  run_id: "run-1",
  title,
  revision: 1,
  renderer_hint: "artifact-dataset",
  accent,
  created_at: "2026-07-29T00:00:00Z",
});

/**
 * The live subject exactly as `RunDestination` builds it from the run fold:
 * a real title, and `accent: null` because the ledger event has no accent.
 */
const liveSubject: ConversationCanvasSubject = {
  subjectKey: KEY,
  kind: "artifact",
  subjectId: "art_forecast",
  runId: "run-1",
  title: "dataset artifact",
  revision: 1,
  rendererHint: "artifact-dataset",
  accent: null,
  createdAt: "",
};

function harness(
  accent: string | null,
  title?: string,
): (p: { children: ReactNode }) => ReactElement {
  const transport = {
    request: async () => ({ subjects: [wireSubject(accent, title)] }),
  } as unknown as Transport;
  return ({ children }) => (
    <TransportProvider transport={transport}>{children}</TransportProvider>
  );
}

/**
 * Mount the hook and poll until `expectation` holds.
 *
 * The expectation IS the wait condition, deliberately. Waiting on
 * `subjects.length === 1` instead lets the poll settle the moment the archived
 * fetch resolves, which can be a render before the live subjects have merged —
 * so the assertion then samples an intermediate state and the test fails at
 * random under load. Polling on the thing being asserted has no such window.
 */
async function expectMerged(
  storedAccent: string | null,
  live: readonly ConversationCanvasSubject[],
  expectation: (subject: ConversationCanvasSubject) => void,
): Promise<void> {
  const { result } = renderHook(
    () => useConversationCanvas(CONVERSATION, live, true),
    { wrapper: harness(storedAccent) },
  );
  await waitFor(() => {
    expect(result.current.loading).toBe(false);
    expect(result.current.subjects).toHaveLength(1);
    expectation(result.current.subjects[0]!);
  });
}

describe("useConversationCanvas accent merge", () => {
  it("keeps the stored accent when the run fold has already seen the artifact", async () => {
    // The regression: this returned "sky" (URI-derived) because the live
    // subject's hardcoded null replaced the record's "ember".
    await expectMerged("ember", [liveSubject], (s) =>
      expect(s.accent).toBe("ember"),
    );
  });

  it("keeps the stored accent when no live subject exists yet", async () => {
    await expectMerged("ember", [], (s) => expect(s.accent).toBe("ember"));
  });

  it("still lets the live subject win on facts the fold does observe", async () => {
    // Guard against over-correcting: the fix must preserve accent WITHOUT
    // freezing the rest of the subject, or a revision bump would stop landing.
    await expectMerged("ember", [{ ...liveSubject, revision: 4 }], (s) => {
      expect(s.revision).toBe(4);
      expect(s.accent).toBe("ember");
    });
  });

  it("leaves an unchosen accent null rather than inventing one", async () => {
    await expectMerged(null, [liveSubject], (s) => expect(s.accent).toBeNull());
  });

  // Same root cause, and the one a user can actually see: the tab read
  // "dataset artifact · r1" while the surface header beside it said
  // "forecast.csv". The fold cannot know a title either.
  it("keeps the record's title over the fold's synthesized placeholder", async () => {
    await expectMerged("ember", [liveSubject], (s) =>
      expect(s.title).toBe("Q3 forecast"),
    );
  });

  // A titleless record parses to the placeholder "Untitled", which says less
  // than the fold's "<kind> artifact". The fold wins that one.
  it("prefers the fold's label over the parser's Untitled placeholder", async () => {
    const { result } = renderHook(
      () => useConversationCanvas(CONVERSATION, [liveSubject], true),
      { wrapper: harness(null, "") },
    );
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
      expect(result.current.subjects[0]?.title).toBe("dataset artifact");
    });
  });

  it("lets a live accent win when one is somehow present", async () => {
    // If `artifact.created` ever gains an accent, the fold becomes the fresher
    // source and must not be overridden by a stale archive row.
    await expectMerged("ember", [{ ...liveSubject, accent: "plum" }], (s) =>
      expect(s.accent).toBe("plum"),
    );
  });
});
