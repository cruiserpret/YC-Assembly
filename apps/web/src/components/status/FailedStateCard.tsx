import type { SimulationStatus } from "@/lib/schema";

export function FailedStateCard({ status }: { status: SimulationStatus }) {
  const stage = status.failed_stage ?? "unknown";
  const kind = status.error?.kind ?? "unknown";
  const message = status.error?.message ?? "No error message recorded.";
  return (
    <div role="alert" className="rounded border border-warn bg-warn-subtle p-4 text-sm">
      <p className="font-medium text-warn">This simulation stopped at: {stage}</p>
      <p className="mt-2 text-ink-800">
        <span className="font-mono text-xs text-ink-600">{kind}</span>
      </p>
      <p className="mt-2 leading-relaxed text-ink-800">{message}</p>
    </div>
  );
}
