// Phase 10B.1 — defensive frontend filter for system-caveat leakage
// inside persona speech. Mirrors the backend's
// PERSONA_FORBIDDEN_PHRASES list. The backend already strips these
// post-hoc, but old runs persisted before 10B.1 may still contain
// them — this filter keeps them out of the founder's view.

const FORBIDDEN_FRAGMENTS = [
  /synthetic n=/i,
  /synthetic chat/i,
  /directional, not a verdict/i,
  /directional rather than a verdict/i,
  /directional but not a verdict/i,
  /not a real-world forecast/i,
  /not a market forecast/i,
  /\bas an ai\b/i,
  /\bas a synthetic persona\b/i,
  /\bas a synthetic agent\b/i,
  /i'?m a synthetic/i,
  /this is a synthetic/i,
  /synthetic society/i,
  /this simulation/i,
  /this chat is/i,
  /\bn=24\b/i,
  /\bn=21\b/i,
  /the simulation/i,
  /\(synthetic n/i,
  /synthetic-society/i,
  /treat as directional/i,
  /treating it as directional/i,
  /synthetic conversation/i,
];

// Internal stance-calibration markers we want to hide from view but
// preserve under the hood (they're just noise to founders).
const INTERNAL_MARKERS = [
  /\s*\[stance_calibration:[^\]]+\]/g,
  /\s*\[repair_marker:[^\]]+\]/g,
  /\s*\[deterministic_fallback_marker\]/g,
];

export function stripPersonaSystemCaveats(text: string | null | undefined): string {
  if (!text) return "";
  // Strip internal markers everywhere (they may be embedded mid-text)
  let cleaned = text;
  for (const re of INTERNAL_MARKERS) {
    cleaned = cleaned.replace(re, "");
  }
  // Sentence-level scrub for forbidden fragments. Em-dashes /
  // en-dashes also break sentences here so a single line like
  // "directional, not a verdict — but $69.99 worries me" yields
  // two segments: the caveat half is dropped, the buyer half
  // survives.
  const sentences = cleaned
    .split(/(?<=[.!?])\s+|\n+|\s+[—–]\s+/)
    .map((s) => s.trim())
    .filter(Boolean);
  const surviving = sentences.filter(
    (s) => !FORBIDDEN_FRAGMENTS.some((re) => re.test(s)),
  );
  let out = surviving.join(" ").trim();
  // Strip leading "Caveat:" / "Note:" filler
  out = out.replace(/^\s*(?:Caveat|Note)\s*[:\-]\s*/i, "");
  return out;
}
