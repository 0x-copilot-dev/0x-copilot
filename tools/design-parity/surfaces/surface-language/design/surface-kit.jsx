/* global React, Icon */
/* The one table language + shared surface chrome. Every archetype composes
   these; nothing re-derives a header register or a row height locally. */
const { useState: useK, useMemo: useKM } = React;

const nfmt = (n) =>
  Number(n).toLocaleString("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  });
const cfmt = (n) =>
  Number(n).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

const STATUS_TONE = {
  signed: "ok",
  confirmed: "ok",
  done: "ok",
  staged: "",
  held: "warn",
  blocked: "warn",
  failed: "danger",
  rejected: "danger",
};

function SfStatus({ value }) {
  const tone = STATUS_TONE[String(value).toLowerCase()] ?? "";
  return (
    <span
      className={"sfb" + (tone ? " sfb--" + tone : "")}
      style={{ fontSize: 10, padding: "1px 7px" }}
    >
      {value}
    </span>
  );
}

/* cell register by column format — type carries the meaning, not colour */
function cellClass(col, i) {
  if (
    col.align === "end" ||
    col.format === "currency" ||
    col.format === "number"
  )
    return "n";
  if (col.format === "id" || col.format === "date" || col.format === "datetime")
    return "m";
  return i === 0 ? "k" : "";
}
function cellText(v, col) {
  if (v == null || v === "") return "—";
  if (col.format === "currency") return cfmt(v);
  if (col.format === "number") return nfmt(v);
  return String(v);
}

function SfCard({
  kicker,
  tierLabel,
  tier,
  src,
  title,
  sub,
  chips = [],
  link,
  merge,
  children,
}) {
  return (
    <div
      className="sfc"
      data-merge={merge ? "1" : "0"}
      data-tier={tier}
      style={src ? { "--sf-src": src } : undefined}
    >
      <div className="sfc-h">
        <div className="m">
          <div className="sfc-k">
            <span className="sd" />
            <span>{kicker}</span>
            {tierLabel && (
              <>
                <span className="sep">/</span>
                <span>{tierLabel}</span>
              </>
            )}
          </div>
          <div className="sfc-t">{title}</div>
          {sub && <div className="sfc-s">{sub}</div>}
        </div>
        <div className="sfc-r">
          {chips.map((c, i) => (
            <span key={i} className={"sfb" + (c.tone ? " sfb--" + c.tone : "")}>
              {c.t}
            </span>
          ))}
          {link && (
            <span className="sf-lnk">
              {link}
              <Icon.external />
            </span>
          )}
        </div>
      </div>
      {children}
    </div>
  );
}

function SfToolbar({ filter, setFilter, sort, setSort, cols, right }) {
  return (
    <div className="sf-tools">
      <span className="lb">Filter</span>
      <input
        className="sfin"
        style={{ width: 150 }}
        placeholder="Filter rows…"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      />
      <span className="lb">Sort</span>
      <select
        className="sfsel"
        value={sort}
        onChange={(e) => setSort(e.target.value)}
      >
        <option value="">Original order</option>
        {cols.map((c) => (
          <option key={c.path} value={c.path}>
            {c.label} ↑
          </option>
        ))}
      </select>
      <span className="sp" />
      {right}
    </div>
  );
}

/* changes: [{row, field, old, next}] — a proposed-cell diff, hunk-toggleable */
function SfTable({
  cols,
  rows,
  gutter = true,
  changes = [],
  off = {},
  editable = false,
  edits = {},
  onEdit,
  cap,
  sort = "",
  onSort,
}) {
  const chgFor = useKM(() => {
    const m = {};
    changes.forEach((c) => {
      m[c.row + ":" + c.field] = c;
    });
    return m;
  }, [changes]);
  const chgRows = new Set(changes.map((c) => c.row));
  const maxes = useKM(() => {
    const m = {};
    cols.forEach((c) => {
      if (c.format === "number" || c.format === "currency") {
        m[c.path] = Math.max(
          1,
          ...rows.map((r) => Math.abs(Number(r[c.path])) || 0),
        );
      }
    });
    return m;
  }, [cols, rows]);
  const bar = (r, c) =>
    maxes[c.path] ? (
      <span
        className="sfvb"
        style={{
          width:
            Math.max(
              5,
              Math.round(
                ((Math.abs(Number(r[c.path])) || 0) / maxes[c.path]) * 88,
              ),
            ) + "%",
        }}
      />
    ) : null;
  return (
    <>
      <div className="sft-wrap">
        <table className="sft">
          <thead>
            <tr>
              {gutter && <th className="ix" scope="col" />}
              {cols.map((c, i) => (
                <th
                  key={c.path}
                  scope="col"
                  className={
                    c.align === "end" ||
                    c.format === "currency" ||
                    c.format === "number"
                      ? "n"
                      : ""
                  }
                  data-sort={sort === c.path ? "1" : undefined}
                  onClick={() =>
                    onSort && onSort(sort === c.path ? "" : c.path)
                  }
                  style={onSort ? { cursor: "pointer" } : undefined}
                >
                  {c.label}
                  {onSort && <span className="so">↑</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, ri) => {
              const isChg = chgRows.has(ri);
              return (
                <tr
                  key={ri}
                  data-chg={isChg ? "1" : undefined}
                  data-off={isChg && off[ri] ? "1" : undefined}
                >
                  {gutter && <td className="ix">{ri + 1}</td>}
                  {cols.map((c, ci) => {
                    const chg = chgFor[ri + ":" + c.path];
                    const ek = ri + ":" + c.path;
                    const cls = cellClass(c, ci);
                    if (editable) {
                      const val = edits[ek] ?? cellText(r[c.path], c);
                      return (
                        <td
                          key={c.path}
                          className={cls}
                          data-ed={edits[ek] != null ? "1" : undefined}
                        >
                          {maxes[c.path] && edits[ek] == null
                            ? bar(r, c)
                            : null}
                          <input
                            className="sfe sfvv"
                            value={val}
                            onChange={(e) => onEdit(ek, e.target.value)}
                          />
                        </td>
                      );
                    }
                    if (chg && !off[ri]) {
                      return (
                        <td key={c.path} className={cls}>
                          <span className="sfo">{chg.old}</span>
                          <span className="sfn">{chg.next}</span>
                        </td>
                      );
                    }
                    if (chg && off[ri]) {
                      return (
                        <td key={c.path} className={cls}>
                          {chg.old}
                        </td>
                      );
                    }
                    if (c.format === "status")
                      return (
                        <td key={c.path}>
                          <SfStatus value={r[c.path]} />
                        </td>
                      );
                    if (maxes[c.path])
                      return (
                        <td key={c.path} className={cls}>
                          {bar(r, c)}
                          <span className="sfvv">{cellText(r[c.path], c)}</span>
                        </td>
                      );
                    return (
                      <td key={c.path} className={cls}>
                        {cellText(r[c.path], c)}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {cap && <div className="sft-cap">{cap}</div>}
    </>
  );
}

function SfFieldRows({ fields, changes = [], off = {}, onToggle }) {
  const chg = {};
  changes.forEach((c, i) => {
    chg[c.key] = { ...c, i };
  });
  return (
    <div>
      {fields.map((f) => {
        const c = chg[f.key];
        const hidden = c && off[c.i];
        return (
          <div
            className="sfr"
            key={f.key}
            data-chg={c ? "1" : undefined}
            data-off={hidden ? "1" : undefined}
          >
            <span className="l">{f.label}</span>
            <span className={"v" + (f.numeric ? " n" : "")}>
              {c && !hidden ? (
                <>
                  <span className="sfo">{c.old}</span>
                  <span className="sfn">{c.next}</span>
                </>
              ) : c ? (
                c.old
              ) : (
                f.value
              )}
              {c && <span className="hk" style={{ display: "none" }} />}
            </span>
            {c && onToggle && (
              <span
                style={{
                  gridColumn: "1 / -1",
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  paddingTop: 6,
                }}
              >
                <button
                  className="hk"
                  data-on={!hidden ? "1" : undefined}
                  onClick={() => onToggle(c.i)}
                  aria-label="Include this change"
                >
                  <Icon.check />
                </button>
                <span className="prov">
                  {hidden ? "excluded" : "included"} · from {c.src}
                </span>
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

function SfPreparing({ label = "Preparing view", note }) {
  return (
    <div className="sf-prep">
      <span className="pd" />
      {label}
      {note && (
        <span
          style={{
            textTransform: "none",
            letterSpacing: 0,
            color: "var(--mut2)",
            marginLeft: 4,
          }}
        >
          · {note}
        </span>
      )}
    </div>
  );
}

function SfSkeleton({ rows = 5 }) {
  return (
    <div className="sk-rows">
      {Array.from({ length: rows }).map((_, i) => (
        <div className="r" key={i}>
          <span className="sk" style={{ width: 12, marginLeft: "auto" }} />
          <span
            className="sk"
            style={{
              width: (i % 3 === 0 ? 74 : i % 3 === 1 ? 108 : 92) + "%",
              maxWidth: 220,
            }}
          />
          <span className="sk" style={{ width: "62%" }} />
          <span className="sk" style={{ width: "48%", marginLeft: "auto" }} />
          <span className="sk" style={{ width: "70%" }} />
        </div>
      ))}
    </div>
  );
}

function SfNote({ tone, icon, children }) {
  const I =
    icon ||
    (tone === "danger" ? Icon.warn : tone === "warn" ? Icon.shield : Icon.eye);
  return (
    <div className={"sf-note" + (tone ? " sf-note--" + tone : "")}>
      <I />
      {children}
    </div>
  );
}

function SfBar({ copy, hunks, children }) {
  return (
    <div className="sf-bar">
      {copy && <span className="c">{copy}</span>}
      {hunks}
      <span className="sp" />
      {children}
    </div>
  );
}

Object.assign(window, {
  SfCard,
  SfTable,
  SfToolbar,
  SfFieldRows,
  SfPreparing,
  SfSkeleton,
  SfNote,
  SfBar,
  SfStatus,
  nfmt,
  cfmt,
  cellText,
  cellClass,
});
