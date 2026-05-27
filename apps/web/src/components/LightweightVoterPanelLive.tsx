"use client";
// Phase 14A — thin wrapper that fetches voter payload via React Query
// and feeds it into LightweightVoterPanel. Renders null on empty
// state or fetch error so the surrounding report keeps rendering.

import { useLightweightVoters } from "@/lib/useLightweightVoters";

import { LightweightVoterPanel } from "./LightweightVoterPanel";

export interface LightweightVoterPanelLiveProps {
  runId: string;
}

export function LightweightVoterPanelLive({
  runId,
}: LightweightVoterPanelLiveProps) {
  const { data, isLoading, isError } = useLightweightVoters(runId);

  // Fetch error → silently hide the panel; the rest of the report
  // continues. The voter overlay is supplementary, not blocking.
  if (isError) return null;

  return (
    <LightweightVoterPanel
      payload={data}
      isLoading={isLoading}
    />
  );
}
