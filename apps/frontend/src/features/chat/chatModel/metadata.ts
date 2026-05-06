import {
  isAssistantPerformanceMetrics,
  type AssistantPerformanceMetrics,
  type Message,
  type RuntimeEventEnvelope,
} from "@enterprise-search/api-types";
import type { MessageTiming, ThreadMessageLike } from "../runtime/types";
import { asRecord } from "../utils/jsonUtils";

export function metadataFromMessage(
  message: Message,
): ThreadMessageLike["metadata"] {
  const custom: Record<string, unknown> = { ...(message.metadata ?? {}) };
  if (message.quote !== undefined && message.quote !== null) {
    custom.quote = message.quote;
  }
  if (message.source_message_id) {
    custom.source_message_id = message.source_message_id;
  }
  if (message.branch_id) {
    custom.branch_id = message.branch_id;
  }
  return metadataFromCustom(custom);
}

export function metadataFromRuntimeEvent(
  event: RuntimeEventEnvelope,
): ThreadMessageLike["metadata"] | undefined {
  const metrics =
    performanceMetricsFromRecord(event.payload) ??
    performanceMetricsFromRecord(event.metadata);
  return metrics
    ? metadataFromCustom({ performance_metrics: metrics })
    : undefined;
}

export function metadataFromCustom(
  custom: Record<string, unknown>,
): ThreadMessageLike["metadata"] | undefined {
  const metrics = performanceMetricsFromRecord(custom);
  if (Object.keys(custom).length === 0 && !metrics) {
    return undefined;
  }
  return {
    custom,
    ...(metrics ? { timing: timingFromPerformanceMetrics(metrics) } : {}),
  };
}

export function mergeMetadata(
  current: ThreadMessageLike["metadata"] | undefined,
  next: ThreadMessageLike["metadata"] | undefined,
): ThreadMessageLike["metadata"] | undefined {
  if (!current) {
    return next;
  }
  if (!next) {
    return current;
  }
  return {
    ...current,
    ...next,
    custom: {
      ...(current.custom ?? {}),
      ...(next.custom ?? {}),
    },
  };
}

export function performanceMetricsFromRecord(
  value: unknown,
): AssistantPerformanceMetrics | null {
  const record = asRecord(value);
  const metrics = record.performance_metrics;
  return isAssistantPerformanceMetrics(metrics) ? metrics : null;
}

export function timingFromPerformanceMetrics(
  metrics: AssistantPerformanceMetrics,
): MessageTiming {
  return {
    streamStartTime: Date.parse(metrics.started_at),
    firstTokenTime: metrics.first_chunk_at
      ? Date.parse(metrics.first_chunk_at)
      : undefined,
    totalStreamTime: metrics.duration_ms,
    tokenCount: metrics.usage?.output ?? metrics.usage?.total,
    tokensPerSecond: metrics.usage?.output_per_second,
    totalChunks: metrics.chunk_count,
    toolCallCount: 0,
  };
}
