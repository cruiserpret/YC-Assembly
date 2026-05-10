import type { SimulationReport } from "@/lib/schema";

export function DebateShiftMarkers({
  id,
  section,
}: {
  id: string;
  section: SimulationReport["debate_shift_markers"];
}) {
  return (
    <section id={id} className="space-y-4 scroll-mt-8">
      <h2 className="font-serif text-2xl tracking-tight">8. Debate shift markers</h2>
      <p className="prose-card text-base">{section.summary}</p>
      {section.markers.length === 0 ? (
        <p className="text-sm text-ink-400">
          No stance shifts were recorded across rounds.
        </p>
      ) : (
        <div className="overflow-x-auto rounded border border-ink-200">
          <table className="min-w-full text-sm">
            <thead className="bg-ink-100 text-xs uppercase tracking-widest text-ink-600">
              <tr>
                <th className="px-3 py-2 text-left">Round</th>
                <th className="px-3 py-2 text-left">From → To</th>
                <th className="px-3 py-2 text-left">Count</th>
                <th className="px-3 py-2 text-left">Triggered by</th>
                <th className="px-3 py-2 text-left">Argument</th>
              </tr>
            </thead>
            <tbody>
              {section.markers.map((m, i) => (
                <tr key={i} className="border-t border-ink-200">
                  <td className="px-3 py-2 align-top">{m.round_number}</td>
                  <td className="px-3 py-2 align-top">
                    <span className="font-mono text-xs text-ink-600">
                      {m.from_stance} → {m.to_stance}
                    </span>
                  </td>
                  <td className="px-3 py-2 align-top">{m.count}</td>
                  <td className="px-3 py-2 align-top">
                    <span className="font-mono text-xs text-ink-600">
                      {m.triggered_by ?? "—"}
                    </span>
                  </td>
                  <td className="px-3 py-2 align-top text-ink-800">
                    {m.example_argument ?? <span className="text-ink-400">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
