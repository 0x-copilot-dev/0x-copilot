// Electron-main-only bridge for staged browser effects.
//
// This is deliberately distinct from BrowserBroker. The loopback broker is a
// read tool façade; this bridge is created by Electron main and passed only to
// the supervised browser worker. It has no HTTP listener, no bearer/token
// accessor, and no renderer/preload export. Consequently the AI service can
// hold opaque scoped refs but cannot obtain cookies or invoke generic clicks.

import {
  BrowserActionPlanSchema,
  BrowserEffectReceiptSchema,
  BrowserPrepareResultSchema,
  type BrowserActionPlan,
  type BrowserEffectReceipt,
  type BrowserPrepareResult,
} from "./protocol";

export interface BrowserPrivateEffectWorkerPort {
  prepareAction(plan: BrowserActionPlan): Promise<BrowserPrepareResult>;
  applyPrepared(preparedRef: string): Promise<BrowserEffectReceipt>;
  reconcileAction(preparedRef: string): Promise<BrowserEffectReceipt>;
}

/**
 * Main-owned authority façade. Validation happens on both sides of the private
 * bridge to prevent a malformed worker payload from accidentally becoming a
 * capability escalation. This class deliberately exposes no read-broker token.
 */
export class BrowserPrivateEffectBridge {
  readonly #worker: BrowserPrivateEffectWorkerPort;

  constructor(worker: BrowserPrivateEffectWorkerPort) {
    this.#worker = worker;
  }

  async prepareAction(plan: BrowserActionPlan): Promise<BrowserPrepareResult> {
    const safePlan = BrowserActionPlanSchema.parse(plan);
    return BrowserPrepareResultSchema.parse(
      await this.#worker.prepareAction(safePlan),
    );
  }

  async applyPrepared(preparedRef: string): Promise<BrowserEffectReceipt> {
    // A prepared reference is opaque and was minted by the worker only after a
    // plan matched the live browser state. The worker still consumes it once.
    return BrowserEffectReceiptSchema.parse(
      await this.#worker.applyPrepared(preparedRef),
    );
  }

  async reconcileAction(preparedRef: string): Promise<BrowserEffectReceipt> {
    // Reconcile is intentionally observational; the worker port's contract has
    // no generic action argument and no retry/apply fallback.
    return BrowserEffectReceiptSchema.parse(
      await this.#worker.reconcileAction(preparedRef),
    );
  }
}
