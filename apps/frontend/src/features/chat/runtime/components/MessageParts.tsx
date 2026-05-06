import type { ComponentType, ReactElement, ReactNode } from "react";
import type {
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
  | { type: "reasoningGroup"; startIndex: number; endIndex: number };

/**
 * Adjacent-coalescing grouper: consecutive `tool-call` parts collapse into
 * a `toolGroup` range; consecutive `reasoning` parts collapse into a
 * `reasoningGroup` range; everything else (text, source, image, etc.)
 * becomes a `single` range. Mirrors assistant-ui's `groupMessageParts`
 * behavior (without the chain-of-thought variant we don't use).
 */
function groupParts(parts: readonly ContentPart[]): PartRange[] {
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
    if (part.type === "tool-call") {
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
  const content = message.content;
  if (typeof content === "string" || !content || content.length === 0) {
    return null;
  }
  const parts = content as readonly ContentPart[];
  const ranges = groupParts(parts);

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
        return (
          <ReasoningGroupComponent
            key={`rg-${range.startIndex}-${rangeIndex}`}
            startIndex={range.startIndex}
            endIndex={range.endIndex}
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

function renderPart(
  part: ContentPart,
  index: number,
  components: MessagePartsComponents | undefined,
  onResumeToolCall: ((payload: unknown) => void) | undefined,
): ReactElement | null {
  if (part.type === "tool-call") {
    const tools = components?.tools;
    const Specific = tools?.by_name?.[part.toolName];
    const ToolComp = Specific ?? tools?.Fallback;
    if (!ToolComp) {
      return null;
    }
    const props = {
      // Spread the tool-call part fields. We cast through `unknown`
      // because the runtime's content type for a tool-call part is a
      // structural subset of `ToolCallMessagePartProps` (which adds
      // `addResult` + `resume`). Both are supplied below.
      ...(part as unknown as ToolCallMessagePartProps),
      addResult: noopAddResult,
      resume: onResumeToolCall ?? noopResume,
    };
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
