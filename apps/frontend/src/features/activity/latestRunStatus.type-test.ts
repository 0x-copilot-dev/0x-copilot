// PRD-05 — compile-time guard that the false-contract fixture can no longer be
// written. `Conversation.latest_run_status` is narrowed to `ActiveAgentRunStatus`
// (the four non-terminal statuses the server can actually project), so assigning
// a terminal value like "completed" is a type error.
//
// `@ts-expect-error` is itself an error if the next line compiles cleanly, so
// this file's typecheck FAILS if the narrowing is ever reverted — the guard is
// self-inverting and needs no test runner. Included in the typecheck graph via
// apps/frontend/tsconfig.json `include: ["src"]`.

import type { Conversation } from "@0x-copilot/api-types";

export const terminalStatusIsNotEmittable: Conversation = {
  conversation_id: "c",
  org_id: "o",
  user_id: "u",
  assistant_id: "a",
  title: null,
  status: "active",
  created_at: "2026-07-23T00:00:00Z",
  updated_at: "2026-07-23T00:00:00Z",
  archived_at: null,
  metadata: {},
  schema_version: 1,
  // @ts-expect-error terminal status is not emittable in latest_run_status
  latest_run_status: "completed",
};
