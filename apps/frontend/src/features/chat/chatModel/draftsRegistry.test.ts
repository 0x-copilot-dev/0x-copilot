import { describe, expect, it } from "vitest";
import type {
  Draft,
  DraftStatus,
  DraftUpdatedPayload,
  RuntimeEventEnvelope,
} from "@enterprise-search/api-types";
import {
  applyDraftUpdatedEvent,
  draftsByCreatedAt,
  draftsForConversation,
  emptyDraftRegistry,
  seedDrafts,
  upsertDraft,
} from "./draftsRegistry";

const DRAFT_ID = "deadbeefcafe1234deadbeefcafe1234";
const CONVERSATION_ID = "conv_1";

function draft(overrides: Partial<Draft> = {}): Draft {
  return {
    draft_id: DRAFT_ID,
    version: 1,
    conversation_id: CONVERSATION_ID,
    run_id: "run_1",
    user_id: "user_sarah",
    title: "Aurora 4.0",
    content_text: "# Aurora 4.0\n\nLaunch announcement.",
    sections: [{ heading: "Aurora 4.0", body: "Launch announcement." }],
    target_connector: null,
    target_metadata: null,
    citation_ids: [],
    status: "draft" as DraftStatus,
    created_at: "2026-05-04T12:00:00Z",
    ...overrides,
  };
}

function draftEvent(
  payload: Partial<DraftUpdatedPayload> = {},
  overrides: Partial<RuntimeEventEnvelope> = {},
): RuntimeEventEnvelope {
  const fullPayload: DraftUpdatedPayload = {
    draft_id: DRAFT_ID,
    version: 1,
    status: "draft",
    title: "Aurora 4.0",
    sections: [{ heading: "Aurora 4.0", body: "Launch announcement." }],
    target_connector: null,
    target_metadata: null,
    citation_ids: [],
    summary: "Draft v1: Aurora 4.0",
    ...payload,
  };
  return {
    event_id: "evt_1",
    run_id: "run_1",
    conversation_id: CONVERSATION_ID,
    org_id: "org_acme",
    sequence_no: 1,
    event_type: "draft_updated",
    source: "runtime",
    activity_kind: "draft",
    visibility: "user",
    redaction_state: "redacted",
    created_at: "2026-05-04T12:00:00Z",
    payload: fullPayload as unknown as RuntimeEventEnvelope["payload"],
    metadata: {},
    span_id: null,
    parent_span_id: null,
    parent_event_id: null,
    parent_task_id: null,
    task_id: null,
    subagent_id: null,
    display_title: "Draft v1: Aurora 4.0",
    summary: "Draft v1: Aurora 4.0",
    status: "completed",
    presentation: null,
    event_protocol_version: 1,
    ...overrides,
  } as RuntimeEventEnvelope;
}

describe("draftsRegistry", () => {
  it("seeds drafts for a conversation", () => {
    const next = seedDrafts(emptyDraftRegistry(), CONVERSATION_ID, [draft()]);
    const map = draftsForConversation(next, CONVERSATION_ID);
    expect(map.size).toBe(1);
    expect(map.get(DRAFT_ID)?.version).toBe(1);
  });

  it("upsertDraft replaces only when the new version is higher", () => {
    let registry = upsertDraft(emptyDraftRegistry(), draft({ version: 2 }));
    registry = upsertDraft(registry, draft({ version: 1 }));
    const latest = draftsForConversation(registry, CONVERSATION_ID).get(
      DRAFT_ID,
    );
    expect(latest?.version).toBe(2);
  });

  it("applyDraftUpdatedEvent inserts the first version", () => {
    const next = applyDraftUpdatedEvent(emptyDraftRegistry(), draftEvent());
    expect(
      draftsForConversation(next, CONVERSATION_ID).get(DRAFT_ID)?.version,
    ).toBe(1);
  });

  it("higher version overwrites lower version", () => {
    let registry = applyDraftUpdatedEvent(
      emptyDraftRegistry(),
      draftEvent({ version: 1 }),
    );
    registry = applyDraftUpdatedEvent(registry, draftEvent({ version: 2 }));
    expect(
      draftsForConversation(registry, CONVERSATION_ID).get(DRAFT_ID)?.version,
    ).toBe(2);
  });

  it("older version is a no-op (replay/SSE-resume idempotent)", () => {
    let registry = applyDraftUpdatedEvent(
      emptyDraftRegistry(),
      draftEvent({ version: 2 }),
    );
    registry = applyDraftUpdatedEvent(registry, draftEvent({ version: 1 }));
    expect(
      draftsForConversation(registry, CONVERSATION_ID).get(DRAFT_ID)?.version,
    ).toBe(2);
  });

  it("ignores events that are not draft_updated", () => {
    const event = draftEvent({}, { event_type: "model_delta" });
    const next = applyDraftUpdatedEvent(emptyDraftRegistry(), event);
    expect(next.size).toBe(0);
  });

  it("ignores draft_updated with malformed payload", () => {
    const event = draftEvent(
      {},
      {
        payload: {
          not: "a draft",
        } as unknown as RuntimeEventEnvelope["payload"],
      },
    );
    const next = applyDraftUpdatedEvent(emptyDraftRegistry(), event);
    expect(next.size).toBe(0);
  });

  it("draftsByCreatedAt returns deterministic order", () => {
    const a = draft({
      draft_id: "a".repeat(32),
      created_at: "2026-05-04T10:00:00Z",
    });
    const b = draft({
      draft_id: "b".repeat(32),
      created_at: "2026-05-04T11:00:00Z",
    });
    let registry = upsertDraft(emptyDraftRegistry(), b);
    registry = upsertDraft(registry, a);
    const ordered = draftsByCreatedAt(
      draftsForConversation(registry, CONVERSATION_ID),
    );
    expect(ordered.map((d) => d.draft_id)).toEqual([a.draft_id, b.draft_id]);
  });
});
