# Surface language — the two archetypes that got a hue but no treatment

Follow-up to the identity-colour work merged in
[#425](https://github.com/0x-copilot-dev/0x-copilot/pull/425).

That change gave every generative surface an identity hue and rebuilt the tab strip,
the numeric register, and the value bars. Two of the four asks it was scoped against
came back only partly done, and this program closes them honestly:

| Ask                      | What shipped in #425             | What is still missing              |
| ------------------------ | -------------------------------- | ---------------------------------- |
| `board://`               | `plum` hue on the tab and kicker | the design's lane + card treatment |
| the no-spec generic view | hollow identity ring             | the design's honest-view treatment |

Both renderers already existed and were registered. Neither had ever been rendered
in the app or in a harness — the hue was assigned to a surface no one had looked at.
That is the gap, and it is why both PRDs end in a parity check rather than a green
unit suite.

- [PRD-01 — `board://` lanes](PRD-01-board-lanes.md)
- [PRD-02 — the no-spec view](PRD-02-no-spec-view.md)
- [STATUS](STATUS.md)

## Order

Independent. PRD-01 touches `BoardRenderer`; PRD-02 touches the shared fallback in
`_shared/primitives.tsx` and each archetype's spec-less path. They may run in
parallel, and deliberately do: the only shared file is one both merely read from.

## Why these are specified against exact CSS

The identity-colour work shipped three defects that unit tests passed straight
through, and every one of them was a case where the test asserted a proxy rather
than the rendered result — `toHaveClass` while the rule was inert, a hand-built
harness that omitted the inline style that actually won, a class present on a
surface nobody had rendered. So each PRD quotes the design's declarations verbatim
and closes on a computed-style parity report, not on "the class is there".
