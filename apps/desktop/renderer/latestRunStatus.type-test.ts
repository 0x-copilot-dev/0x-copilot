// PRD-05 — desktop twin of the compile-time guard (see the frontend file).
// `Conversation.latest_run_status` is narrowed to `ActiveAgentRunStatus`, so a
// terminal value like "completed" is a type error. `@ts-expect-error` inverts
// the guard: this file's typecheck FAILS if the narrowing is reverted. Included
// in the desktop typecheck graph via apps/desktop/tsconfig.json
// `include: ["renderer/**/*.ts"]`.

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
