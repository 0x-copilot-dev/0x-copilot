import {
  Fragment,
  createElement,
  useEffect,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

import {
  AppIcon,
  Badge,
  Button,
  Card,
  ConnectorChip,
  Field,
  HarnessRow,
  IconButton,
  Select,
  StatusLine,
  StatusPill,
  Switch,
  TextInput,
  Toggle,
  classNames,
} from "@0x-copilot/design-system";

// PRD §9.5.2 — the worker is the preemptive boundary. React renders are
// synchronous, so a host setTimeout cannot interrupt a `while(true){}`
// inside the adapter. By running the adapter inside a Web Worker the
// host can call `terminate()` and the misbehaving code is _dead_, not
// merely cancelled. The 100 ms wall-clock budget is enforced by the
// timer below; on fire we terminate.
const DEFAULT_BUDGET_MS = 100;

const DEFAULT_TAGS = new Set<string>([
  "div",
  "span",
  "p",
  "ul",
  "ol",
  "li",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "strong",
  "em",
  "code",
  "pre",
  "a",
  "img",
  "section",
  "article",
  "header",
  "footer",
  "nav",
  "aside",
  "table",
  "thead",
  "tbody",
  "tr",
  "td",
  "th",
  "figure",
  "figcaption",
  "blockquote",
  "hr",
  "br",
]);

type AnyComponent = (props: Record<string, unknown>) => ReactElement | null;
const DS_COMPONENTS: Record<string, AnyComponent> = {
  Button: Button as unknown as AnyComponent,
  Badge: Badge as unknown as AnyComponent,
  Card: Card as unknown as AnyComponent,
  TextInput: TextInput as unknown as AnyComponent,
  Select: Select as unknown as AnyComponent,
  Switch: Switch as unknown as AnyComponent,
  Toggle: Toggle as unknown as AnyComponent,
  Field: Field as unknown as AnyComponent,
  IconButton: IconButton as unknown as AnyComponent,
  StatusPill: StatusPill as unknown as AnyComponent,
  AppIcon: AppIcon as unknown as AnyComponent,
  HarnessRow: HarnessRow as unknown as AnyComponent,
  StatusLine: StatusLine as unknown as AnyComponent,
  ConnectorChip: ConnectorChip as unknown as AnyComponent,
};

const ALLOWED_PROP_NAMES = new Set<string>([
  "className",
  "style",
  "title",
  "role",
  "id",
  "key",
  "tabIndex",
  "aria-label",
  "aria-labelledby",
  "aria-describedby",
  "aria-hidden",
  "data-testid",
  "data-tier",
  "data-scheme",
  "data-version",
  "data-status",
  "data-state",
  "src",
  "alt",
  "href",
  "target",
  "rel",
  "checked",
  "value",
  "label",
  "name",
  "type",
  "placeholder",
  "disabled",
  "open",
  "variant",
  "size",
  "tone",
  "hint",
  "color",
  "state",
  "tool",
  "args",
  "result",
  "status",
  "logoUrl",
]);

export interface Tier2WorkerLike {
  postMessage(value: unknown): void;
  terminate(): void;
  addEventListener(
    type: "message" | "error",
    listener: (event: { data: unknown }) => void,
  ): void;
  removeEventListener(
    type: "message" | "error",
    listener: (event: { data: unknown }) => void,
  ): void;
}

export interface Tier2WorkerRequest {
  readonly kind: "render";
  readonly adapterSource: string;
  readonly scheme: string;
  readonly version: number;
  readonly mode: "current" | "diff";
  readonly payload: unknown;
}

export interface Tier2JsonElement {
  readonly tag: string;
  readonly props?: Record<string, unknown>;
  readonly children?: ReadonlyArray<Tier2JsonElement | string | number | null>;
}

export type Tier2WorkerResponse =
  | { readonly kind: "rendered"; readonly tree: Tier2JsonElement }
  | {
      readonly kind: "failed";
      readonly reason: "throw" | "oom" | "shape" | "violation";
      readonly detail?: string;
    };

export interface Tier2LoaderProps {
  readonly adapterSource: string;
  readonly scheme: string;
  readonly version: number;
  readonly state?: unknown;
  readonly pendingDiff?: { readonly diff: unknown } | null;
  readonly workerFactory?: () => Tier2WorkerLike;
  readonly budgetMs?: number;
  readonly onFailure?: (
    reason: "throw" | "oom" | "shape" | "violation" | "timeout",
    detail?: string,
  ) => void;
}

type RenderState =
  | { readonly status: "pending" }
  | { readonly status: "rendered"; readonly tree: Tier2JsonElement }
  | {
      readonly status: "failed";
      readonly reason: "throw" | "oom" | "shape" | "violation" | "timeout";
      readonly detail?: string;
    };

function isJsonElement(value: unknown): value is Tier2JsonElement {
  if (value === null || typeof value !== "object") return false;
  const obj = value as Record<string, unknown>;
  return typeof obj.tag === "string";
}

function sanitizeProps(raw: unknown): Record<string, unknown> {
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) return {};
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (key.startsWith("on")) continue;
    if (key === "dangerouslySetInnerHTML") continue;
    if (key === "ref") continue;
    if (!ALLOWED_PROP_NAMES.has(key)) continue;
    if (typeof value === "function") continue;
    out[key] = value;
  }
  return out;
}

function reconcileChild(child: unknown, keyHint: number): ReactNode {
  if (child === null || child === undefined) return null;
  if (typeof child === "string") return child;
  if (typeof child === "number") return child;
  if (typeof child === "boolean") return null;
  if (isJsonElement(child)) {
    return reconcile(child, keyHint);
  }
  return null;
}

function reconcile(node: Tier2JsonElement, keyHint: number): ReactElement {
  const props = sanitizeProps(node.props);
  const childrenRaw = node.children ?? [];
  const children: ReactNode[] = childrenRaw.map((c, i) => reconcileChild(c, i));
  const tag = node.tag;
  if (tag.startsWith("ds:")) {
    const dsName = tag.slice(3);
    const Component = DS_COMPONENTS[dsName];
    if (!Component) {
      return createElement(
        "span",
        {
          ...props,
          key: keyHint,
          "data-tier2-unsafe-tag": tag,
        },
        ...children,
      );
    }
    return createElement(Component, { ...props, key: keyHint }, ...children);
  }
  if (!DEFAULT_TAGS.has(tag)) {
    return createElement(
      "span",
      {
        ...props,
        key: keyHint,
        "data-tier2-unsafe-tag": tag,
      },
      ...children,
    );
  }
  return createElement(tag, { ...props, key: keyHint }, ...children);
}

function defaultWorkerFactory(): Tier2WorkerLike {
  throw new Error(
    "Tier2Loader: no workerFactory provided. Phase 6C is expected to wire the production worker bundle; tests must inject a stub.",
  );
}

export function Tier2Loader(props: Tier2LoaderProps): ReactElement | null {
  const {
    adapterSource,
    scheme,
    version,
    state,
    pendingDiff,
    workerFactory = defaultWorkerFactory,
    budgetMs = DEFAULT_BUDGET_MS,
    onFailure,
  } = props;

  const [renderState, setRenderState] = useState<RenderState>({
    status: "pending",
  });
  const failureRef = useRef(onFailure);
  failureRef.current = onFailure;

  useEffect(() => {
    let settled = false;
    let worker: Tier2WorkerLike | null = null;
    try {
      worker = workerFactory();
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      setRenderState({ status: "failed", reason: "throw", detail });
      failureRef.current?.("throw", detail);
      return;
    }

    const settle = (next: RenderState): void => {
      if (settled) return;
      settled = true;
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
      if (worker) {
        worker.removeEventListener("message", onMessage);
        worker.removeEventListener("error", onError);
      }
      setRenderState(next);
      if (next.status === "failed") {
        failureRef.current?.(next.reason, next.detail);
      }
    };

    const onMessage = (event: { data: unknown }): void => {
      const data = event.data;
      if (data === null || typeof data !== "object") {
        settle({
          status: "failed",
          reason: "shape",
          detail: "worker response was not an object",
        });
        return;
      }
      const response = data as Tier2WorkerResponse;
      if (response.kind === "rendered") {
        if (!isJsonElement(response.tree)) {
          settle({
            status: "failed",
            reason: "shape",
            detail: "worker tree was not a Tier2JsonElement",
          });
          return;
        }
        settle({ status: "rendered", tree: response.tree });
        return;
      }
      if (response.kind === "failed") {
        settle({
          status: "failed",
          reason: response.reason,
          detail: response.detail,
        });
        return;
      }
      settle({
        status: "failed",
        reason: "shape",
        detail: "unknown worker response kind",
      });
    };

    const onError = (event: { data: unknown }): void => {
      const detail =
        event.data instanceof Error
          ? event.data.message
          : event.data === undefined
            ? "worker error"
            : String(event.data);
      settle({ status: "failed", reason: "throw", detail });
    };

    worker.addEventListener("message", onMessage);
    worker.addEventListener("error", onError);

    let timer: ReturnType<typeof setTimeout> | null = setTimeout(() => {
      timer = null;
      if (worker) {
        worker.terminate();
      }
      settle({
        status: "failed",
        reason: "timeout",
        detail: `worker exceeded ${budgetMs}ms budget`,
      });
    }, budgetMs);

    const request: Tier2WorkerRequest = {
      kind: "render",
      adapterSource,
      scheme,
      version,
      mode: pendingDiff ? "diff" : "current",
      payload: pendingDiff ? pendingDiff.diff : (state ?? null),
    };
    worker.postMessage(request);

    return () => {
      if (!settled) {
        settled = true;
        if (timer !== null) {
          clearTimeout(timer);
          timer = null;
        }
        if (worker) {
          worker.removeEventListener("message", onMessage);
          worker.removeEventListener("error", onError);
          worker.terminate();
        }
      }
    };
  }, [
    adapterSource,
    scheme,
    version,
    state,
    pendingDiff,
    budgetMs,
    workerFactory,
  ]);

  if (renderState.status === "pending") {
    return createElement("div", {
      "data-testid": "tier2-loader-pending",
      "data-scheme": scheme,
      "data-version": String(version),
    });
  }
  if (renderState.status === "failed") {
    return null;
  }
  return createElement(Fragment, null, reconcile(renderState.tree, 0));
}

export const __TIER2_DEFAULT_TAGS_FOR_TESTS = DEFAULT_TAGS;
export const __TIER2_DS_COMPONENTS_FOR_TESTS = DS_COMPONENTS;
