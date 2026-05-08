// PR 1.1-rev2 / Phase 4e/4f — derive Sources-tab rows from cited tool calls.
//
// The legacy ``sourcesReducer`` builds a ``SourceEntryMap`` from
// ``source_ingested`` events (PR 1.1's wire). The model-declared
// ``[[N]]`` system instead emits ``citation_made`` events that point at
// a ``source_tool_call_id``; the actual source detail lives on the
// existing tool invocation that ran. To populate the Sources tab from
// the new path without changing ``SourceRow``'s shape, we project each
// cited tool invocation into a synthetic ``SourceEntry`` and merge it
// into the legacy map.
//
// Two helpers, both pure:
//
//   - ``toolInvocationIndex(items)`` walks the chat content and returns
//     a ``Map<tool_call_id, ToolCallSnapshot>`` so a citation event can
//     look up the cited tool's name + args + result without re-walking
//     the whole tree.
//
//   - ``citedToolSources({ runId, citationLinks, toolIndex })`` projects
//     each unique ``source_tool_call_id`` cited in ``runId`` into one
//     synthetic ``SourceEntry``. ``citation_count`` aggregates the
//     number of distinct ``[[N]]`` chips pointing at the tool invocation
//     across the run's messages, matching what ``sourcesByCitationCount``
//     expects.

import type { CitationLink, SourceEntry } from "@enterprise-search/api-types";

import type { CitationLinkRegistryByRun } from "./citationLinkReducer";
import { linksForRun } from "./citationLinkReducer";
import { citationDebug } from "./citationDebug";
import type { ChatItem, ThreadToolCallArgs, ThreadToolCallPart } from "./types";

/** Compact view of a tool invocation we may need to cite. */
export interface ToolCallSnapshot {
  tool_call_id: string;
  tool_name: string;
  args: ThreadToolCallArgs | null;
  result: string | null;
  result_is_error: boolean;
}

export type ToolInvocationIndex = ReadonlyMap<string, ToolCallSnapshot>;

const EMPTY_INDEX: ToolInvocationIndex = new Map();
const EMPTY_SOURCES: SourceEntry[] = [];

/** Synthetic citation_id prefix so legacy and tool-call rows don't
 *  collide on key lookup in ``SourceRow``. The value is opaque to the
 *  Sources tab — it's used as a unique id and as the focus key from
 *  chip clicks. */
export const TOOL_CITATION_ID_PREFIX = "tool:";

/** Synthetic source_doc_id prefix for the same reason — keeps the
 *  ``(source_connector, source_doc_id)`` dedup key stable. */
export const TOOL_DOC_ID_PREFIX = "tool-call:";

const SNIPPET_MAX_CHARS = 280;

export function toolInvocationIndex(
  items: readonly ChatItem[],
): ToolInvocationIndex {
  const index = new Map<string, ToolCallSnapshot>();
  for (const item of items) {
    if (item.kind !== "message") {
      continue;
    }
    for (const part of item.content) {
      if (!isToolCallPart(part)) {
        continue;
      }
      const callId = stringValue(part.toolCallId);
      if (callId === null) {
        continue;
      }
      // First snapshot wins by call_id. The reducer that produces the
      // ChatItem stream already coalesces multiple events for one call
      // into a single tool-call part, so this loop sees the latest
      // state per call.
      if (index.has(callId)) {
        continue;
      }
      const args = isToolCallArgs(part.args) ? part.args : null;
      const result = stringValue(part.result);
      index.set(callId, {
        tool_call_id: callId,
        tool_name: stringValue(part.toolName) ?? callId,
        args,
        result,
        // ``result_is_error`` lives in args under the agent's wire
        // shape; presence of either field is best-effort.
        result_is_error: Boolean(
          (args && args["is_error"]) || (args && args["status"] === "failed"),
        ),
      });
    }
  }
  return index.size === 0 ? EMPTY_INDEX : index;
}

/** Tool names the FE *synthesizes* from non-tool wire events (approval
 *  gates, MCP auth interrupts, progress beats). They are not real tool
 *  calls — the runtime never allocates a ``conversation_ordinal`` for
 *  them and they don't fire ``tool_call_started``. They must NOT
 *  participate in ordinal-position fallback or ordinal-1 would land on
 *  the approval gate instead of the actual tool that ran after the
 *  gate cleared. */
const SYNTHETIC_FE_TOOL_NAMES: ReadonlySet<string> = new Set([
  "approval_request",
  "mcp_auth_required",
  "run_progress",
]);

/** Walk ``items`` in document order and yield each tool-call part's
 *  call_id, deduped. Used by ``citedToolSources`` to map ordinals (1-based
 *  per conversation) back to the corresponding tool invocation when the
 *  runtime didn't bind a ``source_tool_call_id`` — common for LangChain
 *  tools that don't opt into ``InjectedToolCallId`` (DuckDuckGo et al.).
 *
 *  Skips FE-synthesized parts (``approval_request`` etc.) so the Nth
 *  ordinal aligns with the Nth *real* tool invocation — the conversation
 *  ordinal is allocated server-side and never counts the FE-synthesized
 *  approval/auth/progress parts. */
export function toolInvocationCallIdsInOrder(
  items: readonly ChatItem[],
): readonly string[] {
  const seen = new Set<string>();
  const order: string[] = [];
  for (const item of items) {
    if (item.kind !== "message") {
      continue;
    }
    for (const part of item.content) {
      if (!isToolCallPart(part)) {
        continue;
      }
      const toolName = stringValue(part.toolName);
      if (toolName !== null && SYNTHETIC_FE_TOOL_NAMES.has(toolName)) {
        continue;
      }
      const callId = stringValue(part.toolCallId);
      if (callId === null || seen.has(callId)) {
        continue;
      }
      seen.add(callId);
      order.push(callId);
    }
  }
  return order;
}

export interface CitedToolSourcesArgs {
  runId: string | null;
  citationLinks: CitationLinkRegistryByRun;
  toolIndex: ToolInvocationIndex;
  /** PR 1.1-rev2 — ordinal-position fallback. When the runtime
   *  couldn't bind a ``source_tool_call_id`` to an ordinal (LangChain
   *  tool that didn't opt into ``InjectedToolCallId``), we resolve the
   *  ``Nth`` cited ordinal against the ``Nth`` tool-call part in
   *  ``items`` order. ``[]`` disables the fallback. */
  toolCallIdsInOrder?: readonly string[];
  /** Optional cap on the snippet length, mostly for tests. */
  snippetMaxChars?: number;
}

/** Project the conversation's cited tool invocations into ``SourceEntry``
 *  rows.
 *
 *  Each row aggregates how many distinct prose offsets cited this tool's
 *  ordinal across all runs in the registry — that becomes
 *  ``citation_count``, matching the legacy ``sourcesByCitationCount``
 *  ordering used by ``SourcesTab``.
 *
 *  ``runId`` semantics:
 *    - ``string`` → only links emitted in that run (single-turn projection).
 *    - ``null``   → links across every run in the registry. The Sources
 *      tab is conversation-scoped, and after an approval interrupt the
 *      ``citation_made`` events fire on the resumed run while the
 *      assistant message metadata may carry a different run id, so a
 *      single-run filter would silently drop those citations.
 *
 *  Returns ``[]`` when no cited tool invocations exist. */
export function citedToolSources({
  runId,
  citationLinks,
  toolIndex,
  toolCallIdsInOrder = [],
  snippetMaxChars = SNIPPET_MAX_CHARS,
}: CitedToolSourcesArgs): readonly SourceEntry[] {
  const links =
    runId === null
      ? allLinks(citationLinks)
      : linksForRun(citationLinks, runId);
  if (links.length === 0) {
    citationDebug(
      `cited_tool_sources.empty run=${runId ?? "ALL"} ` +
        `reason=no_links runs_indexed=${citationLinks.size} ` +
        `tools_indexed=${toolIndex.size}`,
    );
    return EMPTY_SOURCES;
  }
  // Bucket by tool_call_id; aggregate count and the latest seen offset.
  const byCallId = new Map<
    string,
    {
      ordinal: number;
      tool_call_id: string;
      links: CitationLink[];
    }
  >();
  let ordinalFallbacks = 0;
  for (const link of links) {
    let callId = link.source_tool_call_id;
    if (!callId) {
      // PR 1.1-rev2 — ordinal-position fallback. The Nth cited ordinal
      // resolves to the Nth tool invocation in document order. Common
      // for LangChain tools that don't pass ``InjectedToolCallId`` (the
      // runtime allocates an ordinal but can't bind a call_id at
      // dispatch time).
      const fallback = toolCallIdsInOrder[link.conversation_ordinal - 1];
      if (typeof fallback === "string" && fallback.length > 0) {
        callId = fallback;
        ordinalFallbacks += 1;
      } else {
        // Truly unresolvable (hallucinated ordinal beyond any real
        // tool call). Skip — there's nothing to surface.
        continue;
      }
    }
    const existing = byCallId.get(callId);
    if (existing) {
      existing.links.push(link);
    } else {
      byCallId.set(callId, {
        ordinal: link.conversation_ordinal,
        tool_call_id: callId,
        links: [link],
      });
    }
  }

  const rows: SourceEntry[] = [];
  let missingSnapshots = 0;
  for (const bucket of byCallId.values()) {
    const snapshot = toolIndex.get(bucket.tool_call_id);
    if (snapshot === undefined) {
      missingSnapshots += 1;
    }
    rows.push(toSourceEntry(bucket, snapshot, snippetMaxChars));
  }
  citationDebug(
    `cited_tool_sources.projected run=${runId ?? "ALL"} ` +
      `links=${links.length} unique_calls=${byCallId.size} ` +
      `rows=${rows.length} missing_snapshots=${missingSnapshots} ` +
      `ordinal_fallbacks=${ordinalFallbacks}`,
  );
  return rows;
}

/** Flatten every link in the registry, regardless of run. Used by the
 *  conversation-scoped projection so a citation that fired on a
 *  resumed-after-approval run still surfaces in the Sources tab even
 *  when ``mostRecentAssistantRunId`` points at a sibling run. */
function allLinks(
  registry: CitationLinkRegistryByRun,
): readonly CitationLink[] {
  const out: CitationLink[] = [];
  for (const runId of registry.keys()) {
    for (const link of linksForRun(registry, runId)) {
      out.push(link);
    }
  }
  return out;
}

function toSourceEntry(
  bucket: {
    ordinal: number;
    tool_call_id: string;
    links: readonly CitationLink[];
  },
  snapshot: ToolCallSnapshot | undefined,
  snippetMaxChars: number,
): SourceEntry {
  const toolName = snapshot?.tool_name ?? "tool call";
  // For MCP wrapper calls, derive the connector from the inner
  // ``server_name`` arg so the row groups under ``linear`` rather than
  // ``mcp``. Falls back to the wrapper-name heuristic for everything
  // else. ``approval_request`` is FE-synthesized and shouldn't normally
  // be reachable via ordinal-position fallback (filtered out in
  // ``toolInvocationCallIdsInOrder``), but keep the same args-shape
  // detection here as a defensive default.
  let connector = connectorFromToolName(toolName);
  if (
    (toolName === "call_tool" || toolName === "approval_request") &&
    snapshot?.args
  ) {
    const serverName = stringValue(snapshot.args["server_name"]);
    if (serverName !== null) {
      connector = serverName.toLowerCase();
    }
  }
  return {
    citation_id: `${TOOL_CITATION_ID_PREFIX}${bucket.tool_call_id}`,
    source_connector: connector,
    source_doc_id: `${TOOL_DOC_ID_PREFIX}${bucket.tool_call_id}`,
    source_url: null,
    title: titleFor(toolName, snapshot),
    snippet: snippetFor(snapshot, snippetMaxChars),
    freshness_at: null,
    // ``SourceRow`` displays the ordinal sourced from the parent's
    // ``ordinal`` prop, so we don't pin a value here. ``citation_count``
    // controls relative ordering in ``sourcesByCitationCount`` — using
    // the number of chips that pointed here keeps frequently-cited
    // tools at the top, mirroring the legacy aggregation.
    citation_count: bucket.links.length,
    last_cited_at: "",
  };
}

/** Derive a connector slug from a tool name.
 *
 *  Conventions in the runtime:
 *
 *    - MCP tools land as ``"<server>.<tool>"`` via the citation hint
 *      composed in the MCP middleware (see ``call_tool.py``).
 *    - Built-in tools have flat names (``web_search``,
 *      ``ask_a_question``).
 *    - The historical ``source_ingested`` connectors use slugs like
 *      ``notion`` / ``slack`` / ``drive`` / ``web``; we match them
 *      where possible so legacy and new rows group together.
 */
export function connectorFromToolName(toolName: string): string {
  const dot = toolName.indexOf(".");
  if (dot > 0) {
    return toolName.slice(0, dot).toLowerCase();
  }
  if (toolName === "web_search") {
    return "web";
  }
  if (toolName.startsWith("load_") || toolName.startsWith("call_")) {
    // ``load_mcp_server`` / ``call_mcp_tool`` etc. are control-plane
    // tools — group them under ``mcp`` so they're visually distinct
    // from data-plane sources.
    return "mcp";
  }
  return "tool";
}

function titleFor(
  toolName: string,
  snapshot: ToolCallSnapshot | undefined,
): string {
  // The MCP wrapper exposes itself to the model as ``call_tool`` with
  // ``server_name`` + ``tool_name`` in args. The FE-synthesized
  // ``approval_request`` part also carries ``server_name`` +
  // ``tool_name`` in its args. Surface the actual MCP tool path
  // (``linear.list_issues``) instead of the generic wrapper name so
  // the Sources tab row reads naturally.
  if (
    (toolName === "call_tool" || toolName === "approval_request") &&
    snapshot?.args
  ) {
    const serverName = stringValue(snapshot.args["server_name"]);
    const innerToolName = stringValue(snapshot.args["tool_name"]);
    if (serverName && innerToolName) {
      const labelArgs = snapshot.args["arguments"];
      const argsSummary = isToolCallArgs(labelArgs)
        ? summarizeArgs(labelArgs)
        : null;
      const head = `${serverName}.${innerToolName}`;
      return argsSummary === null ? head : `${head} — ${argsSummary}`;
    }
  }
  if (snapshot?.args) {
    const argsSummary = summarizeArgs(snapshot.args);
    if (argsSummary !== null) {
      return `${toolName} — ${argsSummary}`;
    }
  }
  return toolName;
}

function snippetFor(
  snapshot: ToolCallSnapshot | undefined,
  snippetMaxChars: number,
): string | null {
  if (!snapshot) {
    return null;
  }
  if (snapshot.result_is_error) {
    return "(tool call failed)";
  }
  if (typeof snapshot.result !== "string" || snapshot.result.length === 0) {
    return null;
  }
  const trimmed = snapshot.result.trim();
  if (trimmed.length <= snippetMaxChars) {
    return trimmed;
  }
  return `${trimmed.slice(0, snippetMaxChars).trimEnd()}…`;
}

function summarizeArgs(args: ThreadToolCallArgs): string | null {
  // Heuristic: prefer a short, human-readable arg if one is present.
  for (const key of [
    "query",
    "q",
    "search",
    "name",
    "title",
    "subject",
    "tool_name",
  ]) {
    const value = args[key];
    if (typeof value === "string" && value.trim().length > 0) {
      const trimmed = value.trim();
      return trimmed.length > 64
        ? `${trimmed.slice(0, 64).trimEnd()}…`
        : trimmed;
    }
  }
  return null;
}

function isToolCallPart(part: unknown): part is ThreadToolCallPart {
  return (
    typeof part === "object" &&
    part !== null &&
    (part as { type?: unknown }).type === "tool-call"
  );
}

function isToolCallArgs(value: unknown): value is ThreadToolCallArgs {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}
