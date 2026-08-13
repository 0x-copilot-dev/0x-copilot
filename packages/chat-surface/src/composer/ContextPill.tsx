// ContextPill — the composer's context meter, and the breakdown behind it.
//
// Three disclosure levels over one control:
//
//   L0  the pill itself: a 22px gauge + the server's headroom percent. Quiet at
//       rest, warm below 40% headroom, ember below 15%.
//   L1  hover: input · cached · free, so the common question needs no click.
//   L2  click: the 300px breakdown, grouped by LIFECYCLE and coloured by CLASS.
//
// Why lifecycle owns the grouping: `segment_class` is a structural taxonomy
// (system / tools / messages / response_format) and answers "what kind of bytes
// are these". `lifecycle` answers "what happens if I do nothing" — `resident`
// bytes are rent charged on every call and are fixed by trimming a surface,
// `per_result` bytes are a multiplier on tool-call count and are fixed by
// shrinking one note. Read by class, two connectors whose schemas cost 17% of
// the window every single turn are an unremarkable "tools 19%".
//
// PRESENTATIONAL. It fetches nothing and owns no state but `open`; the host
// binder supplies a {@link ContextPillView} built by `contextPillView.ts`.

import { Menu } from "@0x-copilot/design-system";
import { useRef, useState, type ReactElement } from "react";

import { Icon } from "../icons/Icon";
import type {
  ContextBarSlice,
  ContextLifecycleGroup,
  ContextPillView,
  ContextSegmentRow,
} from "./contextPillView";

/** Hue per `ContextSegmentClass`, resolved from the design-system's context
 *  ramp. `null` is the unattributed provider delta, which is not a class. */
const CLASS_VAR: Record<string, string> = {
  system: "var(--color-ctx-system)",
  tools: "var(--color-ctx-tools)",
  messages: "var(--color-ctx-messages)",
  response_format: "var(--color-ctx-response-format)",
};
const UNATTRIBUTED_VAR = "var(--color-ctx-unattributed)";

export interface ContextPillProps {
  /** The meter's data. Build it with `buildContextPillView`; a `null` view
   *  means nothing has been measured, and the host should render no pill at
   *  all rather than passing a zeroed one. */
  readonly view: ContextPillView;
  readonly disabled?: boolean;
  /**
   * Open the full report (`/context`'s by_call + by_subagent + the per-turn
   * occupancy series). Host-owned navigation — omitted renders no link, never
   * a dead one.
   */
  readonly onOpenReport?: () => void;
}

export function ContextPill({
  view,
  disabled,
  onOpenReport,
}: ContextPillProps): ReactElement {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement | null>(null);

  const known = view.headroomPct !== null;
  // Unknown window (`context_window_tokens: null` — the model is absent from
  // the pricing catalogue) has no percent to state, so the pill falls back to
  // the one figure that is still true: how many tokens went in.
  const primary = known
    ? `${String(view.headroomPct)}%`
    : formatCompact(view.inputTokens);
  const unit = known ? "free" : "in";

  return (
    <div className="atlas-ctx-pill__root">
      <button
        ref={buttonRef}
        type="button"
        className="ui-cpill atlas-ctx-pill"
        data-state={view.pressure}
        data-open={open || undefined}
        data-testid="context-pill"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={pillLabel(view, known)}
        disabled={disabled}
        data-tooltip={tooltipLine(view)}
        // Right-cluster control: a centred tooltip hangs past the composer's
        // edge, and past the whole column in the ~300px Run rail. Same reason
        // the mic and the model pill set an alignment.
        data-tooltip-align="end"
        data-tooltip-placement="top"
        onClick={() => setOpen((current) => !current)}
      >
        <ContextGauge slices={view.slices} known={known} />
        <span className="atlas-ctx-pill__num">{primary}</span>
        <span className="atlas-ctx-pill__unit">{unit}</span>
      </button>

      <Menu
        open={open}
        onClose={() => setOpen(false)}
        anchorRef={buttonRef}
        side="up"
        align="right"
        className="ui-pop atlas-ctx-pop"
      >
        <ContextBreakdown view={view} onOpenReport={onOpenReport} />
      </Menu>
    </div>
  );
}

/**
 * The meter. Consumed slices are drawn; the remaining track IS the headroom the
 * number names — so the bar and the percent are two independent server values
 * that agree, and no `100 - headroom` arithmetic happens on the client.
 *
 * With an unknown window there are no shares to draw, so the gauge degrades to
 * an empty track rather than a full or a zero one: both would be a claim.
 *
 * `aria-hidden` because the button's own `aria-label` already states the
 * numbers — a screen reader should hear one meter, not a bar and a label.
 */
function ContextGauge({
  slices,
  known,
}: {
  readonly slices: readonly ContextBarSlice[];
  readonly known: boolean;
}): ReactElement {
  return (
    <span className="atlas-ctx-gauge" aria-hidden="true" data-known={known}>
      {slices.map((slice) => (
        <i
          key={slice.key}
          style={{
            width: `${String(slice.pct)}%`,
            background: sliceColor(slice),
            opacity: slice.tone,
          }}
        />
      ))}
    </span>
  );
}

function ContextBreakdown({
  view,
  onOpenReport,
}: {
  readonly view: ContextPillView;
  readonly onOpenReport?: () => void;
}): ReactElement {
  return (
    <div className="atlas-ctx-pop__body" data-testid="context-breakdown">
      <div className="atlas-ctx-pop__head">
        <span className="atlas-ctx-pop__title">Context</span>
        <span className="atlas-ctx-pop__model">
          {view.modelLabel}
          {view.windowTokens !== null
            ? ` · ${formatCompact(view.windowTokens)}`
            : ""}
        </span>
      </div>

      <div className="atlas-ctx-pop__summary">
        <span className="atlas-ctx-bar" aria-hidden="true">
          {view.slices.map((slice) => (
            <i
              key={slice.key}
              style={{
                width: `${String(slice.pct)}%`,
                background: sliceColor(slice),
                opacity: slice.tone,
              }}
            />
          ))}
        </span>
        <p className="atlas-ctx-pop__figs">
          <b>{formatFull(view.inputTokens)}</b> in
          <span className="atlas-ctx-pop__sep">·</span>
          <b>{formatFull(view.cachedTokens)}</b> cached
          {view.freeTokens !== null ? (
            <>
              <span className="atlas-ctx-pop__sep">·</span>
              <b>{formatFull(view.freeTokens)}</b> free
            </>
          ) : null}
        </p>
      </div>

      {/* Expected 0. Above it, a first-party contract defect — measured bytes
          that no declaration covers. It lives here and never on the pill: it is
          our bug, and it does not change what the user should send. */}
      {view.undeclaredTokens > 0 ? (
        <p className="atlas-ctx-pop__defect" data-testid="context-undeclared">
          <Icon name="warn" size={11} />
          <span>
            <b>{formatFull(view.undeclaredTokens)} undeclared</b> — measured
            bytes matching no declaration.
          </span>
        </p>
      ) : null}

      {view.groups.map((group) => (
        <ContextGroup key={group.lifecycle} group={group} />
      ))}

      {view.unattributedDelta !== 0 ? (
        <p className="atlas-ctx-row atlas-ctx-row--delta">
          <span
            className="atlas-ctx-row__sw"
            aria-hidden="true"
            style={{ background: UNATTRIBUTED_VAR }}
          />
          <span className="atlas-ctx-row__label">
            <s>provider overhead</s>
          </span>
          <span className="atlas-ctx-row__tok">
            {signed(view.unattributedDelta)}
          </span>
          <span className="atlas-ctx-row__pct" />
        </p>
      ) : null}

      {view.compaction !== null || onOpenReport !== undefined ? (
        <div className="atlas-ctx-pop__foot">
          <span className="atlas-ctx-pop__note">
            {view.compaction !== null
              ? `Compacted ${formatCompact(view.compaction.before)} → ${formatCompact(view.compaction.after)}`
              : ""}
          </span>
          {onOpenReport !== undefined ? (
            <button
              type="button"
              className="atlas-ctx-pop__link"
              onClick={onOpenReport}
              data-testid="context-open-report"
            >
              Report
              <Icon name="external" size={10} />
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function ContextGroup({
  group,
}: {
  readonly group: ContextLifecycleGroup;
}): ReactElement {
  return (
    <>
      <p className="atlas-ctx-grp">
        <span>{group.label}</span>
        {group.note !== null ? <em>{group.note}</em> : null}
      </p>
      {group.rows.map((row) => (
        <ContextRow key={row.key} row={row} />
      ))}
    </>
  );
}

function ContextRow({
  row,
}: {
  readonly row: ContextSegmentRow;
}): ReactElement {
  // The fold's remainder is a SUM over several declarations, so it carries no
  // markers: a `3P` chip on it would claim every folded row is third-party.
  const summary = row.remainder === true;
  return (
    <p
      className="atlas-ctx-row"
      data-remainder={summary || undefined}
      data-testid={`context-row-${row.label}`}
    >
      <span
        className="atlas-ctx-row__sw"
        aria-hidden="true"
        style={{
          background: CLASS_VAR[row.segmentClass] ?? UNATTRIBUTED_VAR,
          opacity: row.tone,
        }}
      />
      <span className="atlas-ctx-row__label">
        {row.label}
        {row.detail !== null ? <s> · {row.detail}</s> : null}
      </span>
      {/* Three markers, each carrying a contract the number alone cannot.
          `3P`: bytes the user did not author and can remove by disconnecting.
          `⌾`:  a cacheable stable prefix — bills at roughly a tenth, so "large
                but cached" is a different finding from "large and re-billed".
          `≈`:  counter_source "proxy", the fail-open signature. The ledger took
                a worse number over failing the run, and the row says so. */}
      {row.thirdParty && !summary ? (
        <span className="atlas-ctx-mk atlas-ctx-mk--3p" title="Third-party">
          3P
        </span>
      ) : null}
      {row.cacheable && !summary ? (
        <span
          className="atlas-ctx-mk atlas-ctx-mk--cache"
          title="Cached prefix"
        >
          ⌾
        </span>
      ) : null}
      {row.approximate && !summary ? (
        <span className="atlas-ctx-mk atlas-ctx-mk--prox" title="Estimated">
          ≈
        </span>
      ) : null}
      <span className="atlas-ctx-row__tok">{formatFull(row.tokens)}</span>
      <span className="atlas-ctx-row__pct">{formatShare(row.pctOfWindow)}</span>
    </p>
  );
}

function sliceColor(slice: ContextBarSlice): string {
  if (slice.segmentClass === null) return UNATTRIBUTED_VAR;
  return CLASS_VAR[slice.segmentClass] ?? UNATTRIBUTED_VAR;
}

function pillLabel(view: ContextPillView, known: boolean): string {
  const head = known
    ? `Context: ${String(view.headroomPct)}% of the window free`
    : `Context: ${formatFull(view.inputTokens)} tokens in, window size unknown`;
  return `${head}. Open breakdown.`;
}

/** The one-line hover readout. Cached is on it deliberately: a cached prefix
 *  bills at roughly a tenth, so omitting it makes a large-but-cached surface
 *  look like a problem it is not. */
function tooltipLine(view: ContextPillView): string {
  const parts = [
    `${formatFull(view.inputTokens)} in`,
    `${formatFull(view.cachedTokens)} cached`,
  ];
  if (view.freeTokens !== null) {
    parts.push(`${formatFull(view.freeTokens)} free`);
  }
  return parts.join(" · ");
}

/** Grouped thousands, locale-independent so a test asserts one string and a
 *  user in any locale reads the same figure as the docs. */
function formatFull(value: number): string {
  const sign = value < 0 ? "-" : "";
  const digits = Math.abs(Math.round(value)).toString();
  let out = "";
  for (let i = 0; i < digits.length; i += 1) {
    if (i > 0 && (digits.length - i) % 3 === 0) out += ",";
    out += digits[i];
  }
  return `${sign}${out}`;
}

/** Pill/header form: 200000 -> "200k", 79240 -> "79.2k". */
function formatCompact(value: number): string {
  const abs = Math.abs(value);
  if (abs < 1000) return formatFull(value);
  const thousands = value / 1000;
  // Drop the decimal once it stops carrying information at this size.
  const text =
    Math.abs(thousands) >= 100
      ? Math.round(thousands).toString()
      : (Math.round(thousands * 10) / 10).toString();
  return `${text}k`;
}

function signed(value: number): string {
  return value > 0 ? `+${formatFull(value)}` : formatFull(value);
}

/** `null` share = unknown window, so the cell is empty rather than "0%".
 *  Anything that rounds to zero but is not zero reads "<1%". */
function formatShare(pct: number | null): string {
  if (pct === null) return "";
  const rounded = Math.round(pct);
  if (rounded === 0) return pct > 0 ? "<1%" : "0%";
  return `${String(rounded)}%`;
}
