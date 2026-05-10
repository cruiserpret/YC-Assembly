"use client";
// Phase 10B+ — run detail page. Shows progress polling and, once
// complete, switches to the cockpit (graph + debate + live
// distribution + report). Failed runs stay on the progress view
// (no fake report).

import { useState } from "react";
import { RunCockpit } from "@/components/RunCockpit";
import { RunProgress } from "@/components/RunProgress";
import type { RunStatusResponse } from "@/lib/types";

export default function RunPage({
  params,
}: {
  params: { runId: string };
}) {
  const [status, setStatus] = useState<RunStatusResponse | null>(null);

  return (
    <div className="space-y-8">
      <RunProgress
        runId={params.runId}
        onComplete={(s) => setStatus(s)}
      />
      {status?.status === "complete" ? (
        <RunCockpit runId={params.runId} />
      ) : null}
    </div>
  );
}
