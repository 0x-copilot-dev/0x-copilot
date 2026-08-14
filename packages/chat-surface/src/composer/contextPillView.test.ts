import type {
  ConversationContextOccupancyResponse,
  ConversationContextResponse,
  ContextOccupancySegment,
  ContextOccupancySnapshot,
} from "@0x-copilot/api-types";
import { describe, expect, it } from "vitest";

import { buildContextPillView } from "./contextPillView";

// One consistent scenario throughout: Opus 5, a 200k window, Linear + GitHub
// connected, 24 tool results. The finding it exists to surface is that the two
// connectors' schemas are 33,610 RESIDENT tokens — rent charged on every call
// whether or not the model touches them.
function segment(
  over: Partial<ContextOccupancySegment> = {},
): ContextOccupancySegment {
  return {
    segment_class: "tools",
    label: "tools:linear",
    lifecycle: "resident",
    third_party: true,
    detail: "41 tools",
    byte_count: 89_640,
    estimated_tokens: 22_410,
    item_count: 41,
    cache_eligibility: "stable_prefix",
    counter_source: "tokenizer",
    ...over,
  };
}

const SEGMENTS: ContextOccupancySegment[] = [
  segment(),
  segment({
    label: "tools:github",
    detail: "26 tools",
    estimated_tokens: 11_200,
    item_count: 26,
  }),
  segment({
    segment_class: "messages",
    label: "messages:tool_results",
    lifecycle: "per_result",
    third_party: false,
    detail: null,
    estimated_tokens: 21_830,
    item_count: 24,
    cache_eligibility: "never",
    counter_source: "proxy",
  }),
  segment({
    segment_class: "messages",
    label: "messages:transcript",
    lifecycle: "per_turn",
    third_party: false,
    detail: "18 msgs",
    estimated_tokens: 12_300,
    item_count: 18,
    cache_eligibility: "never",
  }),
  segment({
    segment_class: "system",
    label: "system:instructions",
    lifecycle: "resident",
    third_party: false,
    detail: null,
    estimated_tokens: 4_100,
    item_count: 1,
  }),
  segment({
    label: "tools:core",
    detail: "9 tools",
    third_party: false,
    estimated_tokens: 3_860,
    item_count: 9,
  }),
  segment({
    segment_class: "system",
    label: "system:skills",
    lifecycle: "resident",
    third_party: false,
    detail: "6 loaded",
    estimated_tokens: 2_300,
    item_count: 6,
  }),
];

function snapshot(
  over: Partial<ContextOccupancySnapshot> = {},
): ContextOccupancySnapshot {
  return {
    schema_version: 1,
    model_call_id: "call_1",
    attempt_ordinal: 1,
    assembly_record_id: null,
    graph_scope: "root",
    provider: "anthropic",
    model_family: "claude-opus-5",
    measured_at: "2026-08-14T10:00:00Z",
    context_window_tokens: 200_000,
    estimated_input_tokens: 78_000,
    provider_input_tokens: 79_240,
    cached_input_tokens: 21_100,
    cache_creation_input_tokens: 0,
    undeclared_tokens: 0,
    unattributed_delta: 1_240,
    free_tokens: 120_760,
    segments: SEGMENTS,
    unreadable_segment_count: 0,
    ...over,
  };
}

function occupancy(
  over: Partial<ContextOccupancySnapshot> = {},
): ConversationContextOccupancyResponse {
  return {
    conversation_id: "conv_1",
    run_id: "run_1",
    snapshot: snapshot(over),
  };
}

function context(
  over: Partial<ConversationContextResponse["current"]> = {},
): ConversationContextResponse {
  return {
    model: {
      provider: "anthropic",
      name: "Claude Opus 5",
      context_window_tokens: 200_000,
    },
    current: {
      last_run_id: "run_1",
      input_tokens: 79_240,
      output_tokens: 2_100,
      cached_input_tokens: 21_100,
      available_tokens: 120_760,
      headroom_pct: 60,
      ...over,
    },
    breakdown: { by_call: [], by_subagent: [], compression_events: [] },
  };
}

describe("buildContextPillView", () => {
  it("passes headroom_pct through verbatim, never re-deriving it", () => {
    // The server's number is deliberately inconsistent with the token figures
    // here. The contract says render it verbatim, so a view that "corrects" it
    // from available/window would silently disagree with every other surface.
    const view = buildContextPillView({
      context: context({ headroom_pct: 37 }),
      occupancy: occupancy(),
    });
    expect(view?.headroomPct).toBe(37);
  });

  it("takes the PROVIDER token count as the headline, not our estimate", () => {
    // provider_input_tokens is authoritative and matches billing; the segments
    // sum to our estimate, and the difference is reported rather than hidden.
    const view = buildContextPillView({
      context: context(),
      occupancy: occupancy(),
    });
    expect(view?.inputTokens).toBe(79_240);
    expect(view?.unattributedDelta).toBe(1_240);
    const summed = view!.groups
      .flatMap((group) => group.rows)
      .reduce((total, row) => total + row.tokens, 0);
    expect(summed).toBe(78_000);
  });

  it("falls back to our estimate when the provider reported nothing", () => {
    const view = buildContextPillView({
      context: null,
      occupancy: occupancy({ provider_input_tokens: null }),
    });
    expect(view?.inputTokens).toBe(78_000);
  });

  describe("pressure bands", () => {
    it.each([
      [100, "quiet"],
      [40, "quiet"],
      [39, "warm"],
      [15, "warm"],
      [14, "critical"],
      [0, "critical"],
    ])("headroom %i%% reads as %s", (headroom, expected) => {
      const view = buildContextPillView({
        context: context({ headroom_pct: headroom }),
        occupancy: occupancy(),
      });
      expect(view?.pressure).toBe(expected);
    });

    it("is quiet — NOT critical — when the window is unknown", () => {
      // A model absent from the pricing catalogue has no headroom to report.
      // Escalating on missing data would put an ember meter in front of every
      // such user for a condition that is not theirs and not urgent.
      const view = buildContextPillView({
        context: context({ headroom_pct: null, available_tokens: null }),
        occupancy: occupancy({
          context_window_tokens: null,
          free_tokens: null,
        }),
      });
      expect(view?.pressure).toBe("quiet");
      expect(view?.windowTokens).toBeNull();
    });
  });

  it("reports no percentages at all when the window size is unknown", () => {
    // Every share is a fraction of a denominator we do not have. Inventing one
    // is the failure mode this guards.
    const view = buildContextPillView({
      context: context({ headroom_pct: null }),
      occupancy: occupancy({ context_window_tokens: null }),
    });
    expect(view?.slices).toEqual([]);
    const shares = view!.groups
      .flatMap((group) => group.rows)
      .map((row) => row.pctOfWindow);
    expect(shares.length).toBeGreaterThan(0);
    expect(shares.every((share) => share === null)).toBe(true);
  });

  describe("malformed bodies", () => {
    // A meter is not worth crashing a composer over. A 200 carrying a partial
    // or error-shaped body yields a TRUTHY response object whose nested members
    // never arrived, and reading through them threw — which took out the whole
    // composer, send button included. The failure mode has to be a missing
    // meter, never a missing send button.
    it.each([
      ["an empty object", {}],
      ["a body with no model", { current: {}, breakdown: {} }],
      ["a body with no current", { model: {}, breakdown: {} }],
      ["a body with no breakdown", { model: {}, current: {} }],
      ["an error-shaped body", { detail: "not found" }],
    ])("survives %s from /context", (_label, body) => {
      expect(() =>
        buildContextPillView({
          context: body as unknown as ConversationContextResponse,
          occupancy: occupancy(),
        }),
      ).not.toThrow();
    });

    it("survives a snapshot whose segments are not an array", () => {
      const view = buildContextPillView({
        context: context(),
        occupancy: {
          conversation_id: "c",
          run_id: "r",
          snapshot: {
            ...snapshot(),
            segments: undefined as unknown as ContextOccupancySegment[],
          },
        },
      });
      expect(view?.groups).toEqual([]);
      // The totals are STORED, not summed from segments — so the headline
      // survives a decomposition the client could not read.
      expect(view?.inputTokens).toBe(79_240);
    });
  });

  it("returns null when nothing has been measured", () => {
    // Not-measured, unknown-run and other-tenant are deliberately
    // indistinguishable at the API, so they are one state here too: the meter
    // renders nothing. A zeroed meter would be a claim.
    expect(buildContextPillView({ context: null, occupancy: null })).toBeNull();
    expect(
      buildContextPillView({
        context: null,
        occupancy: { conversation_id: "c", run_id: null, snapshot: null },
      }),
    ).toBeNull();
  });

  describe("grouping", () => {
    it("groups by lifecycle, resident first", () => {
      const view = buildContextPillView({
        context: context(),
        occupancy: occupancy(),
      });
      expect(view?.groups.map((group) => group.lifecycle)).toEqual([
        "resident",
        "per_result",
        "per_turn",
      ]);
    });

    it("surfaces the resident connector rent as the top two rows", () => {
      // The whole point of grouping by lifecycle rather than by class: read by
      // class this is an unremarkable "tools 19%".
      const view = buildContextPillView({
        context: context(),
        occupancy: occupancy(),
      });
      const resident = view!.groups[0]!;
      expect(resident.rows.slice(0, 2).map((row) => row.label)).toEqual([
        "linear",
        "github",
      ]);
      expect(resident.rows[0]!.tokens + resident.rows[1]!.tokens).toBe(33_610);
      expect(resident.rows.every((row) => row.tokens > 0)).toBe(true);
    });

    it("notes the multiplier that makes each group actionable", () => {
      const view = buildContextPillView({
        context: context(),
        occupancy: occupancy(),
      });
      // The heading names the RECURRENCE; each row owns its own exact `× N`.
      // A group-level count was a second, vaguer number — and every obvious
      // formulation was wrong once segments arrived one-per-contribution
      // ("× 1 results" from a max, budget-notes-as-results from a sum).
      expect(view?.groups[0]!.note).toBe("every call");
      expect(view?.groups[1]!.note).toBe("scales with results");
      expect(view?.groups[2]!.note).toBe("scales with turns");
    });

    it("drops zero-token segments rather than listing them", () => {
      const view = buildContextPillView({
        context: context(),
        occupancy: occupancy({
          segments: [
            ...SEGMENTS,
            segment({
              segment_class: "response_format",
              label: "response_format:none",
              estimated_tokens: 0,
              item_count: 0,
            }),
          ],
        }),
      });
      const labels = view!.groups.flatMap((group) =>
        group.rows.map((row) => row.label),
      );
      expect(labels).not.toContain("none");
    });
  });

  describe("segment markers", () => {
    it("carries third-party, cacheable and approximate through", () => {
      const view = buildContextPillView({
        context: context(),
        occupancy: occupancy(),
      });
      const rows = view!.groups.flatMap((group) => group.rows);
      const linear = rows.find((row) => row.label === "linear")!;
      expect(linear).toMatchObject({
        thirdParty: true,
        cacheable: true,
        approximate: false,
      });
      // counter_source "proxy" is the fail-open signature — the ledger chose a
      // worse number over failing the run, and the row must say so.
      const results = rows.find((row) => row.label === "tool_results")!;
      expect(results.approximate).toBe(true);
      expect(results.cacheable).toBe(false);
    });

    it("steps tone within a class so siblings stay distinguishable", () => {
      const view = buildContextPillView({
        context: context(),
        occupancy: occupancy(),
      });
      const rows = view!.groups.flatMap((group) => group.rows);
      const tools = rows.filter((row) => row.segmentClass === "tools");
      expect(tools.map((row) => row.tone)).toEqual([1, 0.72, 0.5]);
    });

    it("strips the owner namespace for display but keys on the full label", () => {
      const view = buildContextPillView({
        context: context(),
        occupancy: occupancy(),
      });
      const rows = view!.groups.flatMap((group) => group.rows);
      expect(rows.map((row) => row.label)).toContain("instructions");
      // The key is `lifecycle::label` — the axis rows fold on. It deliberately
      // excludes `detail`, which is a per-contribution ordinal.
      expect(rows.map((row) => row.key)).toContain(
        "resident::system:instructions",
      );
    });

    it("FOLDS two contributions of one declaration into a single row", () => {
      // This assertion used to demand the opposite — one row per segment, keyed
      // apart by `detail`. A live run disproved it: the ledger emits a segment
      // per contribution, so that rule put `tool_result` in the popover eight
      // times as msg[10]…msg[17]. `detail` is a per-contribution ordinal, not
      // an identity; the declaration is.
      const view = buildContextPillView({
        context: context(),
        occupancy: occupancy({
          segments: [
            segment({ detail: "issues", estimated_tokens: 9_000 }),
            segment({ detail: "projects", estimated_tokens: 7_000 }),
          ],
        }),
      });
      const rows = view!.groups.flatMap((group) => group.rows);
      expect(rows).toHaveLength(1);
      expect(rows[0]!.tokens).toBe(16_000);
      expect(rows[0]!.detail).toBe("\u00d7 82");
    });

    it("names the UNDECLARED sentinel rather than rendering an empty cell", () => {
      const view = buildContextPillView({
        context: context(),
        occupancy: occupancy({
          segments: [
            segment({
              label: "UNDECLARED",
              detail: null,
              estimated_tokens: 3_180,
              third_party: false,
            }),
          ],
          undeclared_tokens: 3_180,
        }),
      });
      expect(view!.groups[0]!.rows[0]!.label).toBe("undeclared");
      expect(view?.undeclaredTokens).toBe(3_180);
    });
  });

  describe("the bar", () => {
    it("draws consumed slices only, leaving the track as the headroom", () => {
      const view = buildContextPillView({
        context: context(),
        occupancy: occupancy(),
      });
      const total = view!.slices.reduce((sum, slice) => sum + slice.pct, 0);
      // 79,240 of 200,000 — the remaining ~60% is the track the number names,
      // which is why nothing here computes 100 - headroom.
      expect(total).toBeCloseTo(39.62, 1);
    });

    it("groups slices by class so each class reads as ONE run", () => {
      // Sorted by size alone the classes interleave (tools · messages ·
      // messages · tools · system …), which draws seven stripes and states
      // none of the four totals. Row correspondence is by colour, not
      // position, so regrouping the bar costs nothing.
      const view = buildContextPillView({
        context: context(),
        occupancy: occupancy(),
      });
      const classes = view!.slices
        .map((slice) => slice.segmentClass)
        .filter((c): c is NonNullable<typeof c> => c !== null);
      const runs = classes.filter((c, i) => c !== classes[i - 1]);
      expect(runs).toEqual(["tools", "messages", "system"]);
      expect(new Set(runs).size).toBe(runs.length);
    });

    it("gives the positive provider delta a classless slice", () => {
      const view = buildContextPillView({
        context: context(),
        occupancy: occupancy(),
      });
      const delta = view!.slices.find((slice) => slice.segmentClass === null);
      expect(delta?.pct).toBeCloseTo(0.62, 2);
    });

    it("draws no slice for a NEGATIVE delta but still reports it", () => {
      // Negative means we over-counted. A bar cannot draw negative width; the
      // figure still has to survive to the row.
      const view = buildContextPillView({
        context: context(),
        occupancy: occupancy({
          provider_input_tokens: 77_180,
          unattributed_delta: -820,
        }),
      });
      expect(view!.slices.some((slice) => slice.segmentClass === null)).toBe(
        false,
      );
      expect(view?.unattributedDelta).toBe(-820);
    });
  });

  it("reports the most recent compaction by timestamp, not by array order", () => {
    const base = context();
    const view = buildContextPillView({
      context: {
        ...base,
        breakdown: {
          ...base.breakdown,
          compression_events: [
            {
              before: 128_000,
              after: 34_000,
              strategy: "summarize",
              at: "2026-08-14T09:00:00Z",
            },
            {
              before: 90_000,
              after: 21_000,
              strategy: "summarize",
              at: "2026-08-14T08:00:00Z",
            },
          ],
        },
      },
      occupancy: occupancy(),
    });
    expect(view?.compaction).toEqual({ before: 128_000, after: 34_000 });
  });
});

// The shape a REAL snapshot has, taken from a live packaged run
// (journey-composer-and-budgets, 2026-08-14): the ledger emits one segment per
// contribution, so `detail` carries a message ordinal or a tool name and the
// same declaration recurs many times. 44 segments, 16 declarations.
//
// Every fixture above has one segment per label, which is exactly why the
// original one-row-per-segment render looked fine in tests and shipped a
// 44-row list into a 300px popover.
function liveShapeSegments(): ContextOccupancySegment[] {
  const out: ContextOccupancySegment[] = [];
  const push = (
    label: string,
    lifecycle: ContextOccupancySegment["lifecycle"],
    cls: ContextOccupancySegment["segment_class"],
    detail: string,
    tokens: number,
  ): void => {
    out.push(
      segment({
        label,
        lifecycle,
        segment_class: cls,
        third_party: false,
        detail,
        estimated_tokens: tokens,
        item_count: 1,
        cache_eligibility: null,
        counter_source: "tokenizer",
      }),
    );
  };
  // 8 tool results, one per message ordinal.
  [1404, 1226, 1107, 1044, 1013, 996, 928, 774].forEach((t, i) =>
    push(
      "agent_runtime.conversation:tool_result",
      "per_result",
      "messages",
      `msg[${String(10 + i)}]`,
      t,
    ),
  );
  // 5 budget notes, same declaration, different ordinals.
  [41, 41, 41, 41, 41].forEach((t, i) =>
    push(
      "agent_runtime.capabilities:tool_budget_note",
      "per_result",
      "messages",
      `msg[${String(11 + i)}]`,
      t,
    ),
  );
  // 11 undeclared, one per tool.
  [
    "write_file",
    "glob",
    "delete",
    "ls",
    "read",
    "grep",
    "edit",
    "bash",
    "task",
    "web",
    "fetch",
  ].forEach((tool, i) =>
    push("UNDECLARED", "resident", "system", tool, 182 - i * 5),
  );
  // 10 distinct first-party tool schemas — the long tail nobody acts on.
  [
    "publish_artifact",
    "revise_artifact",
    "stage_rowset_write",
    "list_connected_servers",
    "suggest_mcp_connector",
    "auth_mcp",
    "load_mcp_server",
    "load_skill",
    "ask_a_question",
    "load_prior_tool_result",
  ].forEach((name, i) =>
    push(
      `agent_runtime.capabilities:${name}`,
      "resident",
      "tools",
      name,
      1381 - i * 100,
    ),
  );
  return out;
}

describe("buildContextPillView — the shape a live ledger actually has", () => {
  const live = () =>
    buildContextPillView({
      context: context(),
      occupancy: occupancy({ segments: liveShapeSegments() }),
    })!;

  it("folds repeated segments of one declaration into ONE row", () => {
    // 8 `tool_result` segments differing only by message ordinal are one fact.
    const perResult = live().groups.find((g) => g.lifecycle === "per_result")!;
    const toolResults = perResult.rows.find((r) => r.label === "tool_result")!;
    expect(toolResults.tokens).toBe(8492);
    expect(toolResults.detail).toBe("× 8");
  });

  it("drops the message ordinal — msg[13] is not a thing anyone can act on", () => {
    const labels = live()
      .groups.flatMap((g) => g.rows)
      .map((r) => `${r.label} ${r.detail ?? ""}`);
    expect(labels.some((l) => l.includes("msg["))).toBe(false);
  });

  it("keeps the whole popover under a readable row count", () => {
    // 44 segments in, and the original render put 44 rows in a 300px frame.
    const rows = live().groups.flatMap((g) => g.rows);
    // The live run carried 44; this fixture reproduces the same SHAPE at 34.
    expect(liveShapeSegments()).toHaveLength(34);
    expect(rows.length).toBeLessThanOrEqual(12);
  });

  it("caps each group at four rows plus a remainder that NAMES what it swallowed", () => {
    // A silent top-4 would read as "that is everything".
    const resident = live().groups.find((g) => g.lifecycle === "resident")!;
    expect(resident.rows).toHaveLength(5);
    const more = resident.rows.at(-1)!;
    expect(more.remainder).toBe(true);
    expect(more.label).toMatch(/^\d+ more$/);
    expect(more.tokens).toBeGreaterThan(0);
  });

  it("loses no tokens to the fold — the remainder carries the tail's sum", () => {
    const view = live();
    const drawn = view.groups
      .flatMap((g) => g.rows)
      .reduce((sum, r) => sum + r.tokens, 0);
    const measured = liveShapeSegments().reduce(
      (sum, s) => sum + s.estimated_tokens,
      0,
    );
    expect(drawn).toBe(measured);
  });

  it("files a folded row by its own lifecycle, not by its label", () => {
    // `conversation:tool_result` is per_result while the rest of
    // `conversation:*` is per_turn. Keying the group lookup on label alone put
    // tool results in the transcript's group.
    const perResult = live().groups.find((g) => g.lifecycle === "per_result")!;
    expect(perResult.rows.map((r) => r.label)).toContain("tool_result");
    const perTurn = live().groups.find((g) => g.lifecycle === "per_turn");
    expect(perTurn?.rows.map((r) => r.label) ?? []).not.toContain(
      "tool_result",
    );
  });

  it("keeps a group of five or fewer intact rather than folding one row away", () => {
    const perResult = live().groups.find((g) => g.lifecycle === "per_result")!;
    expect(perResult.rows.every((r) => r.remainder !== true)).toBe(true);
  });
});
