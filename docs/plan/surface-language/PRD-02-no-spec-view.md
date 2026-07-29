# PRD-02 — the no-spec view

## The gap

When no `SurfaceSpec` matches a tool result, every archetype renderer falls back to
`GenericFieldList`. That path works, but it renders as a bare list of fields with a
"preparing" hint — which reads like something is broken or still loading.

The design is emphatic that it is neither. Rule 04 of its surface language:

> **Unknown degrades.** An unknown archetype falls to the generic view. Nothing in
> this pane ever renders as an error.

And the tier label is `"no spec"` with the note _"Nothing matched. The generic view
is a real view, not an error."_ Today we ship the fallback without the honesty that
makes it legible.

## Design source

`0xCopilot Surface Language` → `GenericSurface` (`surface-archetypes2.jsx`):

```jsx
<SfCard tier={3} kicker="Incident" tierLabel="no spec" title={…} chips={[{t:"acknowledged", tone:"warn"}]}>
  <SfNote>
    <span>No spec matched <code>pagerduty.incident.read</code>, so this is the payload
    as the tool sent it — top-level fields only, nested objects summarised. A spec
    will be generated and cached on the next call.</span>
  </SfNote>
  <SfFieldRows fields={GENERIC_PAYLOAD} />
  <SfBar copy={<>Read-only. <span style={{color:"var(--mut2)"}}>Generic views never
    carry a write action.</span></>}>
    <button className="cbtn cbtn--sm"><Icon.external /> Open in PagerDuty</button>
  </SfBar>
</SfCard>
```

```css
.sf-note {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  padding: 9px 12px;
  font-size: 12px;
  line-height: 1.55;
  color: var(--tx2);
  border-bottom: 1px solid var(--line);
  background: var(--ink2);
}
.sf-note svg {
  width: 13px;
  height: 13px;
  flex: none;
  margin-top: 2px;
  color: var(--mut);
}
.sf-note code {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--tx);
}

.sfr {
  display: grid;
  grid-template-columns: 172px minmax(0, 1fr);
  gap: 14px;
  align-items: baseline;
  padding: 9px 12px;
  border-bottom: 1px solid var(--line);
}
.sfr > .l {
  font-family: var(--mono);
  font-size: 9.5px;
  letter-spacing: 0.11em;
  text-transform: uppercase;
  color: var(--mut2);
}
.sfr > .v {
  font-size: 13px;
  color: var(--tx);
  min-width: 0;
  overflow-wrap: anywhere;
}
.sfr > .v.n {
  font-family: var(--mono);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
```

The tier-3 kicker dot is already correct: `data-surface-hue="none"` gives the hollow
ring, and the design agrees (`.sfc[data-tier="3"] .sfc-k .sd { background:transparent;
box-shadow:inset 0 0 0 1.5px var(--mut2) }`).

Tokens: `--ink2` → `--color-bg-elevated`, `--tx2` → `--color-text-strong`, `--mut` →
`--color-text-muted`, `--mut2` → `--color-text-subtle`, `--tx` → `--color-text`.

## Requirements

1. A spec-less surface states **why** it looks the way it does, naming the tool it
   could not match, and says a spec will be generated and cached. It must read as a
   deliberate view, never as an error or a loading state.
2. The note also states the two honest limits of the generic render: top-level
   fields only, nested objects summarised.
3. Field rows follow the design's two-column grid, with mono/caps labels and a
   numeric register (`tabular-nums`) for numeric-looking values.
4. The surface carries a read-only footer making explicit that a generic view never
   offers a write action. This is a **safety statement**, not decoration: the whole
   point of the tier is that nothing was understood well enough to act on.
5. The tool name is rendered as inert text in a code register. It arrives from tool
   output and is untrusted — it must never become a link, and it is length-capped
   like every other displayed value.
6. Replaces the current `PreparingHint` on the spec-less path. "Preparing" is a lie
   when nothing is being prepared; keep it only where a spec genuinely is in flight.

## The one thing that must not regress

The generic view is the **degradation target for every archetype**. `TableRenderer`,
`RecordRenderer`, `BoardRenderer`, `DocRenderer`, and `MessageRenderer` all route
here when `specFromState` returns `undefined`. A change that throws, or that assumes
a shape only one of them produces, takes down every surface at once. It must stay
total over `unknown`.

## Non-goals

- Actually generating or caching the spec. The note describes a tier-2 behaviour
  that already exists elsewhere; this PRD only tells the truth about it.
- A per-connector "Open in …" action. The design shows one for PagerDuty; ours has
  no safe generic destination, and `spec.link.url_path` is the sanctioned path when
  a spec exists. Do not synthesize a URL.

## Definition of done

- [ ] A spec-less surface renders the honest note naming the unmatched tool
- [ ] Field rows match the design's grid, label register, and numeric register
- [ ] The read-only footer is present and states that generic views never write
- [ ] No `PreparingHint` on the spec-less path; it survives where a spec is in flight
- [ ] The path is total: every archetype's fallback renders for `null`, `[]`, a
      primitive, a deeply nested object, and a hostile 10k-char string
- [ ] `surface-renderers` suite green
