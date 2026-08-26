// Recovering real tool arguments from the runtime's streaming-JSON accumulator.
//
// WHY THIS EXISTS — the shape the runtime actually sends is not the shape the
// projector assumed. Captured from a real packaged run (`agent-data/v1/.../
// events.jsonl`), an `edit_file` call arrives as:
//
//   tool_call_started  { tool_name, call_id, args: {},          delta: "" }
//   tool_call_delta    { tool_name, call_id, args: {delta: "{\"file_pa"},   … }
//   tool_call_delta    { tool_name, call_id, args: {delta: "{\"file_path\": \"/Use"}, … }
//   …46 frames…
//   tool_result        { tool_name, call_id, output: {content: "Successfully…"} }
//
// `payload.args` on an INTERMEDIATE delta frame is therefore not the arguments —
// it is a one-key envelope whose `delta` string is the model's argument JSON as
// it has streamed so far.
//
// SCOPE THIS HONESTLY. The runtime's FINAL delta frame carries `args` as the
// parsed object (`{file_path, old_string, new_string}`), and the terminal
// `tool_result` / `tool_call_completed` branch keeps `prior.args`. A SETTLED
// card therefore always showed real arguments — replaying the captured stream
// confirms old and new agree on the last frame. What `updatedToolArgs` used to
// store verbatim was the envelope on every frame IN BETWEEN, so a call in
// flight rendered its arguments as
//
//   { "delta": "{\"file_path\": \"/Users/…/random.csv\", \"old_string\": …" }
//
// — escaped JSON nested in a JSON string — for the whole time it was streaming
// (46 frames for the captured edit). The fix is bounded to that window: hold
// the last value that parsed instead of painting a half-written string.
//
// Not every tool streams. `read_file` in the same capture carried its real
// arguments on `tool_call_started` (`{file_path: …}`) and emitted zero deltas,
// so both paths have to keep working.

/** The single key the runtime's streaming-argument envelope carries. */
const ACCUMULATOR_KEY = "delta";

/**
 * True when `record` is the runtime's streaming-argument envelope rather than a
 * tool's real arguments.
 *
 * The test is deliberately narrow — exactly one key, named `delta`, holding a
 * string that is a prefix of a JSON object. A tool whose own schema happens to
 * declare a lone `delta` string argument that is not JSON is therefore left
 * alone. A tool whose lone `delta` argument IS a JSON object literal would be
 * unwrapped incorrectly, which is accepted: no such tool exists in the catalog,
 * and the alternative (rendering every streamed call as escaped JSON) is the
 * bug this replaces.
 *
 * The empty string counts. The runtime opens every streamed call with
 * `args: {delta: ""}` before any argument text exists, and treating that first
 * frame as a real argument record is what stored `{delta: ""}` as the call's
 * arguments — a regression test covers the whole captured frame sequence for
 * exactly this reason.
 */
export function isStreamedArgsEnvelope(
  record: Record<string, unknown>,
): boolean {
  const keys = Object.keys(record);
  if (keys.length !== 1 || keys[0] !== ACCUMULATOR_KEY) return false;
  const value = record[ACCUMULATOR_KEY];
  if (typeof value !== "string") return false;
  const opened = value.trimStart();
  return opened === "" || opened.startsWith("{");
}

/**
 * The real arguments carried by a streaming envelope, or `undefined` while the
 * accumulated string is still a partial JSON prefix.
 *
 * Returning `undefined` mid-stream is the point: a partial prefix must leave
 * the last good parse in place rather than overwrite it with garbage. Frames
 * are cumulative and monotonically longer, so the final frame parses.
 */
export function parseStreamedArgs(
  record: Record<string, unknown>,
): Record<string, unknown> | undefined {
  if (!isStreamedArgsEnvelope(record)) return undefined;
  const raw = record[ACCUMULATOR_KEY];
  if (typeof raw !== "string") return undefined;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    // Still a prefix — expected on every frame but the last.
    return undefined;
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return undefined;
  }
  return parsed as Record<string, unknown>;
}

/**
 * The arguments to store for a tool call, given the newest `payload.args`
 * snapshot and whatever was stored before it.
 *
 * Three cases, in the order they occur on the wire:
 *   • an ordinary record (`{}` from `tool_call_started`, or a tool's real
 *     arguments) — store it as-is;
 *   • a streaming envelope that parses — store the recovered arguments;
 *   • a streaming envelope that does not parse yet — keep `prior`, so a
 *     half-written string never replaces a complete one.
 */
export function resolveToolArgs(
  snapshot: Record<string, unknown>,
  prior: Record<string, unknown> | undefined,
): Record<string, unknown> | undefined {
  if (!isStreamedArgsEnvelope(snapshot)) return snapshot;
  return parseStreamedArgs(snapshot) ?? prior;
}
