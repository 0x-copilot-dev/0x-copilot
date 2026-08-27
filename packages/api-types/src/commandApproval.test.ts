// The command-execution lane's slice of this package (PRD-shell-execution §6,
// §14.1, §15.1). Phase 0 only: the server does not emit these fields yet and no
// tool is model-visible, so everything asserted here is a contract shape rather
// than a behaviour.
//
// What is worth asserting in a type-only package is the part that fails
// SILENTLY. `RuntimeApiEventType` is a closed union backing
// `isRuntimeApiEventType` → `isRuntimeEventEnvelope`, and every client drops an
// envelope whose type is not in that union — no error, no warning, no trace at
// any layer. That is exactly why the PRD forbids a bespoke `api_event_type` for
// the command approval and routes it over `approval_requested` with the
// discrimination carried in `approval_kind`; the same failure on the producer
// side is recorded in `capabilities/desktop/workspace_grant.py:83-101`, where a
// custom event name meant no event, no approval record, and a run parked on an
// interrupt the client was never told about.
//
// ⚠️ The TYPE-level pins below are not covered by CI. `tsconfig.json` excludes
// `src/**/*.test.ts` and vitest transpiles without checking, so an annotation
// here fails a local `tsc` and a review, not `npm run typecheck`. The runtime
// assertions are the half CI actually executes — which is why each test drives a
// real guard instead of only declaring a fixture.

import { describe, expect, it } from "vitest";

import {
  isApprovalRequestedPayload,
  isRuntimeApiEventType,
  isRuntimeEventEnvelope,
} from "./index";
import type {
  ApprovalId,
  ApprovalRequestedPayload,
  StructuredRuntimeEventEnvelope,
  ToolPolicyKind,
  ToolUsePolicyEntry,
  ToolUsePolicyResponse,
  UpdateToolUsePolicyRequest,
} from "./index";

/**
 * The payload as the `ask_a_question` projection will hand it over
 * (`runtime_api/schemas/events.py:2669-2736`).
 *
 * `op_class: "execute"` and `risk_level: "high"` are two knobs on two sides of
 * the wire answering two different questions — may this decision cover more
 * than this call (server), and may this be approved from the collapsed card
 * (client). Reaching for `op_class: "destructive"` to get the second would cost
 * the first: `ToolAccessGate._grant_options` withholds `allow_always` for
 * `destructive` alone, so every command card would ship `["allow_once"]` and no
 * client change could draw the "Allow for this run" control back.
 */
const commandApprovalPayload = (): Record<string, unknown> => ({
  approval_id: "appr_cmd_1",
  approval_kind: "ask_a_question",
  op_class: "execute",
  risk_level: "high",
  grant_options: ["allow_once", "allow_always"],
  command: "pytest -q",
  workspace_label: "my-project",
});

const commandApprovalEnvelope = (): Record<string, unknown> => ({
  event_id: "evt_cmd_1",
  run_id: "run_1",
  conversation_id: "conv_1",
  sequence_no: 7,
  event_type: "approval_requested",
  source: "runtime",
  activity_kind: "approval",
  payload: commandApprovalPayload(),
  metadata: {},
  created_at: "2026-08-27T00:00:00Z",
});

describe("command approval over the runtime event transport", () => {
  it("rides `approval_requested`, because a lane-specific event type is dropped", () => {
    expect(isRuntimeApiEventType("approval_requested")).toBe(true);
    // The name a bespoke lane would plausibly have picked. It is not in the
    // closed union, so `isRuntimeEventEnvelope` rejects the frame and the card
    // never renders — the failure this whole indirection exists to avoid.
    expect(isRuntimeApiEventType("command_approval_requested")).toBe(false);
    expect(isRuntimeEventEnvelope(commandApprovalEnvelope())).toBe(true);
  });

  it("carries the verbatim command and the grant label through the approval guard", () => {
    const payload: unknown = commandApprovalPayload();
    if (!isApprovalRequestedPayload(payload)) {
      throw new Error("a command approval must satisfy the approval guard");
    }
    // Assigning to `string` is what makes these real pins. `ApprovalRequestedPayload`
    // carries an index signature, so a field DROPPED from the interface still
    // reads at runtime — it just types as `unknown`, and every typed consumer
    // goes quiet instead of failing. `unknown` is not assignable to `string`,
    // so the drop breaks here rather than in someone's blank approval card.
    const commandText: string = payload.command ?? "";
    const workspaceLabel: string = payload.workspace_label ?? "";
    expect(commandText).toBe("pytest -q");
    expect(workspaceLabel).toBe("my-project");
    expect(payload.op_class).toBe("execute");
    expect(payload.risk_level).toBe("high");
  });

  it("indexes the typed envelope without losing the two new fields", () => {
    // `StructuredRuntimeEventEnvelope` indexes `RuntimeEventPayloadByType` by
    // event type; a payload key that is not on the mapped interface is a
    // compile error at exactly this site, not at the producer.
    const typed: StructuredRuntimeEventEnvelope<"approval_requested"> = {
      event_id: "evt_cmd_1",
      run_id: "run_1",
      conversation_id: "conv_1",
      sequence_no: 7,
      event_type: "approval_requested",
      activity_kind: "approval",
      created_at: "2026-08-27T00:00:00Z",
      payload: {
        approval_id: "appr_cmd_1" as ApprovalId,
        approval_kind: "ask_a_question",
        op_class: "execute",
        risk_level: "high",
        command: "pytest -q",
        workspace_label: "my-project",
      },
    };

    expect(typed.payload.command).toBe("pytest -q");
    expect(typed.payload.workspace_label).toBe("my-project");
  });

  it("keeps both fields optional, so every other approval lane is untouched", () => {
    const mcpApproval: ApprovalRequestedPayload = {
      approval_id: "appr_mcp_1" as ApprovalId,
      approval_kind: "mcp_tool",
      tool_name: "call_mcp_tool",
      op_class: "write",
    };

    expect(mcpApproval.command).toBeUndefined();
    expect(mcpApproval.workspace_label).toBeUndefined();
  });
});

describe("the EXECUTE policy axis", () => {
  it("is a fourth axis rather than a reuse of an existing one", () => {
    const axes = [
      "read",
      "write",
      "destructive",
      "execute",
    ] as const satisfies readonly ToolPolicyKind[];

    // `write` is disqualified outright, not merely inelegant: it AUTO-RUNS
    // under the BYPASS posture, so a command classed `write` would make the
    // composer's bypass pill turn on unattended command execution. `destructive`
    // survives BYPASS but makes one knob answer two questions — blocking
    // connector deletions would also block `npm test`.
    expect(axes).toContain("execute");
    expect(new Set(axes).size).toBe(axes.length);
  });

  it("carries `ask` as the deployment default, on both the read and write shape", () => {
    const entry: ToolUsePolicyEntry = {
      kind: "execute",
      // Not `block`: the tool is already off by default per workspace grant, and
      // a second off-switch is the one nobody finds (§6).
      mode: "ask",
      updated_at: "2026-08-27T00:00:00Z",
      updated_by_user_id: null,
    };
    const update: UpdateToolUsePolicyRequest = {
      policies: [{ kind: entry.kind, mode: entry.mode }],
    };

    expect(entry.mode).toBe("ask");
    expect(update.policies[0]?.kind).toBe("execute");
  });

  it("is ignorable by a client that has not learned it", () => {
    // SPEC.md's rollout rule — an enum addition needs a UI fallback before it
    // ships. The settings adapter resolves its three axes by NAME out of the
    // `policies` list, so a fourth row is inert rather than a crash or a
    // mis-indexed axis. Asserted here because the response is a list and a
    // reader that treated it as a positional tuple would read `execute`'s mode
    // as `destructive`'s.
    const response: ToolUsePolicyResponse = {
      scope: "user",
      org_id: "org_1",
      user_id: "user_1",
      policies: [
        {
          kind: "execute",
          mode: "ask",
          updated_at: "2026-08-27T00:00:00Z",
          updated_by_user_id: null,
        },
        {
          kind: "destructive",
          mode: "require",
          updated_at: "2026-08-27T00:00:00Z",
          updated_by_user_id: null,
        },
      ],
    };

    const destructive = response.policies.find(
      (policy) => policy.kind === "destructive",
    );
    expect(destructive?.mode).toBe("require");
  });
});
