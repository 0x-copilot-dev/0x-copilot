// activityDigest — say WHAT a folded run of activity did, in one quiet line.
//
// PRD-03 ("the answer wins") folds a settled run of tool/fleet cards into one
// row, and `absorbThoughtActivity` folds the same cards into the thought that
// produced them. Both were right about density and both shipped a label that
// names only a COUNT: "Thought process · 2 steps", "Worked for 3s · 2 steps".
//
// A live capture is what that costs. One turn searched the web and dispatched a
// subagent; the moment it settled the transcript read
//
//     Thought process · 2 steps ⌄
//     <the answer>
//
// and nothing on screen said a search or a subagent had ever happened. In Focus
// the Run-details column is folded too, so that row was the ENTIRE record of the
// agent's work — which is the literal content of the bug report "in Focus mode I
// can't see anything". PRD-03's own reference table shows the half we dropped:
// Codex folds to `Worked for 26s ›`, but Claude Desktop keeps `Read depth.py ›`.
// We copied the fold and lost the noun.
//
// So the fold stays and the label learns to talk:
//
//     Thought for 8s · Searched the web · Dispatched 1 subagent ⌄
//
// Pure on purpose. Both fold sites hold the member list already, and a digest
// that needed the DOM could not be unit-tested against the one thing that
// matters here — the sentence.

import { classifyTool, toolDisplayName } from "../subagents/subagentHelpers";
import { toolViewFor } from "./toolViews";

/** What the digest needs to know about one folded card. */
export type ActivityDigestMember =
  | {
      readonly kind: "tool";
      readonly toolName: string;
      /** The tool's raw args. Only `file_path` is read — to count FILES, not
       *  calls, so three edits to one file read "Edited 1 file". */
      readonly args?: Readonly<Record<string, unknown>> | undefined;
    }
  | {
      readonly kind: "fleet";
      /** Children in this fleet. `0` means "not known yet" and counts as one —
       *  a dispatch happened, and a digest that said "0 subagents" would be a
       *  stranger lie than rounding up. */
      readonly total: number;
    };

/** Phrases shown before the rest collapse into `+N more`. Three is what fits
 *  beside "Thought for 12s" in a 730px Focus column without wrapping. */
export const ACTIVITY_DIGEST_MAX_PHRASES = 3;

type Bucket = {
  /** Distinct units: file paths where the tool names one, else one per call. */
  readonly units: Set<string>;
  /** Every folded step in this bucket — what `+N more` counts. */
  steps: number;
  readonly phrase: (count: number) => string;
};

const plural = (count: number, noun: string): string =>
  `${count} ${noun}${count === 1 ? "" : "s"}`;

const repeated = (label: string, count: number): string =>
  count === 1 ? label : `${label} ×${count}`;

function filePathOf(
  args: Readonly<Record<string, unknown>> | undefined,
): string | null {
  const value = args?.["file_path"];
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

/**
 * Which sentence a tool call contributes to.
 *
 * The registry (`toolViewFor`) is asked first because it is the product's one
 * statement of what a tool IS; `classifyTool` covers the two families the
 * registry files under "generic" but a reader thinks of as their own verbs (a
 * web search, a shell alias). Everything else keeps its own humanised name — a
 * connector call is not "1 step", it is "Create issue".
 */
function bucketFor(toolName: string): {
  readonly key: string;
  readonly phrase: (count: number) => string;
} {
  switch (toolViewFor(toolName).kind) {
    case "edit":
      return { key: "edit", phrase: (n) => `Edited ${plural(n, "file")}` };
    case "write":
      return { key: "write", phrase: (n) => `Wrote ${plural(n, "file")}` };
    case "read":
      return { key: "read", phrase: (n) => `Read ${plural(n, "file")}` };
    case "search":
      return { key: "search", phrase: (n) => repeated("Searched files", n) };
    case "command":
      return { key: "command", phrase: (n) => `Ran ${plural(n, "command")}` };
    case "generic":
      break;
  }
  switch (classifyTool(toolName)) {
    case "web_search":
      return { key: "web", phrase: (n) => repeated("Searched the web", n) };
    case "shell":
      return { key: "command", phrase: (n) => `Ran ${plural(n, "command")}` };
    default: {
      const label = toolDisplayName(toolName);
      return { key: `other:${label}`, phrase: (n) => repeated(label, n) };
    }
  }
}

/**
 * One line naming the work a folded row hides, or `null` for nothing to say.
 *
 * Phrases appear in FIRST-OCCURRENCE order, so the line reads as the run went —
 * "Searched the web · Read 3 files · Edited 1 file" — rather than sorted by a
 * priority the reader would have to learn.
 */
export function describeActivity(
  members: readonly ActivityDigestMember[],
): string | null {
  const buckets = new Map<string, Bucket>();
  let anonymous = 0;

  for (const member of members) {
    if (member.kind === "fleet") {
      const bucket = buckets.get("fleet") ?? {
        units: new Set<string>(),
        steps: 0,
        phrase: (n: number) => `Dispatched ${plural(n, "subagent")}`,
      };
      // One unit per CHILD: the phrase counts subagents, not dispatches.
      const children = Math.max(member.total, 1);
      for (let i = 0; i < children; i += 1) {
        bucket.units.add(`fleet-${bucket.steps}-${i}`);
      }
      bucket.steps += 1;
      buckets.set("fleet", bucket);
      continue;
    }

    const { key, phrase } = bucketFor(member.toolName);
    const bucket = buckets.get(key) ?? {
      units: new Set<string>(),
      steps: 0,
      phrase,
    };
    const path =
      key === "edit" || key === "write" || key === "read"
        ? filePathOf(member.args)
        : null;
    // No path (or not a file tool) → every call is its own unit.
    anonymous += 1;
    bucket.units.add(path ?? `call-${anonymous}`);
    bucket.steps += 1;
    buckets.set(key, bucket);
  }

  if (buckets.size === 0) return null;

  const all = [...buckets.values()];
  const shown = all.slice(0, ACTIVITY_DIGEST_MAX_PHRASES);
  const hiddenSteps = all
    .slice(ACTIVITY_DIGEST_MAX_PHRASES)
    .reduce((sum, bucket) => sum + bucket.steps, 0);

  const phrases = shown.map((bucket) => bucket.phrase(bucket.units.size));
  if (hiddenSteps > 0) phrases.push(`+${hiddenSteps} more`);
  return phrases.join(" · ");
}
