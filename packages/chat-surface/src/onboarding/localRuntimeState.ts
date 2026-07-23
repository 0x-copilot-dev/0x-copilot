// Local-runtime state derivation (PRD-P8 §4.2 client mirror).
//
// The runtime state is derived SERVER-SIDE (`LocalModelsStatus.runtime_state`)
// — this module never re-derives it, it only decides what to believe:
//
//   • the server sent `runtime_state` → trust it verbatim.
//   • the server omitted it (older build; PRD-P8 D3 keeps every new field
//     optional) → fall back to `ollama_running`: `true` → "running",
//     `false` → "unknown".
//
// The fallback deliberately stops at "unknown". A client cannot see the host
// filesystem, so it cannot tell "the binary is missing" (①) from "the daemon
// is down" (④) — guessing "not_installed" there is exactly the lie D2 exists
// to prevent (it would render a `Get Ollama ↗` button to someone who already
// has Ollama installed).
//
// Pure: no React, no I/O, no globals.

import type {
  LocalModelsStatus,
  LocalRuntimeState,
} from "@0x-copilot/api-types";

/** Every value the server contract admits, for runtime validation. */
const KNOWN_RUNTIME_STATES: readonly LocalRuntimeState[] = [
  "unknown",
  "not_installed",
  "stopped",
  "running",
];

/**
 * Runtime state for a capability probe response.
 *
 * A `runtime_state` the client does not recognise (a newer server, a garbled
 * proxy) is treated as absent and falls back to `ollama_running` rather than
 * being passed through as an unrenderable state.
 */
export function deriveLocalRuntimeState(
  status: LocalModelsStatus,
): LocalRuntimeState {
  const declared = status.runtime_state;
  if (
    declared !== undefined &&
    declared !== null &&
    KNOWN_RUNTIME_STATES.includes(declared)
  ) {
    return declared;
  }
  return status.ollama_running ? "running" : "unknown";
}

/**
 * Whether this server may start/restart the runtime (`POST
 * /v1/local-models/runtime/start` can do anything but 404).
 *
 * Absent → `false`: a host that never advertised the capability must not get a
 * `Restart Ollama` button that cannot work (PRD-P8 §5).
 */
export function deriveRuntimeManaged(status: LocalModelsStatus): boolean {
  return status.runtime_managed === true;
}
