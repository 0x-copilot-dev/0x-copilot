import { Badge, Card } from "@enterprise-search/design-system";
import type { ReactElement } from "react";
import { useEffect, useMemo, useState } from "react";
import { Streamdown } from "streamdown";
import type {
  ActivityEvent,
  ActivityStatus,
  RunActivity,
  SubagentActivity,
  ToolCallActivity,
} from "./chatModel";

type ActivityTab = "overview" | string;

export function RunActivityPanel({
  activity,
}: {
  activity: RunActivity;
}): ReactElement {
  const [activeTab, setActiveTab] = useState<ActivityTab>("overview");
  const activeSubagent = useMemo(
    () =>
      activity.subagents.find((subagent) => subagent.id === activeTab) ?? null,
    [activeTab, activity.subagents],
  );

  useEffect(() => {
    if (
      activeTab !== "overview" &&
      !activity.subagents.some((subagent) => subagent.id === activeTab)
    ) {
      setActiveTab("overview");
    }
  }, [activeTab, activity.subagents]);

  return (
    <Card tone="muted" className="run-activity-panel">
      <RunStatusHeader activity={activity} />
      {activity.subagents.length > 0 ? (
        <SubagentTabStrip
          subagents={activity.subagents}
          activeTab={activeTab}
          onSelect={setActiveTab}
        />
      ) : null}
      {activeSubagent ? (
        <SubagentActivityPanel subagent={activeSubagent} />
      ) : (
        <section
          className="run-activity-panel__body"
          aria-label="Run activity overview"
        >
          {shouldShowReasoning(activity.reasoning.length) ? (
            <ReasoningSummaryStream
              items={activity.reasoning}
              emptyText="Waiting for reasoning updates..."
            />
          ) : null}
          <ActivityRows events={activity.events} tools={activity.tools} />
        </section>
      )}
      <footer className="run-activity-panel__footer">
        {footerSummary(activity)}
      </footer>
    </Card>
  );
}

export function RunStatusHeader({
  activity,
}: {
  activity: RunActivity;
}): ReactElement {
  return (
    <header className="run-status-header">
      <div className="run-status-header__title">
        <StatusDot status={activity.status} />
        <div>
          <strong>{activity.title}</strong>
          {activity.summary ? <p>{activity.summary}</p> : null}
        </div>
      </div>
      <Badge tone={badgeTone(activity.status)}>
        {labelForStatus(activity.status)}
      </Badge>
    </header>
  );
}

export function SubagentTabStrip({
  subagents,
  activeTab,
  onSelect,
}: {
  subagents: SubagentActivity[];
  activeTab: ActivityTab;
  onSelect: (value: ActivityTab) => void;
}): ReactElement {
  return (
    <div
      className="subagent-tab-strip"
      role="tablist"
      aria-label="Subagent activity"
    >
      <button
        type="button"
        role="tab"
        aria-selected={activeTab === "overview"}
        className={activeTab === "overview" ? "is-active" : undefined}
        onClick={() => onSelect("overview")}
      >
        Overview
      </button>
      {subagents.map((subagent) => (
        <button
          key={subagent.id}
          type="button"
          role="tab"
          aria-selected={activeTab === subagent.id}
          className={activeTab === subagent.id ? "is-active" : undefined}
          onClick={() => onSelect(subagent.id)}
        >
          <StatusDot status={subagent.status} />
          {tabLabel(subagent, subagents)}
        </button>
      ))}
    </div>
  );
}

export function SubagentActivityPanel({
  subagent,
}: {
  subagent: SubagentActivity;
}): ReactElement {
  return (
    <section
      className="subagent-activity-panel"
      aria-label={`${subagent.name} subagent activity`}
    >
      <header>
        <div>
          <span className="app-eyebrow">Subagent</span>
          <h3>{subagent.name}</h3>
          {subagent.summary ? (
            <p title={subagent.summary}>{compactText(subagent.summary)}</p>
          ) : null}
        </div>
        <Badge tone={badgeTone(subagent.status)}>
          {labelForStatus(subagent.status)}
        </Badge>
      </header>
      {shouldShowReasoning(subagent.reasoning.length) ? (
        <ReasoningSummaryStream
          items={subagent.reasoning}
          emptyText="No reasoning summaries yet."
        />
      ) : null}
      <ActivityRows events={subagent.events} tools={subagent.tools} />
    </section>
  );
}

export function ReasoningSummaryStream({
  items,
  emptyText,
}: {
  items: Array<{ id: string; text: string }>;
  emptyText: string;
}): ReactElement {
  return (
    <section
      className="reasoning-summary-stream"
      aria-label="Reasoning summary"
    >
      <h3>Thinking</h3>
      {items.length > 0 ? (
        <div className="reasoning-summary-stream__items">
          {items.map((item) => (
            <Streamdown
              key={item.id}
              className="reasoning-markdown"
              mode="streaming"
            >
              {item.text}
            </Streamdown>
          ))}
        </div>
      ) : (
        <p>{emptyText}</p>
      )}
    </section>
  );
}

export function ToolCallRow({
  tool,
}: {
  tool: ToolCallActivity;
}): ReactElement {
  return (
    <div className="tool-call-row">
      <StatusDot status={tool.status} />
      <div>
        <strong>{tool.name}</strong>
        {tool.summary ? <p>{tool.summary}</p> : null}
        {tool.result ? (
          <p className="tool-call-row__result">{tool.result}</p>
        ) : null}
      </div>
      <Badge tone={badgeTone(tool.status)}>{labelForStatus(tool.status)}</Badge>
    </div>
  );
}

export function ActivityEventRow({
  event,
}: {
  event: ActivityEvent;
}): ReactElement {
  return (
    <div className="activity-event-row">
      <StatusDot status={event.status} />
      <div>
        <strong>{event.title}</strong>
        {event.summary ? <p>{event.summary}</p> : null}
      </div>
    </div>
  );
}

function ActivityRows({
  events,
  tools,
}: {
  events: ActivityEvent[];
  tools: ToolCallActivity[];
}): ReactElement {
  const visibleEvents = events.filter(
    (event) => !isInternalActivityEvent(event),
  );
  if (visibleEvents.length === 0 && tools.length === 0) {
    return (
      <div className="activity-event-row">
        <StatusDot status="running" />
        <p>Waiting for tool calls or progress events...</p>
      </div>
    );
  }
  return (
    <section className="activity-rows" aria-label="Activity rows">
      {visibleEvents.map((event) => (
        <ActivityEventRow key={event.id} event={event} />
      ))}
      {tools.map((tool) => (
        <ToolCallRow key={tool.id} tool={tool} />
      ))}
    </section>
  );
}

function isInternalActivityEvent(event: ActivityEvent): boolean {
  return event.eventType === "subagent_progress" && !event.summary;
}

function StatusDot({ status }: { status: ActivityStatus }): ReactElement {
  return (
    <span
      className={`activity-status-dot activity-status-dot--${status}`}
      aria-hidden="true"
    />
  );
}

function footerSummary(activity: RunActivity): string {
  const runningSubagents = activity.subagents.filter(
    (subagent) => subagent.status === "running",
  ).length;
  const completedSubagents = activity.subagents.filter(
    (subagent) => subagent.status === "completed",
  ).length;
  if (activity.subagents.length === 0) {
    return activity.status === "running"
      ? "Agent is working"
      : labelForStatus(activity.status);
  }
  if (runningSubagents > 0) {
    return `${runningSubagents} subagent${runningSubagents === 1 ? "" : "s"} running`;
  }
  return `${completedSubagents}/${activity.subagents.length} subagents completed`;
}

function shouldShowReasoning(itemCount: number): boolean {
  return itemCount > 0;
}

function tabLabel(
  subagent: SubagentActivity,
  subagents: SubagentActivity[],
): string {
  const matching = subagents.filter(
    (candidate) => candidate.name === subagent.name,
  );
  if (matching.length === 1) {
    return subagent.name;
  }
  return `${subagent.name} ${matching.indexOf(subagent) + 1}`;
}

function compactText(text: string, maxLength = 160): string {
  const normalized = text.replace(/\s+/g, " ").trim();
  if (normalized.length <= maxLength) {
    return normalized;
  }
  return `${normalized.slice(0, maxLength - 1)}...`;
}

function badgeTone(
  status: ActivityStatus,
): "neutral" | "success" | "warning" | "danger" | "accent" {
  if (status === "completed") {
    return "success";
  }
  if (status === "failed" || status === "cancelled") {
    return "danger";
  }
  if (status === "waiting" || status === "queued") {
    return "warning";
  }
  if (status === "running") {
    return "accent";
  }
  return "neutral";
}

function labelForStatus(status: ActivityStatus): string {
  return status.charAt(0).toUpperCase() + status.slice(1);
}
