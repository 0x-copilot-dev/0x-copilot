import type { CSSProperties, ReactElement } from "react";

import type { SaaSRendererAdapter } from "@0x-copilot/chat-surface";

import { SURFACE_PALETTE as PALETTE } from "../_shared/palette";
import {
  cardStyle,
  DiffFieldRow,
  EmptyBody,
  FieldRow,
  fieldGridStyle,
  GenericFieldList,
  pageStyle,
  PreparingHint,
  SurfaceHeader,
  SurfaceLinkRow,
} from "../_shared/primitives";
import { formatValue, resolvePath } from "../_shared/path";
import {
  changesFromDiff,
  dataFromState,
  specFromState,
  type SurfaceDiff,
  type SurfaceField,
  type SurfaceSpec,
  type SurfaceState,
} from "../_shared/specTypes";

const KICKER = "Message";

/**
 * The `message://` archetype — a composer-card layout (subject/from/to/body
 * from spec paths). Spec-less state falls back to the generic list; the diff
 * view renders a PENDING body block echoing the EmailRenderer treatment
 * (PRD-06 upgrades it to a word diff).
 */
export function MessageRenderer(state: SurfaceState | unknown): ReactElement {
  const spec = specFromState(state);
  const data = dataFromState(state);
  return (
    <article
      style={pageStyle}
      data-testid="message-renderer"
      data-mode="current"
      data-spec={spec ? "present" : "absent"}
      aria-label="Message surface"
    >
      <section style={cardStyle}>
        {spec ? renderWithSpec(spec, data) : renderFallback(data)}
      </section>
    </article>
  );
}

function renderWithSpec(spec: SurfaceSpec, data: unknown): ReactElement {
  const subject = formatValue(resolvePath(data, spec.title_path));
  const from = spec.subtitle_path
    ? formatValue(resolvePath(data, spec.subtitle_path))
    : undefined;
  const fields: readonly SurfaceField[] = spec.fields ?? [];
  return (
    <>
      <SurfaceHeader kicker={KICKER} title={subject} subtitle={from} />
      {fields.length > 0 ? (
        <div style={fieldGridStyle} data-testid="message-fields">
          {fields.map((field, index) => (
            <FieldRow
              key={`${field.path}:${index}`}
              fieldKey={field.path}
              label={field.label}
              value={formatValue(resolvePath(data, field.path), field.format)}
            />
          ))}
        </div>
      ) : (
        <EmptyBody>No message fields configured.</EmptyBody>
      )}
      {spec.link ? (
        <SurfaceLinkRow
          label={spec.link.label}
          value={resolvePath(data, spec.link.url_path)}
        />
      ) : null}
    </>
  );
}

function renderFallback(data: unknown): ReactElement {
  return (
    <>
      <SurfaceHeader kicker={KICKER} title="Message" />
      <PreparingHint />
      <GenericFieldList data={data} format={(v) => formatValue(v)} />
    </>
  );
}

/** Diff view — a PENDING body block over the proposed field changes. */
export function MessageDiffRenderer(diff: SurfaceDiff | unknown): ReactElement {
  const spec = specFromState(diff);
  const changes = changesFromDiff(diff);
  const labelFor = new Map<string, string>(
    (spec?.fields ?? []).map((field) => [field.path, field.label]),
  );
  return (
    <article
      style={pageStyle}
      data-testid="message-renderer"
      data-mode="diff"
      aria-label="Message surface — pending edit"
    >
      <section style={cardStyle}>
        <SurfaceHeader kicker={KICKER} title="Pending message edit" />
        <div
          style={pendingBlockStyle}
          data-testid="message-pending-block"
          data-state="pending"
          aria-label="Pending edit"
        >
          <span style={pendingLabelStyle} data-testid="message-pending-label">
            PENDING · Proposed
          </span>
          {changes.length > 0 ? (
            <div style={fieldGridStyle} data-testid="message-diff-rows">
              {changes.map((change, index) => (
                <DiffFieldRow
                  key={`${change.field}:${index}`}
                  fieldKey={change.field}
                  label={labelFor.get(change.field) ?? change.field}
                  previousValue={formatValue(change.old)}
                  nextValue={formatValue(change.new)}
                />
              ))}
            </div>
          ) : (
            <EmptyBody>No pending changes.</EmptyBody>
          )}
        </div>
      </section>
    </article>
  );
}

export const messageAdapter: SaaSRendererAdapter<SurfaceState, SurfaceDiff> = {
  scheme: "message",
  matches: (uri: string) => uri.startsWith("message://"),
  renderCurrent: (state: SurfaceState): ReactElement => MessageRenderer(state),
  renderDiff: (diff: SurfaceDiff): ReactElement => MessageDiffRenderer(diff),
  metadata: {
    origin: "first-party",
    schemaVersion: 1,
  },
};

const pendingBlockStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  padding: 12,
  borderRadius: 10,
  background: PALETTE.limeBgSoft,
  border: `1px solid ${PALETTE.lime}`,
};

const pendingLabelStyle: CSSProperties = {
  fontSize: 11,
  letterSpacing: 0.6,
  textTransform: "uppercase",
  color: PALETTE.textLo,
  fontWeight: 600,
};
