import type { SimulationReport } from "@/lib/schema";

export function ConfidenceSection({
  id,
  section,
}: {
  id: string;
  section: SimulationReport["confidence"];
}) {
  const sc = section.split_confidence;
  return (
    <section id={id} className="space-y-4 scroll-mt-8">
      <h2 className="font-serif text-2xl tracking-tight">9. Confidence (simulation entropy)</h2>
      <p className="prose-card text-base">{section.summary}</p>
      <p className="text-xs text-ink-400">
        These numbers describe the simulated society's stance distribution — they are not a
        market forecast.
      </p>

      <div className="grid gap-4 rounded border border-ink-200 bg-ink-50 p-4 text-sm sm:grid-cols-2">
        <div>
          <p className="text-xs uppercase tracking-widest text-ink-400">Largest bucket</p>
          <p className="font-mono">
            {sc.largest_bucket_stance} = {sc.largest_bucket_count}
          </p>
        </div>
        <div>
          <p className="text-xs uppercase tracking-widest text-ink-400">Second bucket</p>
          <p className="font-mono">
            {sc.second_bucket_stance ?? "—"} = {sc.second_bucket_count}
          </p>
        </div>
        <div>
          <p className="text-xs uppercase tracking-widest text-ink-400">Separation ratio</p>
          <p className="font-mono">{sc.separation_ratio.toFixed(2)}</p>
        </div>
        <div>
          <p className="text-xs uppercase tracking-widest text-ink-400">Interpretation</p>
          <p className="font-mono">{sc.interpretation}</p>
        </div>
      </div>

      {section.stance_distribution_by_round.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-ink-600">
            Per-round stance distribution ({section.stance_distribution_by_round.length} rounds)
          </summary>
          <ul className="mt-2 space-y-1 font-mono text-xs">
            {section.stance_distribution_by_round.map((round, i) => (
              <li key={i} className="text-ink-700">
                round {i + 1}:{" "}
                {round
                  .map((s) => `${s.stance}=${s.count}`)
                  .join(", ")}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
