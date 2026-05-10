import type { SimulationStatus } from "@/lib/schema";

export function RoundProgress({
  progress,
}: {
  progress: SimulationStatus["progress"];
}) {
  if (!progress) return null;
  const round = progress.current_round ?? null;
  const idx = numeric(progress.round_index);
  const done = numeric(progress.agents_completed);
  const total = numeric(progress.agents_total);

  const ratio = done && total ? Math.min(1, done / total) : 0;

  return (
    <div className="mt-2 space-y-1 text-xs text-ink-600">
      {round && idx ? (
        <p>
          Round {idx} of 7 — {humanize(String(round))}
        </p>
      ) : (
        <p>Setting up the round…</p>
      )}
      {done !== null && total !== null && (
        <div className="flex items-center gap-2">
          <div
            className="h-1.5 flex-1 overflow-hidden rounded bg-ink-200"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={total ?? 0}
            aria-valuenow={done ?? 0}
          >
            <div
              className="h-full bg-ink-800"
              style={{ width: `${(ratio * 100).toFixed(1)}%` }}
            />
          </div>
          <span className="text-ink-400">
            {done} / {total} agents
          </span>
        </div>
      )}
    </div>
  );
}

function numeric(v: number | string | null | undefined): number | null {
  if (v === null || v === undefined) return null;
  const n = typeof v === "number" ? v : parseInt(v, 10);
  return Number.isFinite(n) ? n : null;
}

function humanize(s: string): string {
  return s.replace(/_/g, " ");
}
