// firstRunAckLines — derive the three acknowledgment echo lines (PRD-P3 §3.6).
//
// Pure copy derivation, byte-verbatim vs SPEC §"Copy strings":
//   model   — {name}[ · downloading N%| · on-device]
//   tools   — {web search|none}[ · {connector}…]
//   privacy — local → "nothing leaves this machine"; key → "key in your OS keychain"
//
// Tolerant of `pct === undefined` (P2's download-annotated pct may not be
// wired): a local engine with no pct reads as "· on-device", never "· NaN%".

// Named `FirstRunAckEngine` (not `FirstRunEngine`) to avoid colliding with the
// surface's `FirstRunEngine` discriminated union in `firstRun.ts` — this is the
// flattened ack-copy view (kind + display name + optional pct).
export interface FirstRunAckEngine {
  readonly kind: "local" | "key";
  /** Display name — e.g. "Qwen 3 4B" or "Claude Sonnet 4.5". */
  readonly name: string;
  /** Local download progress 0–100 (P2). Undefined → treat as ready. */
  readonly pct?: number;
}

export interface FirstRunToolsState {
  readonly webOn: boolean;
  readonly connectors: readonly string[];
}

export interface FirstRunAckLines {
  readonly modelLine: string;
  readonly toolsLine: string;
  readonly privacyLine: string;
}

function modelSuffix(engine: FirstRunAckEngine): string {
  if (engine.kind !== "local") {
    return "";
  }
  if (engine.pct !== undefined && engine.pct < 100) {
    return ` · downloading ${engine.pct}%`;
  }
  return " · on-device";
}

export function firstRunAckLines(
  engine: FirstRunAckEngine,
  tools: FirstRunToolsState,
): FirstRunAckLines {
  const modelLine = `model — ${engine.name}${modelSuffix(engine)}`;

  const toolsBase = tools.webOn ? "web search" : "none";
  const connectorSuffix =
    tools.connectors.length > 0 ? ` · ${tools.connectors[0]}…` : "";
  const toolsLine = `tools — ${toolsBase}${connectorSuffix}`;

  const privacyLine =
    engine.kind === "local"
      ? "nothing leaves this machine"
      : "key in your OS keychain";

  return { modelLine, toolsLine, privacyLine };
}
