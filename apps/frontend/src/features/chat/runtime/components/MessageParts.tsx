import type { ComponentType, ReactElement, ReactNode } from "react";
import type {
  MessagePartStatus,
  ReasoningGroupProps,
  ReasoningMessagePartProps,
  TextMessagePartProps,
  ThreadMessageLike,
  ToolCallMessagePartProps,
} from "../types";
import { useMessage } from "./messageContext";

/**
 * Components-map shape consumed by `<MessageParts>`. Mirrors the subset of
 * assistant-ui's `MessagePrimitive.Parts` props that the Atlas codebase
 * actually uses today (no audio/image/file/source/data renderers — those
 * never appear in our content stream).
 */
export interface MessagePartsComponents {
  Text?: ComponentType<TextMessagePartProps>;
  Reasoning?: ComponentType<ReasoningMessagePartProps>;
  ReasoningGroup?: ComponentType<ReasoningGroupProps>;
  ToolGroup?: ComponentType<{
    startIndex: number;
    endIndex: number;
    children?: ReactNode;
  }>;
  tools?: {
    by_name?: Record<string, ComponentType<ToolCallMessagePartProps>>;
    Fallback?: ComponentType<ToolCallMessagePartProps>;
  };
}

type ContentPart = NonNullable<
  Exclude<ThreadMessageLike["content"], string>
>[number];

type PartRange =
  | { type: "single"; index: number }
  | { type: "toolGroup"; startIndex: number; endIndex: number }
  | { type: "subagentFleetGroup"; fleetIndex: number; childIndexes: number[] }
  | { type: "reasoningGroup"; startIndex: number; endIndex: number };

interface FleetIndex {
  readonly fleetChildrenByIndex: ReadonlyMap<number, readonly number[]>;
  readonly groupedChildIndexes: ReadonlySet<number>;
}

/**
 * Adjacent-coalescing grouper: consecutive `tool-call` parts collapse into
 * a `toolGroup` range; consecutive `reasoning` parts collapse into a
 * `reasoningGroup` range; everything else (text, source, image, etc.)
 * becomes a `single` range. Mirrors assistant-ui's `groupMessageParts`
 * behavior (without the chain-of-thought variant we don't use).
 */
function groupParts(
  parts: readonly ContentPart[],
  fleetIndex: FleetIndex,
): PartRange[] {
  const ranges: PartRange[] = [];
  let toolStart = -1;
  let reasoningStart = -1;
  const closeTool = (endIndex: number): void => {
    if (toolStart !== -1) {
      ranges.push({ type: "toolGroup", startIndex: toolStart, endIndex });
      toolStart = -1;
    }
  };
  const closeReasoning = (endIndex: number): void => {
    if (reasoningStart !== -1) {
      ranges.push({
        type: "reasoningGroup",
        startIndex: reasoningStart,
        endIndex,
      });
      reasoningStart = -1;
    }
  };
  for (let i = 0; i < parts.length; i += 1) {
    const part = parts[i];
    if (fleetIndex.groupedChildIndexes.has(i)) {
      closeTool(i - 1);
      closeReasoning(i - 1);
      continue;
    }
    if (part.type === "tool-call") {
      // Interrupt-style tool calls (user-facing approval / auth prompts) are
      // not "steps" of the surrounding work — they pause the entire run and
      // require user action. Break them out of any neighboring tool group
      // so they render as their own card instead of nesting visually inside
      // a subagent's "Reading N sources" panel.
      if (isSubagentFleetCall(part)) {
        closeReasoning(i - 1);
        closeTool(i - 1);
        ranges.push({
          type: "subagentFleetGroup",
          fleetIndex: i,
          childIndexes: [...(fleetIndex.fleetChildrenByIndex.get(i) ?? [])],
        });
        continue;
      }
      if (isStandaloneToolCall(part)) {
        closeReasoning(i - 1);
        closeTool(i - 1);
        ranges.push({ type: "single", index: i });
        continue;
      }
      closeReasoning(i - 1);
      if (toolStart === -1) {
        toolStart = i;
      }
    } else if (part.type === "reasoning") {
      closeTool(i - 1);
      if (reasoningStart === -1) {
        reasoningStart = i;
      }
    } else {
      closeTool(i - 1);
      closeReasoning(i - 1);
      ranges.push({ type: "single", index: i });
    }
  }
  closeTool(parts.length - 1);
  closeReasoning(parts.length - 1);
  return ranges;
}

function indexSubagentFleets(parts: readonly ContentPart[]): FleetIndex {
  const fleetIndexById = new Map<string, number>();
  parts.forEach((part, index) => {
    if (!isSubagentFleetCall(part)) return;
    const id = toolCallId(part);
    if (id) {
      fleetIndexById.set(id, index);
    }
  });

  const mutable = new Map<number, number[]>();
  const grouped = new Set<number>();
  // PR 3.2.4 debug — collect diagnostic info so we can see in DEV
  // whether the reshape is matching children to fleets correctly.
  const debugSubagents: Array<{
    index: number;
    toolCallId: string | null;
    parent_fleet_id: string | null;
    matched: boolean;
  }> = [];
  parts.forEach((part, index) => {
    if (!isSubagentCall(part)) return;
    const fleetId = parentFleetId(part);
    const parentIndex =
      fleetId !== null ? fleetIndexById.get(fleetId) : undefined;
    const matched = parentIndex !== undefined;
    debugSubagents.push({
      index,
      toolCallId: toolCallId(part),
      parent_fleet_id: fleetId,
      matched,
    });
    if (!fleetId || parentIndex === undefined) return;
    const children = mutable.get(parentIndex) ?? [];
    children.push(index);
    mutable.set(parentIndex, children);
    grouped.add(index);
  });
  if (
    typeof import.meta !== "undefined" &&
    import.meta.env?.DEV &&
    (fleetIndexById.size > 0 || debugSubagents.length > 0)
  ) {
    // eslint-disable-next-line no-console
    console.log("[PR 3.2.4 fleet reshape]", {
      fleetIds: [...fleetIndexById.keys()],
      fleetIndexes: [...fleetIndexById.values()],
      subagents: debugSubagents,
      groupedChildIndexes: [...grouped],
    });
  }

  return {
    fleetChildrenByIndex: mutable,
    groupedChildIndexes: grouped,
  };
}

const INTERRUPT_TOOL_NAMES = new Set(["approval_request", "mcp_auth_required"]);

const STANDALONE_TOOL_NAMES = new Set([
  ...INTERRUPT_TOOL_NAMES,
  "run_subagent",
]);

function toolName(part: ContentPart): string | null {
  if (part.type !== "tool-call") {
    return null;
  }
  const value = (part as { toolName?: unknown }).toolName;
  return typeof value === "string" ? value : null;
}

function toolCallId(part: ContentPart): string | null {
  if (part.type !== "tool-call") {
    return null;
  }
  const value = (part as { toolCallId?: unknown }).toolCallId;
  return typeof value === "string" ? value : null;
}

function argsRecord(part: ContentPart): Record<string, unknown> {
  if (part.type !== "tool-call") {
    return {};
  }
  const args = (part as { args?: unknown }).args;
  return args && typeof args === "object" && !Array.isArray(args)
    ? (args as Record<string, unknown>)
    : {};
}

function parentFleetId(part: ContentPart): string | null {
  const value = argsRecord(part).parent_fleet_id;
  return typeof value === "string" && value.length > 0 ? value : null;
}

function isStandaloneToolCall(part: ContentPart): boolean {
  const name = toolName(part);
  return name !== null && STANDALONE_TOOL_NAMES.has(name);
}

function isSubagentCall(part: ContentPart): boolean {
  return toolName(part) === "run_subagent";
}

function isSubagentFleetCall(part: ContentPart): boolean {
  return toolName(part) === "run_subagent_fleet";
}

/**
 * Walks `message.content`, dispatching each part to the appropriate
 * renderer in `components`. Replaces `MessagePrimitive.Parts`.
 *
 * Tool-call parts route through `components.tools.by_name[toolName]`
 * with a fallback to `components.tools.Fallback`; the matched component
 * is given a `resume` callback wired through context so interrupt tools
 * (approval / mcp_auth / ask-a-question) can ship the user's decision
 * back to the host.
 */
export function MessageParts({
  components,
}: {
  components?: MessagePartsComponents;
}): ReactElement | null {
  const { message, onResumeToolCall } = useMessage();
  if (!message) {
    if (import.meta.env?.DEV) {
      // eslint-disable-next-line no-console
      console.warn(
        "MessageParts: <Message message={...}> Provider rendered with undefined message; skipping.",
      );
    }
    return null;
  }
  const content = message.content;
  if (typeof content === "string" || !content || content.length === 0) {
    return null;
  }
  const parts = content as readonly ContentPart[];
  const fleetIndex = indexSubagentFleets(parts);
  const ranges = groupParts(parts, fleetIndex);

  return (
    <>
      {ranges.map((range, rangeIndex) => {
        if (range.type === "single") {
          return renderPart(
            parts[range.index],
            range.index,
            components,
            onResumeToolCall,
          );
        }
        if (range.type === "toolGroup") {
          const ToolGroupComponent = components?.ToolGroup;
          const children = collectRangeChildren(
            parts,
            range.startIndex,
            range.endIndex,
            components,
            onResumeToolCall,
          );
          if (!ToolGroupComponent) {
            return (
              <span key={`tg-${range.startIndex}-${rangeIndex}`}>
                {children}
              </span>
            );
          }
          return (
            <ToolGroupComponent
              key={`tg-${range.startIndex}-${rangeIndex}`}
              startIndex={range.startIndex}
              endIndex={range.endIndex}
            >
              {children}
            </ToolGroupComponent>
          );
        }
        if (range.type === "subagentFleetGroup") {
          // PR 3.2.4 — fleet card renders its children as compact
          // <FleetSubagentRow>s, not as full <SubagentCard>s. Pass the
          // raw child parts so the fleet tool can build the rows
          // itself; we no longer render the children via `renderPart`
          // (which would give us SubagentCards with full chrome).
          const nestedChildren = range.childIndexes.map(
            (index) => parts[index],
          );
          return renderPart(
            parts[range.fleetIndex],
            range.fleetIndex,
            components,
            onResumeToolCall,
            undefined,
            nestedChildren,
          );
        }
        // reasoningGroup
        const ReasoningGroupComponent = components?.ReasoningGroup;
        const children = collectRangeChildren(
          parts,
          range.startIndex,
          range.endIndex,
          components,
          onResumeToolCall,
        );
        if (!ReasoningGroupComponent) {
          return (
            <span key={`rg-${range.startIndex}-${rangeIndex}`}>{children}</span>
          );
        }
        const groupParts = parts.slice(range.startIndex, range.endIndex + 1);
        const groupStatus = reasoningGroupStatus(groupParts);
        const groupElapsedSeconds = reasoningGroupElapsedSeconds(groupParts);
        return (
          <ReasoningGroupComponent
            key={`rg-${range.startIndex}-${rangeIndex}`}
            startIndex={range.startIndex}
            endIndex={range.endIndex}
            status={groupStatus}
            elapsedSeconds={groupElapsedSeconds}
          >
            {children}
          </ReasoningGroupComponent>
        );
      })}
    </>
  );
}

function collectRangeChildren(
  parts: readonly ContentPart[],
  startIndex: number,
  endIndex: number,
  components: MessagePartsComponents | undefined,
  onResumeToolCall: ((payload: unknown) => void) | undefined,
): ReactNode[] {
  const out: ReactNode[] = [];
  for (let i = startIndex; i <= endIndex; i += 1) {
    out.push(renderPart(parts[i], i, components, onResumeToolCall));
  }
  return out;
}

/** Default status envelope for renderers that still expect the
 *  assistant-ui `status: { type }` shape. The Atlas runtime stores
 *  per-part state on the part object itself; until each renderer is
 *  migrated off the assistant-ui shape, we synthesize this so they
 *  don't crash on `status.type`. */
const COMPLETE_STATUS: MessagePartStatus = { type: "complete" };
const RUNNING_STATUS: MessagePartStatus = { type: "running" };

/** Reasoning-group status: `running` if any contained reasoning part is
 *  still streaming, otherwise `complete`. */
function reasoningGroupStatus(
  parts: readonly ContentPart[],
): "running" | "complete" {
  for (const part of parts) {
    if (part.type !== "reasoning") {
      continue;
    }
    if ((part as { status?: MessagePartStatus }).status?.type === "running") {
      return "running";
    }
  }
  return "complete";
}

/** Reasoning-group elapsed-seconds: latest `updatedAtMs` minus earliest
 *  `startedAtMs` across the contained reasoning parts, floored at zero
 *  and rounded to whole seconds. Returns `0` when timestamps are
 *  missing — happens for replayed pre-PR-3.6 messages. */
function reasoningGroupElapsedSeconds(parts: readonly ContentPart[]): number {
  let earliestStart = Number.POSITIVE_INFINITY;
  let latestUpdate = Number.NEGATIVE_INFINITY;
  for (const part of parts) {
    if (part.type !== "reasoning") {
      continue;
    }
    const start = (part as { startedAtMs?: number }).startedAtMs;
    const updated = (part as { updatedAtMs?: number }).updatedAtMs;
    if (typeof start === "number" && start < earliestStart) {
      earliestStart = start;
    }
    if (typeof updated === "number" && updated > latestUpdate) {
      latestUpdate = updated;
    }
  }
  if (
    !Number.isFinite(earliestStart) ||
    !Number.isFinite(latestUpdate) ||
    latestUpdate < earliestStart
  ) {
    return 0;
  }
  return Math.max(0, Math.round((latestUpdate - earliestStart) / 1000));
}

function partStatus(part: ContentPart): MessagePartStatus {
  // If the part already carries a `status` envelope, pass it through.
  // Otherwise infer: tool-calls with a `result` are complete; without,
  // they're running. Text + reasoning default to complete (they only
  // live in the parts list once committed).
  const existing = (part as { status?: MessagePartStatus }).status;
  if (existing && typeof existing.type === "string") {
    return existing;
  }
  if (part.type === "tool-call") {
    const result = (part as { result?: unknown }).result;
    return result === undefined ? RUNNING_STATUS : COMPLETE_STATUS;
  }
  return COMPLETE_STATUS;
}

function renderPart(
  part: ContentPart,
  index: number,
  components: MessagePartsComponents | undefined,
  onResumeToolCall: ((payload: unknown) => void) | undefined,
  children?: ReactNode,
  nestedChildren?: readonly ContentPart[],
): ReactElement | null {
  if (part.type === "tool-call") {
    const tools = components?.tools;
    const Specific = tools?.by_name?.[part.toolName];
    const ToolComp = Specific ?? tools?.Fallback;
    if (!ToolComp) {
      return null;
    }
    // Tools were authored against the assistant-ui shape where the
    // runtime synthesised non-null `args` / `argsText` / `status`. We
    // mirror that contract here so each tool can keep destructuring
    // without optional-chaining every field.
    const safeArgs = ((part as { args?: unknown }).args ?? {}) as Record<
      string,
      unknown
    >;
    const safeArgsText =
      typeof (part as { argsText?: unknown }).argsText === "string"
        ? (part as { argsText: string }).argsText
        : "";
    const props = {
      ...(part as unknown as ToolCallMessagePartProps),
      args: safeArgs,
      argsText: safeArgsText,
      status: partStatus(part),
      addResult: noopAddResult,
      resume: onResumeToolCall ?? noopResume,
      children,
      nestedChildren,
    } as unknown as ToolCallMessagePartProps;
    return <ToolComp key={`p-${index}`} {...props} />;
  }
  if (part.type === "text") {
    const TextComp = components?.Text;
    if (!TextComp) {
      return null;
    }
    return (
      <TextComp
        key={`p-${index}`}
        {...(part as unknown as TextMessagePartProps)}
        status={partStatus(part)}
      />
    );
  }
  if (part.type === "reasoning") {
    const ReasoningComp = components?.Reasoning;
    if (!ReasoningComp) {
      return null;
    }
    return (
      <ReasoningComp
        key={`p-${index}`}
        {...(part as unknown as ReasoningMessagePartProps)}
        status={partStatus(part)}
      />
    );
  }
  // Source / image / file / audio / data — unused by Atlas today; render
  // nothing rather than warn (we validate our event stream upstream).
  return null;
}

function noopAddResult(): void {
  // Atlas tools don't call addResult; they communicate via `resume`.
}

function noopResume(): void {
  // Tool components attempt to resume but no host wired the callback.
  // Silently no-op rather than crash so storybook / preview mounts stay
  // functional.
}
