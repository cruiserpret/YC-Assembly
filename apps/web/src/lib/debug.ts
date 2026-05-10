/**
 * Debug-mode gate. Build-time env, NOT a runtime UI flag — there is no
 * way for a normal user to flip this without rebuilding. Default OFF.
 *
 * When ON, the report page mounts a `<DebugPanel />` that surfaces raw
 * JSON. When OFF, the panel never renders, and no raw JSON / prompt
 * snapshot / llm_call_log content reaches the DOM.
 */
export const DEBUG_MODE: boolean =
  (process.env.NEXT_PUBLIC_ASSEMBLY_DEBUG ?? "").toLowerCase() === "true";

/**
 * Public-mode gate. When TRUE, the UI hides developer-facing controls
 * (mode picker, raw cost / LLM call counts, fixture_demo) and renders
 * founder-friendly copy instead. Default TRUE — keep dev controls
 * visible only when explicitly opted out (e.g. `NEXT_PUBLIC_ASSEMBLY_PUBLIC_MODE=false`
 * for local dev). Phase 10B.5.
 */
export const PUBLIC_MODE: boolean =
  ((process.env.NEXT_PUBLIC_ASSEMBLY_PUBLIC_MODE ?? "").toLowerCase()
   !== "false");
