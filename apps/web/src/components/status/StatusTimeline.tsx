import { ALL_PIPELINE_STAGES, stageIndex, stageLabel } from "@/lib/format";
import type { SimulationStatus } from "@/lib/schema";
import { RoundProgress } from "./RoundProgress";

export function StatusTimeline({ status }: { status: SimulationStatus }) {
  const currentStage = (status.progress?.stage ?? status.status) as string;
  const currentIdx = stageIndex(currentStage);
  const isFailed = status.status === "failed";

  return (
    <ol className="space-y-3">
      {ALL_PIPELINE_STAGES.map((stage, i) => {
        const state =
          isFailed && stage === status.failed_stage
            ? "failed"
            : i < currentIdx
            ? "done"
            : i === currentIdx
            ? "active"
            : "upcoming";
        return (
          <li key={stage} className="flex items-start gap-3">
            <Marker state={state} />
            <div className="flex-1">
              <p
                className={
                  state === "active"
                    ? "font-medium text-ink-900"
                    : state === "done"
                    ? "text-ink-600"
                    : state === "failed"
                    ? "font-medium text-warn"
                    : "text-ink-400"
                }
              >
                {stageLabel(stage)}
              </p>
              {state === "active" && stage === "simulating" && (
                <RoundProgress progress={status.progress ?? null} />
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function Marker({ state }: { state: "done" | "active" | "upcoming" | "failed" }) {
  const base = "mt-1 h-3 w-3 shrink-0 rounded-full";
  if (state === "done") return <span className={`${base} bg-ink-800`} aria-label="done" />;
  if (state === "active")
    return (
      <span
        className={`${base} animate-pulse border-2 border-ink-800 bg-ink-50`}
        aria-label="active"
      />
    );
  if (state === "failed")
    return <span className={`${base} bg-warn`} aria-label="failed" />;
  return <span className={`${base} border border-ink-200 bg-ink-50`} aria-label="upcoming" />;
}
