"use client";

import * as Collapsible from "@radix-ui/react-collapsible";
import { useState } from "react";

const KIND_LABEL: Record<string, string> = {
  agent_response: "Agent response",
  debate_turn: "Debate turn",
  simulation_round: "Round",
  agent: "Agent",
  evidence_item: "Evidence item",
};

export function SimulationReferenceList({
  refs,
}: {
  refs: { kind: string; target_id: string; note?: string | null }[];
}) {
  const [open, setOpen] = useState(false);
  return (
    <Collapsible.Root open={open} onOpenChange={setOpen}>
      <Collapsible.Trigger asChild>
        <button className="rounded border border-ink-200 bg-ink-50 px-2 py-0.5 text-xs text-ink-800 hover:bg-ink-100">
          Simulation references ({refs.length}) {open ? "▴" : "▾"}
        </button>
      </Collapsible.Trigger>
      <Collapsible.Content className="mt-2 w-full">
        <ul className="space-y-1 rounded border border-ink-200 bg-ink-50 p-2 text-xs">
          {refs.map((ref, i) => (
            <li key={`${ref.target_id}-${i}`} className="flex flex-wrap items-baseline gap-x-2">
              <span className="text-ink-600">
                ↳ {KIND_LABEL[ref.kind] ?? ref.kind}
              </span>
              <span className="font-mono text-[11px] text-ink-400">{ref.target_id}</span>
              {ref.note && <span className="text-ink-600">— {ref.note}</span>}
            </li>
          ))}
        </ul>
      </Collapsible.Content>
    </Collapsible.Root>
  );
}
