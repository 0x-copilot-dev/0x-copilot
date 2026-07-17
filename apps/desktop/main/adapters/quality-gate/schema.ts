import { z, type ZodIssue } from "zod";

import type {
  SaaSRendererAdapter,
  SaaSRendererAdapterOrigin,
} from "@0x-copilot/chat-surface";

// Q1 (PRD §9.5.1). Run on every load of a tier-2 module from disk. A failure
// here means the generated module does not implement SaaSRendererAdapter and
// must not be installed. The schema does NOT call the functions — it only
// proves they exist with the right arities; behavioral verification is Q3
// (smoke render) and Q4 (live error boundary).

const ORIGINS: readonly SaaSRendererAdapterOrigin[] = [
  "first-party",
  "agent-generated",
  "community",
] as const;

const callable = (arity: number) =>
  z.custom<(...args: unknown[]) => unknown>(
    (v) => typeof v === "function" && (v as Function).length >= arity,
    { message: `expected function with arity >= ${arity}` },
  );

const metadataSchema = z
  .object({
    origin: z.enum(
      ORIGINS as unknown as [
        SaaSRendererAdapterOrigin,
        ...SaaSRendererAdapterOrigin[],
      ],
    ),
    schemaVersion: z.number().int().nonnegative(),
    generatedAt: z.string().optional(),
    generatorModel: z.string().optional(),
  })
  .strict();

const adapterSchema = z
  .object({
    scheme: z.string().min(1),
    matches: callable(1),
    renderCurrent: callable(1),
    renderDiff: callable(1),
    metadata: metadataSchema,
  })
  .passthrough();

export interface SchemaOk {
  readonly ok: true;
  readonly value: SaaSRendererAdapter;
}

export interface SchemaFail {
  readonly ok: false;
  readonly errors: readonly ZodIssue[];
}

export function validateAdapterSchema(input: unknown): SchemaOk | SchemaFail {
  const parsed = adapterSchema.safeParse(input);
  if (parsed.success) {
    return { ok: true, value: parsed.data as unknown as SaaSRendererAdapter };
  }
  return { ok: false, errors: parsed.error.issues };
}
