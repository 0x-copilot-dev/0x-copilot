/* design-parity · computed-style extractor (browser context)
 * =========================================================================
 * Runs INSIDE a rendered page (via the in-app Browser `javascript_tool`, or
 * Playwright `page.evaluate`, or pasted into DevTools). It reads
 * getComputedStyle for a curated set of *visual* properties, for each element
 * named in an explicit anchor spec, and returns a JSON profile.
 *
 * Why an explicit anchor spec (not a whole-tree walk): the design mock and the
 * live app use DIFFERENT class names for the same elements, so we align by a
 * hand-authored {label, selector} map (surfaces/<name>/anchors.json). One side
 * is rendered, `__extractParity(spec)` is run against it, and the resulting
 * `{label -> styles}` profile is compared label-for-label with the other side.
 *
 * Usage (browser):
 *   __extractParity({ props: [...], elements: [{label, selector}, ...] })
 * Returns: { label: { matched, tag, classes, text, styles: {prop: value} } }
 * =========================================================================
 */
(function () {
  // The curated visual-property set. Layout-dependent sizes (width/height) are
  // captured but flagged noisy in the comparator (they vary with viewport).
  const DEFAULT_PROPS = [
    // typography
    "fontFamily",
    "fontSize",
    "fontWeight",
    "fontStyle",
    "lineHeight",
    "letterSpacing",
    "textTransform",
    "textAlign",
    // color
    "color",
    "backgroundColor",
    // border (collapsed 4-side helpers below add: borderWidth/Style/Color/Radius)
    // box
    "display",
    "flexDirection",
    "justifyContent",
    "alignItems",
    "flexGrow",
    "flexWrap",
    "opacity",
    // layout size (noisy)
    "width",
    "height",
  ];

  function collapse4(cs, a, b, c, d) {
    const v = [cs[a], cs[b], cs[c], cs[d]];
    if (v[0] === v[1] && v[1] === v[2] && v[2] === v[3]) return v[0];
    if (v[0] === v[2] && v[1] === v[3]) return v[0] + " " + v[1];
    return v.join(" ");
  }

  function normText(el) {
    // Direct text of the element (not descendants' block text), collapsed.
    let t = "";
    for (const n of el.childNodes) {
      if (n.nodeType === 3) t += n.nodeValue;
    }
    t = (t || el.textContent || "").replace(/\s+/g, " ").trim();
    return t.length > 60 ? t.slice(0, 60) + "…" : t;
  }

  globalThis.__extractParity = function (spec) {
    const props = (spec && spec.props) || DEFAULT_PROPS;
    const out = {};
    for (const el of spec.elements) {
      const node = document.querySelector(el.selector);
      if (!node) {
        out[el.label] = { matched: false, selector: el.selector };
        continue;
      }
      const cs = getComputedStyle(node);
      const styles = {};
      for (const p of props) styles[p] = cs[p];
      // collapsed multi-side properties
      styles.padding = collapse4(
        cs,
        "paddingTop",
        "paddingRight",
        "paddingBottom",
        "paddingLeft",
      );
      styles.margin = collapse4(
        cs,
        "marginTop",
        "marginRight",
        "marginBottom",
        "marginLeft",
      );
      styles.borderWidth = collapse4(
        cs,
        "borderTopWidth",
        "borderRightWidth",
        "borderBottomWidth",
        "borderLeftWidth",
      );
      styles.borderStyle = collapse4(
        cs,
        "borderTopStyle",
        "borderRightStyle",
        "borderBottomStyle",
        "borderLeftStyle",
      );
      styles.borderColor = collapse4(
        cs,
        "borderTopColor",
        "borderRightColor",
        "borderBottomColor",
        "borderLeftColor",
      );
      styles.borderRadius = collapse4(
        cs,
        "borderTopLeftRadius",
        "borderTopRightRadius",
        "borderBottomRightRadius",
        "borderBottomLeftRadius",
      );
      styles.gap =
        cs.rowGap === cs.columnGap
          ? cs.gap || cs.rowGap
          : cs.rowGap + " / " + cs.columnGap;
      out[el.label] = {
        matched: true,
        tag: node.tagName.toLowerCase(),
        classes: node.getAttribute("class") || "",
        text: normText(node),
        styles,
      };
    }
    return out;
  };
})();
