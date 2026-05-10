"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { useState } from "react";

import type { EvidenceAnchorDetail, SimulationReport } from "@/lib/schema";
import { clip } from "@/lib/format";

export function EvidenceLedger({
  id,
  section,
  details,
}: {
  id: string;
  section: SimulationReport["evidence_ledger"];
  details: Record<string, EvidenceAnchorDetail>;
}) {
  return (
    <section id={id} className="space-y-4 scroll-mt-8">
      <h2 className="font-serif text-2xl tracking-tight">9. Evidence ledger</h2>

      <div className="grid gap-4 sm:grid-cols-3">
        <Counter label="Direct evidence" value={section.counts.direct_count} />
        <Counter label="Analogical evidence" value={section.counts.analogical_count} />
        <Counter
          label="Missing evidence"
          value={section.counts.missing_count}
          warn
        />
      </div>

      <MissingEvidencePanel missing={section.missing} details={details} />
      <ClaimTraceabilityTable claims={section.claim_traceability} />
    </section>
  );
}

function Counter({
  label,
  value,
  warn,
}: {
  label: string;
  value: number;
  warn?: boolean;
}) {
  return (
    <div
      className={`rounded border p-4 ${
        warn ? "border-warn bg-warn-subtle" : "border-ink-200 bg-ink-50"
      }`}
    >
      <p className={`text-xs uppercase tracking-widest ${warn ? "text-warn" : "text-ink-400"}`}>
        {label}
      </p>
      <p className="mt-1 font-mono text-2xl">{value}</p>
    </div>
  );
}

function MissingEvidencePanel({
  missing,
  details,
}: {
  missing: SimulationReport["evidence_ledger"]["missing"];
  details: Record<string, EvidenceAnchorDetail>;
}) {
  const [openId, setOpenId] = useState<string | null>(null);
  return (
    <div className="space-y-3 rounded border border-warn bg-warn-subtle p-4">
      <h3 className="font-serif text-lg text-warn">What this simulation didn't have</h3>
      {missing.length === 0 ? (
        <p className="text-sm text-ink-600">
          No missing-evidence entries were recorded for this run.
        </p>
      ) : (
        <ul className="space-y-2 text-sm">
          {missing.map((m) => (
            <li
              key={m.evidence_id}
              className="flex flex-wrap items-baseline gap-x-2"
            >
              <span className="font-mono text-xs text-warn">{m.node_class}</span>
              <span className="text-ink-800">— {clip(m.summary, 220)}</span>
              <button
                type="button"
                onClick={() => setOpenId(m.evidence_id)}
                className="ml-auto text-xs text-ink-600 underline hover:text-ink-900"
              >
                inspect
              </button>
            </li>
          ))}
        </ul>
      )}
      <Dialog.Root open={openId !== null} onOpenChange={(o) => !o && setOpenId(null)}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 bg-ink-900/30" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[80vh] w-[min(640px,90vw)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded border border-ink-200 bg-white p-6 shadow-xl">
            <Dialog.Title className="font-serif text-lg">Missing evidence</Dialog.Title>
            {openId && (
              <p className="mt-3 text-sm text-ink-800">
                This is a row marked as missing — the simulation knows the artifact was expected
                but never gathered it.
              </p>
            )}
            {openId && details[openId] && (
              <pre className="mt-3 whitespace-pre-wrap font-mono text-[11px] text-ink-700">
                {JSON.stringify(details[openId], null, 2)}
              </pre>
            )}
            <div className="mt-4 text-right">
              <Dialog.Close asChild>
                <button className="rounded border border-ink-200 px-3 py-1 text-sm text-ink-800 hover:bg-ink-100">
                  Close
                </button>
              </Dialog.Close>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}

function ClaimTraceabilityTable({
  claims,
}: {
  claims: SimulationReport["evidence_ledger"]["claim_traceability"];
}) {
  return (
    <div className="space-y-3 rounded border border-ink-200 p-4">
      <h3 className="font-serif text-lg">Claim traceability</h3>
      {claims.length === 0 ? (
        <p className="text-sm text-ink-600">
          This simulation did not produce verbatim factual quotations. Subjective interpretation
          lives in each section's summary.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-ink-100 text-xs uppercase tracking-widest text-ink-600">
              <tr>
                <th className="px-3 py-2 text-left">Claim</th>
                <th className="px-3 py-2 text-left">Type</th>
                <th className="px-3 py-2 text-left">Basis</th>
                <th className="px-3 py-2 text-left">Source</th>
              </tr>
            </thead>
            <tbody>
              {claims.map((c) => (
                <tr key={c.claim_id} className="border-t border-ink-200 align-top">
                  <td className="px-3 py-2 text-ink-800">
                    {c.claim_text}
                    <p className="mt-1 text-xs italic text-ink-600">"{c.source_excerpt}"</p>
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">{c.claim_type}</td>
                  <td className="px-3 py-2 font-mono text-xs">{c.basis}</td>
                  <td className="px-3 py-2">
                    {c.source_url ? (
                      <a
                        href={c.source_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-xs underline hover:text-ink-900"
                      >
                        {c.source_url}
                      </a>
                    ) : (
                      <span className="text-xs text-ink-400">no URL</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
