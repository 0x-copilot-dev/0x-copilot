// Webhooks sub-destination — manages the webhook lifecycle that
// Routines §9.7 Q6 registered. Lives under /connectors/webhooks per
// connectors-prd §7.3.
//
// Composition:
//   1. <PageHeader> — title + "Add webhook" CTA.
//   2. <DocList> of <WebhookCard>s, or an <EmptyState> when zero.
//
// Pure presentation: host owns transport + routing.

import { type CSSProperties, type ReactElement } from "react";

import type { SectionResult, TriggerId } from "@enterprise-search/api-types";
import type { Webhook } from "@enterprise-search/api-types/src/connectors";

import { DocList } from "../../../shell/DocList";
import { EmptyState } from "../../../shell/EmptyState";
import { PageHeader } from "../../../shell/PageHeader";

import { WebhookCard } from "./WebhookCard";

export interface WebhooksDestinationProps {
  /**
   * Server-projected list. `null` = loading skeleton; SectionResult
   * wrapper for uniform error handling.
   */
  readonly items?: SectionResult<ReadonlyArray<Webhook>> | null;
  /** "Add webhook" CTA — opens the create wizard. */
  readonly onCreate?: () => void;
  /** Card click — host wires deep-link to webhook detail. */
  readonly onOpenWebhook?: (id: TriggerId) => void;
  /** Retry when items.status === "error". */
  readonly onRetry?: () => void;
  /** Test seam for relative-time formatting. */
  readonly now?: number;
}

export function WebhooksDestination(
  props: WebhooksDestinationProps = {},
): ReactElement {
  const { items = null, onCreate, onOpenWebhook, onRetry, now } = props;

  return (
    <section
      role="region"
      aria-label="Webhooks"
      data-component="webhooks-destination"
      style={rootStyle}
    >
      <div style={innerStyle}>
        <PageHeader
          title="Webhooks"
          subtitle="Signed HTTP receivers — routines fire these to your endpoints."
          primaryAction={
            onCreate !== undefined
              ? { label: "Add webhook", onClick: onCreate }
              : undefined
          }
        />
        <div data-testid="webhooks-body" style={bodyStyle}>
          {renderBody({ items, onCreate, onOpenWebhook, onRetry, now })}
        </div>
      </div>
    </section>
  );
}

interface BodyArgs {
  readonly items: WebhooksDestinationProps["items"];
  readonly onCreate: WebhooksDestinationProps["onCreate"];
  readonly onOpenWebhook: WebhooksDestinationProps["onOpenWebhook"];
  readonly onRetry: WebhooksDestinationProps["onRetry"];
  readonly now: WebhooksDestinationProps["now"];
}

function renderBody(args: BodyArgs): ReactElement {
  const { items, onCreate, onOpenWebhook, onRetry, now } = args;

  if (items === null || items === undefined) {
    return (
      <div data-testid="webhooks-skeleton" style={skeletonStackStyle}>
        {[0, 1, 2].map((i) => (
          <div key={i} style={skeletonCardStyle} aria-hidden="true">
            <span style={skeletonBarStyle(50)} />
            <span style={skeletonBarStyle(80)} />
          </div>
        ))}
      </div>
    );
  }
  if (items.status === "error" || items.status === "unavailable") {
    return (
      <EmptyState
        title={
          items.status === "unavailable"
            ? "Webhooks unavailable"
            : "Couldn't load webhooks"
        }
        body={items.error ?? undefined}
        action={
          onRetry !== undefined
            ? { label: "Retry", onClick: onRetry }
            : undefined
        }
      />
    );
  }
  const rows = items.data ?? [];
  if (rows.length === 0) {
    return (
      <EmptyState
        title="No webhooks yet"
        body="Add a signed receiver so routines can fire HMAC-protected webhooks at your endpoints."
        action={
          onCreate !== undefined
            ? { label: "Add webhook", onClick: onCreate }
            : undefined
        }
      />
    );
  }
  return (
    <DocList
      ariaLabel="Webhooks"
      items={rows}
      keyFor={(w) => w.id}
      renderRow={(w) => (
        <WebhookCard
          webhook={w}
          now={now}
          onClick={
            onOpenWebhook !== undefined ? () => onOpenWebhook(w.id) : undefined
          }
        />
      )}
    />
  );
}

// === Styles ============================================================

const rootStyle: CSSProperties = {
  width: "100%",
  height: "100%",
  minHeight: 0,
  background: "var(--color-bg, #131316)",
  color: "var(--color-text, #ededee)",
  boxSizing: "border-box",
  display: "flex",
  flexDirection: "column",
  overflow: "auto",
};

const innerStyle: CSSProperties = {
  width: "100%",
  maxWidth: 1080,
  margin: "0 auto",
  padding: "16px 20px 32px",
  boxSizing: "border-box",
  display: "flex",
  flexDirection: "column",
  gap: 12,
};

const bodyStyle: CSSProperties = {
  flex: 1,
  minHeight: 0,
  padding: "8px 0",
};

const skeletonStackStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
};

const skeletonCardStyle: CSSProperties = {
  padding: 14,
  background: "var(--color-bg-elevated, #18181b)",
  border: "1px solid var(--color-border, #232325)",
  borderRadius: "var(--radius-md, 12px)",
  display: "flex",
  flexDirection: "column",
  gap: 10,
  minHeight: 80,
};

function skeletonBarStyle(widthPercent: number): CSSProperties {
  return {
    display: "inline-block",
    width: `${widthPercent}%`,
    height: 10,
    borderRadius: 4,
    background: "var(--color-border, #232325)",
  };
}
