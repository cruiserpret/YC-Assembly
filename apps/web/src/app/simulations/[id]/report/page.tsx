"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { useEffect } from "react";

import { NotReadyCard } from "@/components/status/NotReadyCard";
import { ReportShell } from "@/components/report/ReportShell";
import { getSimulationReport } from "@/lib/api";

export default function ReportPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = params.id;

  const { data, isLoading, error } = useQuery({
    queryKey: ["simulation", id, "report"],
    queryFn: () => getSimulationReport(id),
    refetchOnMount: true,
  });

  useEffect(() => {
    if (data?.kind === "report_not_ready") {
      const t = setTimeout(() => router.push(`/simulations/${id}/status`), 1000);
      return () => clearTimeout(t);
    }
  }, [data?.kind, id, router]);

  if (isLoading) return <p className="text-sm text-ink-400">Loading report…</p>;
  if (error || !data) {
    return (
      <div>
        <h1 className="font-serif text-2xl">Report not found</h1>
        <p className="mt-2 text-sm text-ink-600">
          The simulation id is unknown. Start a new run from the home page.
        </p>
      </div>
    );
  }

  if (data.kind === "report_not_ready") {
    return <NotReadyCard currentStatus={data.current_status} guidance={data.guidance} />;
  }

  return <ReportShell report={data.report} />;
}
