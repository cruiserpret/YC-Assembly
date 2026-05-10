"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect } from "react";

import { FailedStateCard } from "@/components/status/FailedStateCard";
import { StatusTimeline } from "@/components/status/StatusTimeline";
import { useSimulationStatus } from "@/lib/poll";

export default function StatusPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = params.id;
  const { data, isLoading, error } = useSimulationStatus(id);

  useEffect(() => {
    if (data?.status === "reported") {
      const t = setTimeout(() => router.push(`/simulations/${id}/report`), 800);
      return () => clearTimeout(t);
    }
  }, [data?.status, id, router]);

  if (isLoading) {
    return <p className="text-sm text-ink-400">Loading simulation…</p>;
  }
  if (error || !data) {
    return (
      <div className="space-y-2">
        <h1 className="font-serif text-2xl">Simulation not found</h1>
        <p className="text-sm text-ink-600">
          The simulation id is unknown. Start a new run from the home page.
        </p>
      </div>
    );
  }

  return (
    <section className="space-y-6">
      <div>
        <h1 className="font-serif text-3xl tracking-tight">Simulation status</h1>
        <p className="mt-1 text-xs text-ink-400">id: {id}</p>
      </div>
      <StatusTimeline status={data} />
      {data.status === "failed" && <FailedStateCard status={data} />}
      {data.status === "reported" && (
        <div className="rounded border border-accent bg-accent-subtle p-4 text-sm">
          Report ready. Redirecting…
        </div>
      )}
    </section>
  );
}
