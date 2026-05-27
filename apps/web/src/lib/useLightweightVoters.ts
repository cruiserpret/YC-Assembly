"use client";
// Phase 14A — React Query hook for the 100-voter overlay payload.
//
// Loads GET /assembly/runs/{runId}/lightweight_voters once per run.
// The endpoint returns voter_overlay_available=false (HTTP 200) for
// runs that pre-date the Phase 12C overlay; callers should hide the
// voter panel in that case but keep rendering the rest of the report.
// Errors do NOT block the rest of the report — the panel just hides.

import { useQuery } from "@tanstack/react-query";

import { getAssemblyLightweightVoters } from "./api";
import type { LightweightVotersPayload } from "./types";

export function useLightweightVoters(
  runId: string | null | undefined,
  enabled = true,
) {
  return useQuery<LightweightVotersPayload>({
    queryKey: ["assemblyRun", runId, "lightweight_voters"],
    queryFn: () => getAssemblyLightweightVoters(runId as string),
    enabled: !!runId && enabled,
    // Voter artifacts are written once during the simulation and never
    // change afterwards. Treat them as immutable.
    staleTime: 60 * 60 * 1000, // 1 hour
    gcTime: 60 * 60 * 1000,
    retry: 1, // best-effort; failure is non-fatal for the report
  });
}
