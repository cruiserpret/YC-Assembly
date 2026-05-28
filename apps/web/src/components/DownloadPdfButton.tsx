"use client";
// Phase 12+ — Real PDF download.
//
// One click → real .pdf file. No print dialog, no Save-As prompt — the
// PDF is rendered client-side via @react-pdf/renderer and streamed to
// a Blob download.
//
// @react-pdf/renderer is dynamically imported at button click so it
// stays out of the initial JS bundle (it pulls in ~500 KB of layout
// + PDF primitives, only needed at download time).

import { useState } from "react";

import type {
  CohortsPayload,
  DiscussionPayload,
  DiscussionTranscriptPayload,
  FounderReport,
  IntentPayload,
  LightweightVotersPayload,
  PersonasPayload,
} from "@/lib/types";

export interface DownloadPdfButtonProps {
  runId: string;
  productName?: string;
  report?: FounderReport | null;
  intent?: IntentPayload | null;
  cohorts?: CohortsPayload | null;
  personas?: PersonasPayload | null;
  discussion?: DiscussionPayload | null;
  transcript?: DiscussionTranscriptPayload | null;
  voters?: LightweightVotersPayload | null;
  className?: string;
}

export function DownloadPdfButton({
  runId,
  productName,
  report,
  intent,
  cohorts,
  personas,
  discussion,
  transcript,
  voters,
  className,
}: DownloadPdfButtonProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ready = !!(report && transcript);

  async function onDownload() {
    setError(null);
    if (!ready || !report || !transcript) {
      setError("Report data still loading — try again in a moment.");
      return;
    }
    setBusy(true);
    try {
      // Phase 14B — fetch the voter payload AT CLICK TIME if we
      // don't already have it in props. React Query will return the
      // cached value immediately if it's been fetched on the same
      // page; otherwise it forces a fresh GET. This eliminates the
      // race where users clicked Download before useLightweightVoters
      // completed and got the "unavailable" section instead of the
      // voter graph.
      let votersForPdf = voters ?? null;
      if (!votersForPdf || !votersForPdf.voter_overlay_available) {
        try {
          const { getAssemblyLightweightVoters } = await import(
            "@/lib/api"
          );
          votersForPdf = await getAssemblyLightweightVoters(runId);
        } catch {
          // Endpoint failed at click time — fall through. The PDF
          // renderer's unavailable notice will surface the reason.
        }
      }

      // Lazy-load both the renderer and the document component. Keeps
      // the initial bundle small and avoids SSR-time imports of any
      // browser-only modules inside @react-pdf/renderer.
      const [{ pdf }, { PdfReportDocument }] = await Promise.all([
        import("@react-pdf/renderer"),
        import("./PdfReportDocument"),
      ]);

      const doc = (
        <PdfReportDocument
          runId={runId}
          productName={productName ?? "Synthetic society report"}
          report={report}
          intent={intent ?? null}
          cohorts={cohorts ?? null}
          personas={personas ?? null}
          discussion={discussion ?? null}
          transcript={transcript}
          voters={votersForPdf ?? null}
        />
      );

      const blob = await pdf(doc).toBlob();
      const url = URL.createObjectURL(blob);
      const slug = (productName ?? "assembly-report")
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "");
      const a = document.createElement("a");
      a.href = url;
      a.download = `${slug || "assembly-report"}-${runId.slice(0, 8)}.pdf`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={onDownload}
        disabled={!ready || busy}
        data-testid="download-pdf"
        className={`inline-flex items-center justify-center gap-2 rounded-md bg-accent px-5 py-2.5 text-sm font-semibold text-background transition-shadow hover:shadow-accent-glow disabled:opacity-60 disabled:cursor-not-allowed ${className ?? ""}`}
      >
        <span aria-hidden>↓</span>
        {!ready
          ? "Preparing report…"
          : busy
            ? "Building PDF…"
            : "Download PDF report"}
      </button>
      {error ? (
        <p
          role="alert"
          className="text-xs text-danger"
          data-testid="download-pdf-error"
        >
          Could not generate PDF: {error}
        </p>
      ) : (
        <p className="text-xs text-text-muted">
          One-click .pdf — every section expanded, including the full
          group-by-group debate transcript. No print dialog needed.
        </p>
      )}
    </div>
  );
}
