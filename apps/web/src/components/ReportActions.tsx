"use client";
// Phase 10B.5 — report-page action bar.
//
// Renders three founder-facing actions side-by-side once a run is
// complete:
//   1. Copy report link (clipboard write + small toast)
//   2. Download in-depth report (delegates to DownloadReportButton)
//   3. Run another product (returns to the brief form)
//
// Lives separately from DownloadReportButton so the dashboard can
// place all three together in a polished cluster without growing
// DownloadReportButton's prop surface.

import { useState } from "react";
import Link from "next/link";
import { DownloadReportButton } from "./DownloadReportButton";
import type {
  CohortsPayload,
  DiscussionPayload,
  DiscussionTranscriptPayload,
  FounderReport,
  IntentPayload,
  PersonasPayload,
} from "@/lib/types";

export interface ReportActionsProps {
  runId: string;
  productName?: string;
  report?: FounderReport | null;
  intent?: IntentPayload | null;
  cohorts?: CohortsPayload | null;
  personas?: PersonasPayload | null;
  discussion?: DiscussionPayload | null;
  transcript?: DiscussionTranscriptPayload | null;
}

export function ReportActions({
  runId,
  productName,
  report,
  intent,
  cohorts,
  personas,
  discussion,
  transcript,
}: ReportActionsProps) {
  const [copied, setCopied] = useState(false);

  async function onCopyLink() {
    if (typeof window === "undefined") return;
    const url = window.location.href;
    try {
      // Modern clipboard API; fall back to the legacy form if the
      // browser refuses (e.g. http origin).
      if (
        navigator.clipboard &&
        typeof navigator.clipboard.writeText === "function"
      ) {
        await navigator.clipboard.writeText(url);
      } else {
        const ta = document.createElement("textarea");
        ta.value = url;
        ta.setAttribute("readonly", "");
        ta.style.position = "absolute";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopied(false);
    }
  }

  return (
    <section
      data-testid="report-actions"
      className="flex flex-wrap items-center gap-3 rounded-md border border-border bg-surface p-4"
    >
      <button
        type="button"
        onClick={onCopyLink}
        data-testid="copy-report-link"
        className="inline-flex items-center gap-2 rounded-md border border-border bg-surface-elevated px-4 py-2 text-sm text-text-body transition-colors hover:border-accent-border hover:text-accent"
      >
        {copied ? "Link copied" : "Copy report link"}
      </button>
      <DownloadReportButton
        runId={runId}
        productName={productName}
        report={report}
        intent={intent}
        cohorts={cohorts}
        personas={personas}
        discussion={discussion}
        transcript={transcript}
      />
      <Link
        href="/"
        data-testid="run-another-product"
        className="inline-flex items-center gap-2 rounded-md border border-border bg-surface-elevated px-4 py-2 text-sm text-text-body transition-colors hover:border-accent-border hover:text-accent"
      >
        Run another product
      </Link>
      {copied ? (
        <span
          role="status"
          aria-live="polite"
          data-testid="copy-link-toast"
          className="ml-auto text-xs text-accent"
        >
          ✓ Report URL copied to clipboard
        </span>
      ) : null}
    </section>
  );
}
