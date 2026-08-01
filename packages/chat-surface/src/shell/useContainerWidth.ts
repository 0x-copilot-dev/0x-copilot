// useContainerWidth — observe an element's width without touching `window`.
//
// Source: docs/plan/windowed-mode/PRD-00-overview.md §4 (FR-0.2, FR-0.8).
//
// Invariants (inherited from `destinations/inbox/useInboxLayout.tsx`, the
// shipped precedent this generalises):
//   - No JS `window` resize listeners. `ResizeObserver` only, on the element the
//     caller points at, so a surface embedded in a shrunk column gets the right
//     answer for free.
//   - `ResizeObserver` is NOT in this package's banned-globals list; `window`
//     and friends are. That is why this is legal here.
//   - Works in jsdom: tests shim `ResizeObserver` and drive the width directly.
//     No layout or paint required.
//   - Degrades, never throws: an environment without `ResizeObserver` keeps the
//     caller's default forever rather than crashing the shell.

import { useEffect, useState, type RefObject } from "react";

import { widthClassFor, type ShellWidthClass } from "./layout";

/**
 * Last observed border-box width of `ref`, in CSS pixels.
 *
 * Returns `defaultWidthPx` until the first observer callback. Callers that only
 * care about the derived class should use {@link useObservedWidthClass}, which
 * re-renders on CLASS changes rather than on every pixel (NFR-0.1).
 */
export function useContainerWidth(
  ref: RefObject<HTMLElement | null>,
  defaultWidthPx: number,
): number {
  const [width, setWidth] = useState<number>(defaultWidthPx);

  useEffect(() => {
    const el = ref.current;
    if (el === null) {
      return;
    }
    if (typeof ResizeObserver === "undefined") {
      // Defensive: extremely old environments lack ResizeObserver. Keep the
      // default rather than throwing — a shell that renders wide is a far
      // better failure than a shell that does not render (FR-0.8).
      return;
    }
    const ro = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry === undefined) {
        return;
      }
      const next = readWidth(entry);
      // A non-positive measurement means "not laid out" — a detached node, a
      // `display: none` ancestor, or jsdom, which fires the observer but does no
      // layout so every element reports 0. Treating that as a real width tells
      // callers the container is infinitely narrow, which collapses every
      // width-gated panel in the test environment. Keep the default instead;
      // `widthClassFor` guards the same way.
      if (!Number.isFinite(next) || next <= 0) {
        return;
      }
      setWidth(next);
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
    };
  }, [ref]);

  return width;
}

/**
 * The width CLASS of `ref`'s container.
 *
 * Deliberately stores the class, not the pixel width: a drag from 1400 → 1399
 * fires the observer but must not re-render the shell. Only a class transition
 * does (NFR-0.1).
 */
export function useObservedWidthClass(
  ref: RefObject<HTMLElement | null>,
  defaultClass: ShellWidthClass,
): ShellWidthClass {
  const [cls, setCls] = useState<ShellWidthClass>(defaultClass);

  useEffect(() => {
    const el = ref.current;
    if (el === null || typeof ResizeObserver === "undefined") {
      return;
    }
    const ro = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry === undefined) {
        return;
      }
      const next = widthClassFor(readWidth(entry));
      // Guard inside the setter so an unchanged class is a no-op React bails
      // out of, rather than a re-render per observed pixel.
      setCls((prev) => (prev === next ? prev : next));
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
    };
  }, [ref]);

  return cls;
}

/**
 * `ResizeObserverEntry.borderBoxSize` is a `ReadonlyArray<ResizeObserverSize>`
 * in the spec but shipped as a bare object in older engines; some engines omit
 * it entirely. Fall back to `contentRect`, which is universal.
 */
function readWidth(entry: ResizeObserverEntry): number {
  const boxes = entry.borderBoxSize as
    | readonly ResizeObserverSize[]
    | ResizeObserverSize
    | undefined;
  if (boxes !== undefined) {
    const first = Array.isArray(boxes)
      ? boxes[0]
      : (boxes as ResizeObserverSize);
    if (first !== undefined && typeof first.inlineSize === "number") {
      return first.inlineSize;
    }
  }
  return entry.contentRect.width;
}
