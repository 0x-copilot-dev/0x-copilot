// Brands are compile-time-only — there is no runtime to assert. These tests
// exist so the file participates in `npm test` (when api-types ever runs
// vitest) AND so the type assertions below break the build if a brand
// regresses to plain `string` or accidentally collapses with another brand.
//
// Currently api-types has no `test` script (typecheck-only), so this file
// is checked by tsc through the chat-surface workspace import graph; it is
// also runnable by any future api-types vitest setup.

import type { ApprovalId, ConversationId, RunId, TodoId } from "./brands";

// Smoke: any plain `string` is NOT assignable to a brand. The expectation
// here is encoded as a `@ts-expect-error` so that REMOVING the brand
// (making the type a plain string) is a compile error — i.e., a forced-fail
// regression alarm.
//
// We don't actually run this; it lives at module scope so tsc evaluates it
// during typecheck.
{
  const plain: string = "anything";
  // @ts-expect-error — plain string must not assign to ConversationId
  const _conv: ConversationId = plain;
  // @ts-expect-error — plain string must not assign to RunId
  const _run: RunId = plain;
  // @ts-expect-error — plain string must not assign to TodoId
  const _todo: TodoId = plain;
  // @ts-expect-error — plain string must not assign to ApprovalId
  const _approval: ApprovalId = plain;

  // Cross-brand assignment must also fail.
  const aConv = "conv_001" as ConversationId;
  // @ts-expect-error — ConversationId is not assignable to RunId
  const _r: RunId = aConv;

  // Reading the value as a string IS allowed (brands are strings at
  // runtime; that's the whole point).
  const asString: string = aConv;
  void asString;
  void _conv;
  void _run;
  void _todo;
  void _approval;
  void _r;
}
