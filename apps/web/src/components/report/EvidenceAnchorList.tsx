"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { useState } from "react";

import type { EvidenceAnchorDetail } from "@/lib/schema";
import { clip } from "@/lib/format";

export function EvidenceAnchorList({
  anchors,
  details,
}: {
  anchors: string[];
  details: Record<string, EvidenceAnchorDetail>;
}) {
  const [openId, setOpenId] = useState<string | null>(null);

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-ink-400">Evidence:</span>
      {anchors.map((id) => {
        const d = details[id];
        const label = d
          ? `${d.kind} · ${d.node_class}`
          : `unhydrated`;
        return (
          <button
            key={id}
            type="button"
            onClick={() => setOpenId(id)}
            className="rounded border border-ink-200 bg-ink-50 px-2 py-0.5 font-mono text-[11px] text-ink-800 hover:bg-ink-100"
          >
            {label}
          </button>
        );
      })}

      <Dialog.Root open={openId !== null} onOpenChange={(o) => !o && setOpenId(null)}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 bg-ink-900/30" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[80vh] w-[min(640px,90vw)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded border border-ink-200 bg-white p-6 shadow-xl">
            <Dialog.Title className="font-serif text-lg">Evidence anchor</Dialog.Title>
            {openId && <AnchorDetail detail={details[openId]} id={openId} />}
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

function AnchorDetail({
  detail,
  id,
}: {
  detail: EvidenceAnchorDetail | undefined;
  id: string;
}) {
  if (!detail) {
    return (
      <div className="mt-3 space-y-1 text-sm text-ink-600">
        <p>This evidence id was referenced but not hydrated by the report.</p>
        <p className="font-mono text-xs text-ink-400">{id}</p>
      </div>
    );
  }
  const isMissing = detail.kind === "missing";
  return (
    <div className="mt-3 space-y-3 text-sm">
      <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1 text-ink-800">
        <dt className="text-ink-400">kind</dt>
        <dd className={isMissing ? "text-warn" : ""}>{detail.kind}</dd>
        <dt className="text-ink-400">node class</dt>
        <dd>{detail.node_class}</dd>
        <dt className="text-ink-400">source type</dt>
        <dd>{detail.source_type}</dd>
        {detail.source_url && (
          <>
            <dt className="text-ink-400">source URL</dt>
            <dd>
              <a
                href={detail.source_url}
                target="_blank"
                rel="noreferrer"
                className="text-ink-800 underline hover:text-ink-900"
              >
                {detail.source_url}
              </a>
            </dd>
          </>
        )}
        {detail.captured_at && (
          <>
            <dt className="text-ink-400">captured at</dt>
            <dd>{new Date(detail.captured_at).toLocaleString()}</dd>
          </>
        )}
      </dl>

      {detail.source_excerpt && (
        <div>
          <p className="text-xs uppercase tracking-widest text-ink-400">Source excerpt</p>
          <p className="mt-1 whitespace-pre-line border-l-2 border-ink-200 pl-3 italic text-ink-800">
            {clip(detail.source_excerpt, 800)}
          </p>
        </div>
      )}
      {detail.content_preview && (
        <div>
          <p className="text-xs uppercase tracking-widest text-ink-400">Content preview</p>
          <p className="mt-1 whitespace-pre-line text-ink-800">{detail.content_preview}</p>
        </div>
      )}
      <p className="font-mono text-[11px] text-ink-400">{id}</p>
    </div>
  );
}
