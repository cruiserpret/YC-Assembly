"use client";
// Phase 14A — thin wrapper that fetches voter payload via React Query
// and feeds it into LightweightVoterPanel.
//
// Previous behavior silently returned null on fetch error, which is
// what caused the ShelfSense AI report bug — the user saw the rest
// of the dashboard but no voter panel and no explanation of why.
// Now we always render SOMETHING (loading / error / unavailable /
// available), so the 100-voter feature stays visible to the user
// and any production regression in the fetch path is detectable.

import { useLightweightVoters } from "@/lib/useLightweightVoters";

import { LightweightVoterPanel } from "./LightweightVoterPanel";

export interface LightweightVoterPanelLiveProps {
  runId: string;
}

export function LightweightVoterPanelLive({
  runId,
}: LightweightVoterPanelLiveProps) {
  const { data, isLoading, error } = useLightweightVoters(runId);

  return (
    <LightweightVoterPanel
      payload={data}
      isLoading={isLoading}
      fetchError={error instanceof Error ? error : null}
    />
  );
}
