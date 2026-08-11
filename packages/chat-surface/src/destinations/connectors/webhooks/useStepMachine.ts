// `useStepMachine` — a single, narrow primitive backing the webhook
// create wizard's linear back/forward steps.
//
// It used to live in `destinations/tools/onboarding/`, where it backed the
// four Tools onboarding wizards. Those wizards were unreachable in both
// hosts and were deleted; this hook moved here, to its one remaining
// consumer, rather than to a shared layer — a shared home for a single
// caller is the abstraction this file's original note warned against.
//
// Why a local hook (no library):
// - `useReducer` is enough. Pulling in `xstate` or similar for one short
//   flow would outweigh the wizard it serves.
// - Linear steps with back/forward is exactly what a numeric step counter
//   handles.
// - Lift it to `shell/` or design-system only when a second destination
//   genuinely needs it.
//
// Substitution rule: the hook is pure — it owns `currentStep` and a
// `canAdvance` flag (parent gates by step). The hook does NOT own the
// per-step form data — that lives in the calling wizard's own state.

import { useCallback, useMemo, useReducer } from "react";

interface StepMachineState {
  readonly currentStep: number;
  readonly totalSteps: number;
}

type StepMachineAction =
  | { readonly type: "next" }
  | { readonly type: "back" }
  | { readonly type: "goto"; readonly step: number }
  | { readonly type: "reset" };

function reducer(
  state: StepMachineState,
  action: StepMachineAction,
): StepMachineState {
  switch (action.type) {
    case "next":
      if (state.currentStep >= state.totalSteps - 1) return state;
      return { ...state, currentStep: state.currentStep + 1 };
    case "back":
      if (state.currentStep <= 0) return state;
      return { ...state, currentStep: state.currentStep - 1 };
    case "goto":
      if (action.step < 0 || action.step >= state.totalSteps) return state;
      return { ...state, currentStep: action.step };
    case "reset":
      return { ...state, currentStep: 0 };
    default:
      return state;
  }
}

export interface UseStepMachineOptions {
  readonly totalSteps: number;
  readonly initialStep?: number;
}

export interface StepMachine {
  readonly currentStep: number;
  readonly totalSteps: number;
  readonly isFirst: boolean;
  readonly isLast: boolean;
  readonly next: () => void;
  readonly back: () => void;
  readonly goto: (step: number) => void;
  readonly reset: () => void;
}

export function useStepMachine(opts: UseStepMachineOptions): StepMachine {
  const { totalSteps, initialStep = 0 } = opts;

  const [state, dispatch] = useReducer(reducer, {
    currentStep: Math.min(
      Math.max(initialStep, 0),
      Math.max(0, totalSteps - 1),
    ),
    totalSteps,
  });

  const next = useCallback(() => dispatch({ type: "next" }), []);
  const back = useCallback(() => dispatch({ type: "back" }), []);
  const goto = useCallback(
    (step: number) => dispatch({ type: "goto", step }),
    [],
  );
  const reset = useCallback(() => dispatch({ type: "reset" }), []);

  return useMemo<StepMachine>(
    () => ({
      currentStep: state.currentStep,
      totalSteps: state.totalSteps,
      isFirst: state.currentStep === 0,
      isLast: state.currentStep === state.totalSteps - 1,
      next,
      back,
      goto,
      reset,
    }),
    [state, next, back, goto, reset],
  );
}
