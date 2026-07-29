/* global React, Icon, SfCard, SfTable, SfToolbar, SfFieldRows, SfPreparing, SfSkeleton, SfNote, SfBar, PAYOUT_ROWS, PAYOUT_SPEC, PAYOUT_CHANGES, FORECAST_COLS, FORECAST_ROWS, OPP_FIELDS, OPP_CHANGES, MAIL_BODY, DOC_OUTLINE, BOARD_COLS, GENERIC_PAYLOAD, TIER_LABEL, cfmt */
const { useState: useA, useMemo: useAM } = React;

function Hunks({ total, off, label = "changes" }) {
  const on = total - Object.values(off).filter(Boolean).length;
  return (
    <span className="hkc" style={{ marginLeft: 4 }}>
      <Icon.sliders />
      {on} of {total} {label} included
    </span>
  );
}
function Approve({ label, onDone, done, tone = "pri" }) {
  if (done)
    return (
      <span className="sfb sfb--ok">
        <Icon.check /> {done}
      </span>
    );
  return (
    <>
      <button className="cbtn cbtn--sm">Reject</button>
      <button className={"cbtn cbtn--sm cbtn--" + tone} onClick={onDone}>
        {label}
      </button>
    </>
  );
}
function Drift({ what }) {
  return (
    <SfNote tone="danger">
      <span>
        <b style={{ color: "var(--tx)", fontWeight: 550 }}>
          Source moved while you were reviewing.
        </b>{" "}
        {what} The write was aborted before anything was sent — re-read the
        source to rebuild the diff.
      </span>
      <span className="sp" />
      <button className="cbtn cbtn--sm" style={{ flex: "none" }}>
        <Icon.refresh /> Re-read
      </button>
    </SfNote>
  );
}

/* ── table:// — the generative table, now on the shared language ─────────── */
function TableSurface({ s, st, gutter, preparing, merge }) {
  const [filter, setFilter] = useA("");
  const [sort, setSort] = useA("");
  const [off, setOff] = useA({});
  const [done, setDone] = useA(null);
  const rows = useAM(() => {
    let r = PAYOUT_ROWS.map((x, i) => ({ ...x, __i: i }));
    if (filter.trim()) {
      const q = filter.toLowerCase();
      r = r.filter((x) =>
        Object.values(x).some((v) => String(v).toLowerCase().includes(q)),
      );
    }
    if (sort)
      r = [...r].sort((a, b) =>
        a[sort] > b[sort] ? 1 : a[sort] < b[sort] ? -1 : 0,
      );
    return r;
  }, [filter, sort]);
  const changes =
    st === "proposed"
      ? PAYOUT_CHANGES.map((c) => ({
          ...c,
          row: rows.findIndex((r) => r.__i === c.row),
        })).filter((c) => c.row >= 0)
      : [];
  const total = 1284;
  return (
    <SfCard
      tier={s.tier}
      src={s.src}
      kicker="Table"
      tierLabel={TIER_LABEL[s.tier].t}
      title={s.title}
      sub={s.sub}
      merge={merge}
      chips={[{ t: rows.length + " of " + total + " rows" }]}
      link="Open in Safe"
    >
      {preparing ? (
        <>
          <SfPreparing note="spec in flight for safe.batch.read" />
          <SfSkeleton rows={6} />
        </>
      ) : (
        <>
          {st === "drift" && (
            <Drift what="tx_8f21a was re-staged at 3,600.00 USDC by another signer." />
          )}
          <SfToolbar
            filter={filter}
            setFilter={setFilter}
            sort={sort}
            setSort={setSort}
            cols={PAYOUT_SPEC.columns}
            right={
              st === "proposed" ? (
                <span className="sf-st">
                  <b>3</b> proposed changes
                </span>
              ) : (
                <span className="sf-st">read-only view</span>
              )
            }
          />
          <SfTable
            cols={PAYOUT_SPEC.columns}
            rows={rows}
            gutter={gutter}
            changes={changes}
            off={off}
            sort={sort}
            onSort={setSort}
            cap={
              <>
                <span>
                  Showing {rows.length} of {total} rows
                </span>
                <span className="sp" />
                <span>6 of 6 columns</span>
                <span>render cap 200</span>
              </>
            }
          />
          {st === "proposed" ? (
            <SfBar
              copy={
                <>
                  Approving writes <b>3 cells</b> to the Safe batch. Nothing is
                  signed yet.
                </>
              }
              hunks={
                <>
                  <Hunks total={3} off={off} />
                  <span style={{ display: "flex", gap: 5 }}>
                    {PAYOUT_CHANGES.map((c, i) => (
                      <button
                        key={i}
                        className="hk"
                        data-on={
                          !off[changes[i] && changes[i].row] ? "1" : undefined
                        }
                        onClick={() =>
                          setOff((o) => ({
                            ...o,
                            [changes[i].row]: !o[changes[i].row],
                          }))
                        }
                        aria-label={"Include change " + (i + 1)}
                      >
                        <Icon.check />
                      </button>
                    ))}
                  </span>
                </>
              }
            >
              <Approve
                label="Approve with edits"
                done={done}
                onDone={() => setDone("3 cells written · batch re-staged")}
              />
            </SfBar>
          ) : st === "current" ? (
            <SfBar
              copy={
                <>
                  Nothing pending on this surface.{" "}
                  <span style={{ color: "var(--mut2)" }}>
                    Last write 07-27 11:43 by the run.
                  </span>
                </>
              }
            >
              <button className="cbtn cbtn--sm">
                <Icon.download /> Export CSV
              </button>
            </SfBar>
          ) : null}
        </>
      )}
    </SfCard>
  );
}

/* ── the dataset artifact from the screenshot, same language ─────────────── */
function DatasetSurface({ s, st, gutter, preparing, merge }) {
  const [edits, setEdits] = useA({});
  const [filter, setFilter] = useA("");
  const [sort, setSort] = useA("");
  const [saved, setSaved] = useA(false);
  const dirty = Object.keys(edits).length;
  const rows = useAM(() => {
    let r = FORECAST_ROWS;
    if (filter.trim()) {
      const q = filter.toLowerCase();
      r = r.filter((x) =>
        Object.values(x).some((v) => String(v).toLowerCase().includes(q)),
      );
    }
    if (sort)
      r = [...r].sort((a, b) =>
        a[sort] > b[sort] ? 1 : a[sort] < b[sort] ? -1 : 0,
      );
    return r;
  }, [filter, sort]);
  return (
    <SfCard
      tier={s.tier}
      src={s.src}
      kicker="Dataset artifact"
      tierLabel={TIER_LABEL[0].t}
      title="forecast"
      sub="forecast.csv · text/csv · published by the run"
      merge={merge}
      chips={[{ t: "r" + (saved ? 2 : 1) + " · 6 × 4 · 1.1 KB" }]}
      link="Download"
    >
      {preparing ? (
        <>
          <SfPreparing label="Parsing artifact" note="6 rows · 4 columns" />
          <SfSkeleton rows={5} />
        </>
      ) : (
        <>
          {st === "drift" && (
            <Drift what="r1 was superseded by r2 while you were editing." />
          )}
          <SfToolbar
            filter={filter}
            setFilter={setFilter}
            sort={sort}
            setSort={setSort}
            cols={FORECAST_COLS}
            right={
              <span className="sf-st">
                {dirty ? (
                  <>
                    <b>{dirty}</b> unsaved cell{dirty > 1 ? "s" : ""}
                  </>
                ) : (
                  "cells editable"
                )}
              </span>
            }
          />
          <SfTable
            cols={FORECAST_COLS}
            rows={rows}
            gutter={gutter}
            editable
            edits={edits}
            sort={sort}
            onSort={setSort}
            onEdit={(k, v) => setEdits((e) => ({ ...e, [k]: v }))}
            cap={
              <>
                <span>6 of 6 rows</span>
                <span className="sp" />
                <span>ee896e0aa8fe</span>
              </>
            }
          />
          <SfBar
            copy={
              dirty ? (
                <>
                  Saving writes <b>r2</b>. r1 stays byte-identical and
                  downloadable.
                </>
              ) : (
                <>
                  Edit a cell to stage a new revision.{" "}
                  <span style={{ color: "var(--mut2)" }}>r1 is immutable.</span>
                </>
              )
            }
          >
            {dirty > 0 && (
              <button className="cbtn cbtn--sm" onClick={() => setEdits({})}>
                Discard
              </button>
            )}
            <button
              className="cbtn cbtn--sm cbtn--pri"
              disabled={!dirty}
              onClick={() => {
                setSaved(true);
                setEdits({});
              }}
            >
              Save patched revision
            </button>
          </SfBar>
          <div className="sf-rev">
            <div className="sf-rev-h">
              <span>Revision history</span>
              <span
                style={{
                  marginLeft: "auto",
                  textTransform: "none",
                  letterSpacing: 0,
                }}
              >
                {saved ? 2 : 1} loaded
              </span>
            </div>
            {saved && (
              <div className="sf-rev-r" data-on="1">
                <span className="r">r2</span>
                <span>you · just now · 1.1 KB</span>
              </div>
            )}
            <div className="sf-rev-r" data-on={saved ? undefined : "1"}>
              <span className="r">r1</span>
              <span>model · 2026-07-28 11:33 · 1.1 KB · ee896e0aa8fe</span>
            </div>
          </div>
        </>
      )}
    </SfCard>
  );
}

/* ── record:// ───────────────────────────────────────────────────────────── */
function RecordSurface({ s, st, preparing, merge }) {
  const [off, setOff] = useA({});
  const [done, setDone] = useA(null);
  const on = 3 - Object.values(off).filter(Boolean).length;
  return (
    <SfCard
      tier={s.tier}
      src={s.src}
      kicker="Record"
      tierLabel={TIER_LABEL[1].t}
      title={s.title}
      sub={s.sub}
      merge={merge}
      chips={
        st === "proposed"
          ? [{ t: "3 proposed", tone: "pend" }]
          : [{ t: "48,000 USD" }]
      }
      link="Open in Salesforce"
    >
      {preparing ? (
        <>
          <SfPreparing note="spec in flight for salesforce.opportunity.read" />
          <SfSkeleton rows={6} />
        </>
      ) : (
        <>
          {st === "drift" && (
            <Drift what="Stage is now Closed Won on the record." />
          )}
          <SfFieldRows
            fields={OPP_FIELDS}
            changes={st === "proposed" ? OPP_CHANGES : []}
            off={off}
            onToggle={
              st === "proposed"
                ? (i) => setOff((o) => ({ ...o, [i]: !o[i] }))
                : null
            }
          />
          {st === "proposed" && (
            <SfBar
              copy={
                <>
                  Writes{" "}
                  <b>
                    {on} field{on === 1 ? "" : "s"}
                  </b>{" "}
                  back to Salesforce. Preconditions are re-read at approve time.
                </>
              }
            >
              <Approve
                label={on === 3 ? "Approve" : "Approve with edits"}
                done={done}
                onDone={() => setDone(on + " fields written")}
              />
            </SfBar>
          )}
          {st === "current" && (
            <SfBar
              copy={
                <>
                  Read-only mirror of the record.{" "}
                  <span style={{ color: "var(--mut2)" }}>
                    Synced 2 min ago.
                  </span>
                </>
              }
            />
          )}
        </>
      )}
    </SfCard>
  );
}

/* ── message:// ──────────────────────────────────────────────────────────── */
function MessageSurface({ s, st, preparing, merge }) {
  const [edit, setEdit] = useA(false);
  const [done, setDone] = useA(null);
  return (
    <SfCard
      tier={s.tier}
      src={s.src}
      kicker="Message"
      tierLabel={TIER_LABEL[1].t}
      title={s.title}
      sub="draft · gmail"
      merge={merge}
      chips={[{ t: edit ? "editing" : "draft", tone: edit ? "pend" : "" }]}
      link="Open draft in Gmail"
    >
      {preparing ? (
        <>
          <SfPreparing note="spec in flight for gmail.draft.read" />
          <SfSkeleton rows={4} />
        </>
      ) : (
        <>
          {st === "drift" && (
            <Drift what="The thread got a new reply, so the quoted total no longer matches." />
          )}
          <div className="sfm-h">
            <span className="l">To</span>
            <span className="v">community@0xcopilot.tech</span>
            <span className="l">Cc</span>
            <span className="v">treasury@0xcopilot.tech</span>
            <span className="l">Subject</span>
            <span className="v" style={{ color: "var(--tx)", fontWeight: 550 }}>
              {s.title}
            </span>
          </div>
          <div
            className="sfm-b"
            contentEditable={edit}
            suppressContentEditableWarning
          >
            {MAIL_BODY.map((p, i) => (
              <p key={i}>
                {p.t}
                {p.diff &&
                  st === "proposed" &&
                  p.diff.map((d, j) => (
                    <React.Fragment key={j}>
                      <span className="sfo">{d.o}</span>
                      <span className="sfn">{d.n}</span>
                    </React.Fragment>
                  ))}
                {p.diff &&
                  st !== "proposed" &&
                  p.diff.map((d, j) => <span key={j}>{d.n}</span>)}
                {p.t2}
              </p>
            ))}
            <p className="sfm-sig">— sent by 0xCopilot on behalf of mira.eth</p>
          </div>
          <SfBar
            copy={
              edit ? (
                <>
                  Edited body routes through <b>approve_with_edits</b> — the
                  server re-reads the draft first.
                </>
              ) : st === "proposed" ? (
                <>One word-level change in paragraph 2.</>
              ) : (
                <>Nothing pending. Sending is the only write on this surface.</>
              )
            }
          >
            <button
              className="cbtn cbtn--sm"
              onClick={() => setEdit((e) => !e)}
            >
              {edit ? "Done editing" : "Edit body"}
            </button>
            <Approve
              label={edit ? "Send with edits" : "Send message"}
              done={done}
              onDone={() => setDone("sent · 09:41")}
            />
          </SfBar>
        </>
      )}
    </SfCard>
  );
}

/* ── doc:// ──────────────────────────────────────────────────────────────── */
function DocSurface({ s, st, preparing, merge }) {
  const [sect, setSect] = useA(1);
  const [done, setDone] = useA(null);
  return (
    <SfCard
      tier={s.tier}
      src={s.src}
      kicker="Doc"
      tierLabel={TIER_LABEL[2].t}
      title={s.title}
      sub={s.sub}
      merge={merge}
      chips={[{ t: "4 sections" }]}
      link="Open in Notion"
    >
      {preparing ? (
        <>
          <SfPreparing note="generating spec for notion.page.read" />
          <SfSkeleton rows={5} />
        </>
      ) : (
        <>
          {st === "drift" && (
            <Drift what="The page was edited in Notion 40 seconds ago." />
          )}
          <div className="sfd">
            <div className="sfd-o">
              {DOC_OUTLINE.map((o, i) => (
                <span
                  key={o}
                  className="oi"
                  data-on={sect === i ? "1" : undefined}
                  onClick={() => setSect(i)}
                >
                  {o}
                </span>
              ))}
            </div>
            <div className="sfd-b">
              <h4>Numbers</h4>
              <p>
                Launch Week moved 21,850 USDC to eight contributors across four
                workstreams, all of it staged by the run and signed by a human.
              </p>
              <p data-chg={st === "proposed" ? "1" : undefined}>
                {st === "proposed" ? (
                  <>
                    <span className="sfo">
                      Attendance held flat versus cycle 13.
                    </span>
                    <span className="sfn">
                      Attendance rose 18% versus cycle 13, with 412 live in the
                      AMA.
                    </span>
                  </>
                ) : (
                  "Attendance rose 18% versus cycle 13, with 412 live in the AMA."
                )}
              </p>
              <p>
                Two payouts were held for a memo fix and re-staged the same
                afternoon; the event log has both attempts under run 0x-284.
              </p>
              <h4>Open questions</h4>
              <p>
                Whether the recap should quote on-chain totals or the sheet
                totals when they disagree. Today the run quotes the chain.
              </p>
            </div>
          </div>
          {st === "proposed" && (
            <SfBar
              copy={
                <>
                  Publishing rewrites <b>one block</b>. The rest of the page is
                  untouched.
                </>
              }
            >
              <Approve
                label="Publish block"
                done={done}
                onDone={() => setDone("block published")}
              />
            </SfBar>
          )}
        </>
      )}
    </SfCard>
  );
}

/* ── board:// ────────────────────────────────────────────────────────────── */
function BoardSurface({ s, st, preparing, merge }) {
  const [done, setDone] = useA(null);
  return (
    <SfCard
      tier={s.tier}
      src={s.src}
      kicker="Board"
      tierLabel={TIER_LABEL[2].t}
      title={s.title}
      sub={s.sub}
      merge={merge}
      chips={[{ t: "7 issues" }]}
      link="Open cycle in Linear"
    >
      {preparing ? (
        <>
          <SfPreparing note="generating spec for linear.cycle.read" />
          <SfSkeleton rows={4} />
        </>
      ) : (
        <>
          {st === "drift" && (
            <Drift what="LW-142 was moved to Done by dev.tomo." />
          )}
          {st === "proposed" && (
            <SfNote>
              <span>
                <code>LW-142</code> moves{" "}
                <b style={{ color: "var(--tx)", fontWeight: 550 }}>
                  In progress → In review
                </b>{" "}
                because the transfers are staged and waiting on a signature.
              </span>
            </SfNote>
          )}
          <div className="sfbd">
            {BOARD_COLS.map((c) => (
              <div className="sfbd-c" key={c.name}>
                <div className="sfbd-h">
                  <span>{c.name}</span>
                  <span className="n">{c.cards.length}</span>
                </div>
                {c.cards.map((k) => (
                  <div
                    className="sfk"
                    key={k.t}
                    data-chg={st === "proposed" && k.chg ? "1" : undefined}
                  >
                    <span className="t">{k.t}</span>
                    <span className="f">
                      {k.m}
                      {st === "proposed" && k.chg && (
                        <span className="sfn" style={{ marginLeft: "auto" }}>
                          → In review
                        </span>
                      )}
                    </span>
                  </div>
                ))}
              </div>
            ))}
          </div>
          {st === "proposed" && (
            <SfBar copy={<>One state transition. No fields are rewritten.</>}>
              <Approve
                label="Move issue"
                done={done}
                onDone={() => setDone("LW-142 moved")}
              />
            </SfBar>
          )}
        </>
      )}
    </SfCard>
  );
}

/* ── tier 3 — no spec at all. A real view, not an error. ─────────────────── */
function GenericSurface({ s, preparing, merge }) {
  return (
    <SfCard
      tier={s.tier}
      src={s.src}
      kicker="Incident"
      tierLabel={TIER_LABEL[3].t}
      title={s.title}
      sub="pagerduty · incident 4127"
      merge={merge}
      chips={[{ t: "acknowledged", tone: "warn" }]}
    >
      {preparing ? (
        <>
          <SfPreparing note="no cached spec for pagerduty.incident.read" />
          <SfSkeleton rows={6} />
        </>
      ) : (
        <>
          <SfNote>
            <span>
              No spec matched <code>pagerduty.incident.read</code>, so this is
              the payload as the tool sent it — top-level fields only, nested
              objects summarised. A spec will be generated and cached on the
              next call.
            </span>
          </SfNote>
          <SfFieldRows fields={GENERIC_PAYLOAD} />
          <SfBar
            copy={
              <>
                Read-only.{" "}
                <span style={{ color: "var(--mut2)" }}>
                  Generic views never carry a write action.
                </span>
              </>
            }
          >
            <button className="cbtn cbtn--sm">
              <Icon.external /> Open in PagerDuty
            </button>
          </SfBar>
        </>
      )}
    </SfCard>
  );
}

const SURFACE_RENDERERS = {
  payouts: TableSurface,
  forecast: DatasetSurface,
  opp: RecordSurface,
  mail: MessageSurface,
  doc: DocSurface,
  board: BoardSurface,
  generic: GenericSurface,
};

Object.assign(window, {
  SURFACE_RENDERERS,
  TableSurface,
  DatasetSurface,
  RecordSurface,
  MessageSurface,
  DocSurface,
  BoardSurface,
  GenericSurface,
});
