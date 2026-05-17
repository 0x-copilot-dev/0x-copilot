// <ItemLink ref={ref} /> — the ONLY way a destination renders a
// cross-destination link.
//
// Source: cross-audit.md §1.1 + §3.3 (binding 2026-05-17). Direct
// `router.navigate(…)` calls from destination cards are forbidden;
// they bypass the registry's resolve-and-display logic and lose the
// deleted-ref signal.
//
// Behavior:
// 1. On mount, look up the resolver for `ref.kind`, invoke it with the
//    branded id.
// 2. While the resolver promise is in flight, render a non-disruptive
//    skeleton chip.
// 3. On success with `route != null`, render an `<a>` whose click
//    handler calls `router.navigate(route)`.
// 4. On success with `route === null`, render a `<span>` "deleted
//    ${kind}" chip (cross-audit §5.3 cascade-on-delete default).
// 5. On resolver failure or null, render the same deleted chip with
//    the kind. We don't surface the error string here — destinations
//    that want detailed error UX should call `resolveItemRef` directly.

import {
  useEffect,
  useState,
  type CSSProperties,
  type MouseEvent as ReactMouseEvent,
  type ReactElement,
} from "react";

import type { ItemKind, ItemRef } from "@enterprise-search/api-types";

import { useRouter } from "../providers/RouterProvider";
import type { ArtifactRoute } from "../routing/router";

import { resolveItemRef, type ItemRefResolved } from "./registry";

export interface ItemLinkProps {
  readonly ref: ItemRef;
  /** Optional className for host-level styling overrides. */
  readonly className?: string;
  /**
   * Optional override for the deleted-state label. When omitted the
   * chip reads `deleted ${kind}`.
   */
  readonly deletedLabel?: string;
}

type ResolveState =
  | { readonly kind: "loading" }
  | { readonly kind: "ready"; readonly resolved: ItemRefResolved }
  | { readonly kind: "deleted" }
  | { readonly kind: "error" };

const skeletonStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  height: 18,
  minWidth: 80,
  padding: "0 8px",
  borderRadius: "var(--radius-sm, 6px)",
  backgroundColor: "var(--color-surface-muted, #222224)",
  color: "var(--color-text-subtle, #7e7e84)",
  fontSize: "var(--font-size-xs, 12px)",
  opacity: 0.6,
};

const linkStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  color: "var(--color-accent, #d97757)",
  textDecoration: "none",
  cursor: "pointer",
  fontSize: "var(--font-size-sm, 13px)",
};

const deletedStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  color: "var(--color-text-subtle, #7e7e84)",
  fontStyle: "italic",
  fontSize: "var(--font-size-sm, 13px)",
};

function humanKind(kind: ItemKind): string {
  return kind.replace(/_/g, " ");
}

export function ItemLink({
  ref,
  className,
  deletedLabel,
}: ItemLinkProps): ReactElement {
  const router = useRouter<ArtifactRoute>();
  const [state, setState] = useState<ResolveState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    resolveItemRef(ref)
      .then((resolved) => {
        if (cancelled) return;
        if (resolved === null) {
          setState({ kind: "deleted" });
          return;
        }
        if (resolved.route === null) {
          setState({ kind: "deleted" });
          return;
        }
        setState({ kind: "ready", resolved });
      })
      .catch(() => {
        if (cancelled) return;
        // Resolver promise rejected (unregistered kind, network).
        // Treat as deleted/unavailable — the host-level "something
        // is wrong" banner is the destination's job, not this chip's.
        setState({ kind: "error" });
      });
    return () => {
      cancelled = true;
    };
  }, [ref]);

  if (state.kind === "loading") {
    return (
      <span
        className={className}
        style={skeletonStyle}
        aria-busy="true"
        aria-live="polite"
        role="status"
        data-testid="item-link-skeleton"
        data-item-kind={ref.kind}
      >
        loading…
      </span>
    );
  }

  if (state.kind === "deleted" || state.kind === "error") {
    const label = deletedLabel ?? `deleted ${humanKind(ref.kind)}`;
    return (
      <span
        className={className}
        style={deletedStyle}
        data-testid="item-link-deleted"
        data-item-kind={ref.kind}
      >
        {label}
      </span>
    );
  }

  const { resolved } = state;
  const route = resolved.route as ArtifactRoute; // non-null narrowed above
  const handleClick = (event: ReactMouseEvent<HTMLAnchorElement>): void => {
    // Plain clicks navigate via the router (substrate-agnostic). Modified
    // clicks (cmd/ctrl/shift/middle) fall through to the browser's default
    // so users can open in new tab/window.
    if (
      event.defaultPrevented ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey ||
      event.button !== 0
    ) {
      return;
    }
    event.preventDefault();
    router.navigate(route);
  };

  return (
    <a
      href="#"
      className={className}
      style={linkStyle}
      onClick={handleClick}
      title={resolved.breadcrumb}
      data-testid="item-link"
      data-item-kind={ref.kind}
      data-item-id={ref.id}
    >
      {resolved.icon}
      <span>{resolved.label}</span>
    </a>
  );
}
