"use client";

import { useQuery } from "@tanstack/react-query";
import { getSimulationStatus } from "./api";
import type { SimulationStatus } from "./schema";

const TERMINAL = new Set(["reported", "failed"]);

/**
 * Polling hook for /status. Backs off as the simulation gets longer:
 *   - 2s while it's actively running
 *   - 4s after 60s elapsed
 *   - 8s after 5min elapsed
 * Stops polling on terminal status.
 */
export function useSimulationStatus(id: string, enabled = true) {
  return useQuery({
    queryKey: ["simulation", id, "status"],
    queryFn: () => getSimulationStatus(id),
    enabled,
    refetchInterval: (query) => {
      const data = query.state.data as SimulationStatus | undefined;
      if (data && TERMINAL.has(data.status)) {
        return false;
      }
      const startedAt = query.state.dataUpdatedAt;
      const elapsedMs = startedAt ? Date.now() - startedAt : 0;
      if (elapsedMs > 5 * 60 * 1000) return 8000;
      if (elapsedMs > 60 * 1000) return 4000;
      return 2000;
    },
    refetchIntervalInBackground: false,
  });
}
