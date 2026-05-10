/**
 * Closed-enum badge — never a percentage. Phase 7 deliberately keeps
 * confidence qualitative ("thin / moderate / clear") so the report
 * cannot be misread as a forecast.
 */
export function ConfidenceBadge({ level }: { level: string }) {
  const cls =
    level === "clear"
      ? "border-accent bg-accent-subtle text-accent"
      : level === "thin"
      ? "border-ink-200 bg-ink-100 text-ink-600"
      : "border-ink-200 bg-ink-50 text-ink-800";
  return (
    <span
      className={`shrink-0 rounded border px-2 py-0.5 text-xs font-medium ${cls}`}
      title="Qualitative confidence — not a forecast"
    >
      confidence: {level}
    </span>
  );
}
