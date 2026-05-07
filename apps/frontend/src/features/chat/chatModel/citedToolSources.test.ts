// PR 1.1-rev2 / Phase 4e/4f — tests for the tool-citation projection
// helpers powering the SourcesTab rail.

import { describe, expect, it } from "vitest";
import type { CitationLink } from "@enterprise-search/api-types";

import {
  citedToolSources,
  connectorFromToolName,
  TOOL_CITATION_ID_PREFIX,
  TOOL_DOC_ID_PREFIX,
  toolInvocationIndex,
} from "./citedToolSources";
import {
  emptyCitationLinkRegistry,
  upsertCitationLink,
} from "./citationLinkReducer";
import type { ChatItem } from "./types";

const RUN = "run_1";
const MSG = "msg_1";

function toolPart(overrides: {
  toolCallId: string;
  toolName?: string;
  args?: Record<string, unknown> | null;
  result?: string | null;
}): ChatItem {
  return {
    id: `m_${overrides.toolCallId}`,
    kind: "message",
    role: "assistant",
    runId: RUN,
    content: [
      {
        type: "tool-call",
        toolCallId: overrides.toolCallId,
        toolName: overrides.toolName ?? "linear.list_issues",
        args: overrides.args ?? null,
        result: overrides.result ?? null,
      },
    ],
  } as unknown as ChatItem;
}

function link(overrides: Partial<CitationLink>): CitationLink {
  return {
    conversation_ordinal: 1,
    message_id: MSG,
    prose_offset: 0,
    prose_length: 5,
    source_tool_call_id: "call_one",
    ...overrides,
  };
}

describe("toolInvocationIndex", () => {
  it("indexes one tool call by tool_call_id", () => {
    const items = [
      toolPart({
        toolCallId: "call_one",
        toolName: "web_search",
        args: { query: "langchain deep agents" },
        result: "Top result text",
      }),
    ];
    const index = toolInvocationIndex(items);
    const snapshot = index.get("call_one");
    expect(snapshot?.tool_name).toBe("web_search");
    expect(snapshot?.result).toBe("Top result text");
    expect(snapshot?.args?.query).toBe("langchain deep agents");
  });

  it("ignores non-tool-call parts", () => {
    const items = [
      {
        id: "m1",
        kind: "message",
        role: "assistant",
        runId: RUN,
        content: [{ type: "text", text: "plain prose" }],
      } as unknown as ChatItem,
    ];
    const index = toolInvocationIndex(items);
    expect(index.size).toBe(0);
  });

  it("returns the same empty registry across calls when nothing matches", () => {
    const a = toolInvocationIndex([]);
    const b = toolInvocationIndex([]);
    expect(a).toBe(b); // shared sentinel
    expect(a.size).toBe(0);
  });

  it("first snapshot wins on duplicate tool_call_id across messages", () => {
    const items = [
      toolPart({ toolCallId: "call_one", toolName: "first", result: "r1" }),
      toolPart({ toolCallId: "call_one", toolName: "second", result: "r2" }),
    ];
    const index = toolInvocationIndex(items);
    expect(index.get("call_one")?.tool_name).toBe("first");
  });

  it("ignores tool-call parts without a toolCallId", () => {
    const items = [
      {
        id: "m1",
        kind: "message",
        role: "assistant",
        runId: RUN,
        content: [
          { type: "tool-call", toolCallId: "", toolName: "skipme", args: null },
        ],
      } as unknown as ChatItem,
    ];
    expect(toolInvocationIndex(items).size).toBe(0);
  });
});

describe("connectorFromToolName", () => {
  it("strips MCP server prefix from dotted names", () => {
    expect(connectorFromToolName("linear.list_issues")).toBe("linear");
    expect(connectorFromToolName("notion.search")).toBe("notion");
  });

  it("maps web_search to web", () => {
    expect(connectorFromToolName("web_search")).toBe("web");
  });

  it("groups MCP control-plane tools under mcp", () => {
    expect(connectorFromToolName("load_mcp_server")).toBe("mcp");
    expect(connectorFromToolName("call_mcp_tool")).toBe("mcp");
  });

  it("falls back to tool for unknown names", () => {
    expect(connectorFromToolName("ask_a_question")).toBe("tool");
  });
});

describe("citedToolSources", () => {
  it("returns empty when runId is null", () => {
    const out = citedToolSources({
      runId: null,
      citationLinks: emptyCitationLinkRegistry(),
      toolIndex: toolInvocationIndex([]),
    });
    expect(out).toEqual([]);
  });

  it("returns empty when no citation links exist for the run", () => {
    const out = citedToolSources({
      runId: RUN,
      citationLinks: emptyCitationLinkRegistry(),
      toolIndex: toolInvocationIndex([]),
    });
    expect(out).toEqual([]);
  });

  it("projects one cited tool into a SourceEntry", () => {
    const items = [
      toolPart({
        toolCallId: "call_one",
        toolName: "web_search",
        args: { query: "langchain deep agents" },
        result: "Top result body text".repeat(10),
      }),
    ];
    const links = upsertCitationLink(
      emptyCitationLinkRegistry(),
      RUN,
      link({ conversation_ordinal: 1, source_tool_call_id: "call_one" }),
    );
    const out = citedToolSources({
      runId: RUN,
      citationLinks: links,
      toolIndex: toolInvocationIndex(items),
    });
    expect(out).toHaveLength(1);
    expect(out[0].citation_id).toBe(`${TOOL_CITATION_ID_PREFIX}call_one`);
    expect(out[0].source_doc_id).toBe(`${TOOL_DOC_ID_PREFIX}call_one`);
    expect(out[0].source_connector).toBe("web");
    expect(out[0].title).toContain("web_search");
    expect(out[0].title).toContain("langchain deep agents");
    expect(out[0].snippet).toBeTruthy();
    expect(out[0].citation_count).toBe(1);
  });

  it("aggregates citation_count by tool_call_id across multiple chips", () => {
    const items = [
      toolPart({
        toolCallId: "call_one",
        toolName: "web_search",
        result: "result",
      }),
    ];
    let registry = emptyCitationLinkRegistry();
    registry = upsertCitationLink(
      registry,
      RUN,
      link({ conversation_ordinal: 1, prose_offset: 0 }),
    );
    registry = upsertCitationLink(
      registry,
      RUN,
      link({ conversation_ordinal: 1, prose_offset: 12 }),
    );
    const out = citedToolSources({
      runId: RUN,
      citationLinks: registry,
      toolIndex: toolInvocationIndex(items),
    });
    expect(out).toHaveLength(1);
    expect(out[0].citation_count).toBe(2);
  });

  it("skips citation links with empty source_tool_call_id (hallucinated ordinal)", () => {
    const links = upsertCitationLink(
      emptyCitationLinkRegistry(),
      RUN,
      link({ conversation_ordinal: 99, source_tool_call_id: "" }),
    );
    const out = citedToolSources({
      runId: RUN,
      citationLinks: links,
      toolIndex: toolInvocationIndex([]),
    });
    expect(out).toEqual([]);
  });

  it("renders a placeholder title when no snapshot exists for the cited tool", () => {
    const links = upsertCitationLink(
      emptyCitationLinkRegistry(),
      RUN,
      link({
        conversation_ordinal: 5,
        source_tool_call_id: "missing_call",
      }),
    );
    const out = citedToolSources({
      runId: RUN,
      citationLinks: links,
      toolIndex: toolInvocationIndex([]),
    });
    expect(out).toHaveLength(1);
    expect(out[0].title).toBe("tool call");
    expect(out[0].source_connector).toBe("tool");
    expect(out[0].snippet).toBeNull();
  });

  it("truncates long snippets to the configured cap", () => {
    const items = [
      toolPart({
        toolCallId: "call_one",
        result: "x".repeat(500),
      }),
    ];
    const links = upsertCitationLink(
      emptyCitationLinkRegistry(),
      RUN,
      link({ conversation_ordinal: 1, source_tool_call_id: "call_one" }),
    );
    const out = citedToolSources({
      runId: RUN,
      citationLinks: links,
      toolIndex: toolInvocationIndex(items),
      snippetMaxChars: 50,
    });
    expect(out[0].snippet?.length).toBeLessThanOrEqual(51); // 50 + ellipsis char
    expect(out[0].snippet?.endsWith("…")).toBe(true);
  });

  it("creates multiple rows when distinct tool_call_ids are cited", () => {
    const items = [
      toolPart({ toolCallId: "call_a", toolName: "web_search", result: "ra" }),
      toolPart({
        toolCallId: "call_b",
        toolName: "linear.list_issues",
        result: "rb",
      }),
    ];
    let registry = emptyCitationLinkRegistry();
    registry = upsertCitationLink(
      registry,
      RUN,
      link({ conversation_ordinal: 1, source_tool_call_id: "call_a" }),
    );
    registry = upsertCitationLink(
      registry,
      RUN,
      link({
        conversation_ordinal: 2,
        prose_offset: 12,
        source_tool_call_id: "call_b",
      }),
    );
    const out = citedToolSources({
      runId: RUN,
      citationLinks: registry,
      toolIndex: toolInvocationIndex(items),
    });
    expect(out).toHaveLength(2);
    const connectors = out.map((row) => row.source_connector).sort();
    expect(connectors).toEqual(["linear", "web"]);
  });
});
