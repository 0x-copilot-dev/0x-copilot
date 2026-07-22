/* Design-kit stubs for the design-parity harness.
 *
 * The real design render (Claude Design 73f810d9) pulls Icon / Mark / useTweaks
 * / TweaksPanel from copilot-data.jsx + tweaks-panel.jsx. For a CSS-parity
 * render we only need the DOM STRUCTURE + classes to be faithful — the exact
 * icon geometry is irrelevant (icons are sized by CSS). So we stub the kit
 * globals with minimal, correctly-sized placeholders and drive the FTUE stage
 * from a `?state=` query param. Assigned onto globalThis so the separately
 * compiled copilot-firstrun.jsx script resolves them as free globals.
 */
/* global React */
(function () {
  const h = React.createElement;

  // Any Icon.<name> → a generic 1em stroked glyph (CSS sizes it to 11–16px).
  globalThis.Icon = new Proxy(
    {},
    {
      get: () =>
        function StubIcon() {
          return h(
            "svg",
            {
              width: "1em",
              height: "1em",
              viewBox: "0 0 24 24",
              fill: "none",
              stroke: "currentColor",
              strokeWidth: 1.7,
              strokeLinecap: "round",
              strokeLinejoin: "round",
            },
            h("circle", { cx: 12, cy: 12, r: 8 }),
          );
        },
    },
  );

  // Brand mark — a sized square glyph (real one is the sky hex-flower).
  globalThis.Mark = function Mark({ size = 16 }) {
    return h(
      "svg",
      { width: size, height: size, viewBox: "0 0 24 24" },
      h("circle", { cx: 12, cy: 12, r: 10, fill: "#5fb2ec" }),
    );
  };

  // useTweaks — real hook returns [state, setTweak]. The initial stage is
  // overridable via ?state=choice|dl|local|key so the harness can render any
  // FTUE state without the (stubbed-out) TweaksPanel UI.
  const params = new URLSearchParams(globalThis.location.search);
  globalThis.useTweaks = function useTweaks(initial) {
    const [s, set] = React.useState({
      ...initial,
      stage: params.get("state") || initial.stage,
    });
    const setTweak = (k, v) => set((o) => ({ ...o, [k]: v }));
    return [s, setTweak];
  };

  // The Tweaks dev-overlay is not part of the FTUE surface — render nothing so
  // it never pollutes the `.fr` subtree the extractor walks.
  globalThis.TweaksPanel = () => null;
  globalThis.TweakSection = () => null;
  globalThis.TweakRadio = () => null;
  globalThis.TweakToggle = () => null;
})();
