import type { ReactElement } from "react";

import type { SaaSRendererAdapter } from "@0x-copilot/chat-surface";

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
import { formatValue, isNumericFormat, resolvePath } from "../_shared/path";
import {
  changesFromDiff,
  dataFromState,
  specFromState,
  type SurfaceDiff,
  type SurfaceField,
  type SurfaceSpec,
  type SurfaceState,
} from "../_shared/specTypes";

const KICKER = "Record";

/**
 * The `record://` archetype — a single resource as a title/subtitle header over
 * a label/value field grid (the OpportunityRenderer grammar, spec-driven).
 * Spec-less state renders a "Preparing view…" hint + generic field list; a
 * malformed spec/data never throws.
 */
export function RecordRenderer(state: SurfaceState | unknown): ReactElement {
  const spec = specFromState(state);
  const data = dataFromState(state);
  return (
    <article
      style={pageStyle}
      data-testid="record-renderer"
      data-mode="current"
      data-spec={spec ? "present" : "absent"}
      aria-label="Record surface"
    >
      <section style={cardStyle}>
        {spec ? renderWithSpec(spec, data) : renderFallback(data)}
      </section>
    </article>
  );
}

function renderWithSpec(spec: SurfaceSpec, data: unknown): ReactElement {
  const title = formatValue(resolvePath(data, spec.title_path));
  const subtitle = spec.subtitle_path
    ? formatValue(resolvePath(data, spec.subtitle_path))
    : undefined;
  const fields: readonly SurfaceField[] = spec.fields ?? [];
  return (
    <>
      <SurfaceHeader kicker={KICKER} title={title} subtitle={subtitle} />
      {fields.length > 0 ? (
        <div style={fieldGridStyle}>
          {fields.map((field, index) => (
            <FieldRow
              key={`${field.path}:${index}`}
              fieldKey={field.path}
              label={field.label}
              value={formatValue(resolvePath(data, field.path), field.format)}
              numeric={isNumericFormat(field.format)}
            />
          ))}
        </div>
      ) : (
        <EmptyBody>No fields configured.</EmptyBody>
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
      <SurfaceHeader kicker={KICKER} title="Record" />
      <PreparingHint />
      <GenericFieldList data={data} format={(v) => formatValue(v)} />
    </>
  );
}

/**
 * Diff view — one before→after row per proposed change, mapped back to the
 * spec's field labels where available. Struck-through old, accent-highlighted
 * new (PRD-03 AC4).
 */
export function RecordDiffRenderer(diff: SurfaceDiff | unknown): ReactElement {
  const spec = specFromState(diff);
  const changes = changesFromDiff(diff);
  const labelFor = buildLabelMap(spec);
  return (
    <article
      style={pageStyle}
      data-testid="record-renderer"
      data-mode="diff"
      aria-label="Record surface — proposed changes"
    >
      <section style={cardStyle}>
        <SurfaceHeader
          kicker={KICKER}
          title="Proposed changes"
          badge={`${changes.length} field${changes.length === 1 ? "" : "s"}`}
        />
        {changes.length > 0 ? (
          <div style={fieldGridStyle} data-testid="record-diff-rows">
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
      </section>
    </article>
  );
}

function buildLabelMap(spec: SurfaceSpec | undefined): Map<string, string> {
  const map = new Map<string, string>();
  for (const field of spec?.fields ?? []) {
    map.set(field.path, field.label);
  }
  return map;
}

export const recordAdapter: SaaSRendererAdapter<SurfaceState, SurfaceDiff> = {
  scheme: "record",
  matches: (uri: string) => uri.startsWith("record://"),
  renderCurrent: (state: SurfaceState): ReactElement => RecordRenderer(state),
  renderDiff: (diff: SurfaceDiff): ReactElement => RecordDiffRenderer(diff),
  metadata: {
    origin: "first-party",
    schemaVersion: 1,
  },
};
