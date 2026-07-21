import type { CSSProperties, ReactElement } from "react";

import type { SaaSRendererAdapter } from "@0x-copilot/chat-surface";

import { SURFACE_PALETTE as PALETTE } from "../_shared/palette";
import {
  cardStyle,
  DiffFieldRow,
  EmptyBody,
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

const KICKER = "Document";

/** Cap on sections painted — render-budget guard (PRD-03). */
export const SECTION_RENDER_CAP = 200;

/**
 * The `doc://` archetype — a title over a sections list. Each section item
 * (from `items_path`) renders its first spec field as a heading and the rest as
 * body. Spec-less state falls back to the generic list.
 */
export function DocRenderer(state: SurfaceState | unknown): ReactElement {
  const spec = specFromState(state);
  const data = dataFromState(state);
  return (
    <article
      style={pageStyle}
      data-testid="doc-renderer"
      data-mode="current"
      data-spec={spec ? "present" : "absent"}
      aria-label="Document surface"
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
  const rawSections = spec.items_path
    ? resolvePath(data, spec.items_path)
    : undefined;
  const sections = Array.isArray(rawSections)
    ? rawSections.slice(0, SECTION_RENDER_CAP)
    : [];
  const fields: readonly SurfaceField[] = spec.fields ?? [];
  const [headingField, ...bodyFields] = fields;

  return (
    <>
      <SurfaceHeader kicker={KICKER} title={title} subtitle={subtitle} />
      {sections.length > 0 ? (
        <div style={sectionsStyle} data-testid="doc-sections">
          {sections.map((section, index) => (
            <div
              key={index}
              style={sectionStyle}
              data-testid={`doc-section-${index}`}
            >
              <h3 style={sectionHeadingStyle}>
                {headingField
                  ? formatValue(
                      resolvePath(section, headingField.path),
                      headingField.format,
                    )
                  : formatValue(
                      resolvePath(section, "heading") ??
                        resolvePath(section, "title"),
                    ) || `Section ${index + 1}`}
              </h3>
              {(bodyFields.length > 0 ? bodyFields : []).map(
                (field, bodyIndex) => (
                  <p
                    key={`${field.path}:${bodyIndex}`}
                    style={sectionBodyStyle}
                    data-testid={`doc-section-${index}-body-${bodyIndex}`}
                  >
                    {formatValue(
                      resolvePath(section, field.path),
                      field.format,
                    )}
                  </p>
                ),
              )}
              {bodyFields.length === 0 ? (
                <p style={sectionBodyStyle}>
                  {formatValue(
                    resolvePath(section, "body") ??
                      resolvePath(section, "content"),
                  )}
                </p>
              ) : null}
            </div>
          ))}
        </div>
      ) : (
        <EmptyBody>No sections to display.</EmptyBody>
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
      <SurfaceHeader kicker={KICKER} title="Document" />
      <PreparingHint />
      <GenericFieldList data={data} format={(v) => formatValue(v)} />
    </>
  );
}

/** Diff view — one before→after row per changed section/field. */
export function DocDiffRenderer(diff: SurfaceDiff | unknown): ReactElement {
  const spec = specFromState(diff);
  const changes = changesFromDiff(diff);
  const labelFor = new Map<string, string>(
    (spec?.fields ?? []).map((field) => [field.path, field.label]),
  );
  return (
    <article
      style={pageStyle}
      data-testid="doc-renderer"
      data-mode="diff"
      aria-label="Document surface — proposed changes"
    >
      <section style={cardStyle}>
        <SurfaceHeader
          kicker={KICKER}
          title="Proposed changes"
          badge={`${changes.length} change${changes.length === 1 ? "" : "s"}`}
        />
        {changes.length > 0 ? (
          <div style={fieldGridStyle} data-testid="doc-diff-rows">
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

export const docAdapter: SaaSRendererAdapter<SurfaceState, SurfaceDiff> = {
  scheme: "doc",
  matches: (uri: string) => uri.startsWith("doc://"),
  renderCurrent: (state: SurfaceState): ReactElement => DocRenderer(state),
  renderDiff: (diff: SurfaceDiff): ReactElement => DocDiffRenderer(diff),
  metadata: {
    origin: "first-party",
    schemaVersion: 1,
  },
};

const sectionsStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 14,
};

const sectionStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  paddingBottom: 12,
  borderBottom: `1px solid ${PALETTE.border}`,
};

const sectionHeadingStyle: CSSProperties = {
  margin: 0,
  fontSize: 14,
  fontWeight: 600,
  color: PALETTE.textHi,
  overflowWrap: "anywhere",
};

const sectionBodyStyle: CSSProperties = {
  margin: 0,
  fontSize: 13,
  lineHeight: 1.5,
  color: PALETTE.textMid,
  whiteSpace: "pre-wrap",
  overflowWrap: "anywhere",
};
