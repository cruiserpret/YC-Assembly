"use client";
// Phase 10B+ — full per-turn transcript. Now augmented with:
//   - Round selector tabs (Round 1, 2, 3, …) so the founder can scrub
//     through stages instead of scrolling
//   - Per-round FOR / AGAINST / NEUTRAL distribution bar + counts
//   - Per-turn shift magnitude (▲ 0.X / ▼ 0.X) computed from the
//     persona's pre-ballot stance vs the turn's stance
//   - FOR / AGAINST / NEUTRAL pill on every speaker, color-coded
//   - Per-persona private ballots remain in the collapsible roster

import { useEffect, useMemo, useState } from "react";
import { getAssemblyDiscussionTurns } from "@/lib/api";
import { stripPersonaSystemCaveats } from "@/lib/caveatFilter";
import { humanizeRole, humanizeStance } from "@/lib/labels";
import {
  bucketStance,
  bucketStyle,
  formatShift,
  stanceShift,
} from "@/lib/stance";
import type {
  DiscussionTranscriptPayload,
  PrivateBallotView,
  TranscriptGroup,
  TranscriptRound,
  TranscriptTurn,
} from "@/lib/types";

const ROUND_TITLES: Record<string, string> = {
  public_opening: "Round 1 · Public opening",
  challenge: "Round 2 · Challenge",
  peer_response: "Round 3 · Peer response",
  proof_discussion: "Round 4 · What proof would change my mind",
  reflection_round: "Round 5 · Private reflection",
  final_ballot_round: "Round 6 · Private final ballot",
};

export interface DiscussionTranscriptProps {
  runId: string;
  /** Pass an existing transcript payload to skip the fetch (used by
   *  /run/[runId] when the parent already loaded it for the graph
   *  + live-distribution panels). */
  transcript?: DiscussionTranscriptPayload;
}

export function DiscussionTranscript({
  runId,
  transcript: provided,
}: DiscussionTranscriptProps) {
  const [data, setData] = useState<DiscussionTranscriptPayload | null>(
    provided ?? null,
  );
  const [error, setError] = useState<string | null>(null);
  const [openGroup, setOpenGroup] = useState<number>(0);
  const [openRound, setOpenRound] = useState<number | null>(null);

  useEffect(() => {
    if (provided) {
      setData(provided);
      return;
    }
    let cancelled = false;
    getAssemblyDiscussionTurns(runId)
      .then((d) => {
        if (cancelled) return;
        setData(d);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "Unknown error");
      });
    return () => {
      cancelled = true;
    };
  }, [runId, provided]);

  // Pre-ballot stance lookup, used to compute per-turn shift magnitudes
  const preStanceByPid = useMemo(() => {
    if (!data) return {};
    const out: Record<string, string | null> = {};
    for (const [pid, b] of Object.entries(data.private_ballots)) {
      out[pid] = b.pre?.stance ?? null;
    }
    return out;
  }, [data]);

  if (error) {
    return (
      <div
        role="alert"
        className="rounded-md border border-danger/40 bg-surface px-4 py-3 text-sm text-danger"
        data-testid="transcript-error"
      >
        Could not load transcript: {error}
      </div>
    );
  }
  if (!data) {
    return (
      <div className="rounded-md border border-border bg-surface px-4 py-3 text-sm text-text-muted">
        Loading transcript…
      </div>
    );
  }
  if (!data.groups.length) {
    return (
      <div
        className="rounded-md border border-border bg-surface px-4 py-3 text-sm text-text-muted"
        data-testid="transcript-empty"
      >
        {data.note ??
          "No transcript available for this run yet. Switch to live_founder_brief mode to see the full per-turn discussion."}
      </div>
    );
  }

  const group = data.groups[openGroup] ?? data.groups[0];
  const allRoundNumbers = group.rounds.map((r) => r.round_number);
  const activeRound = openRound ?? allRoundNumbers[0] ?? 1;

  return (
    <section
      data-testid="discussion-transcript"
      className="space-y-4 rounded-md border border-border bg-surface p-6"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <div className="space-y-1">
          <h3 className="text-lg font-semibold text-text-primary">
            What the synthetic agents said
          </h3>
          <p className="text-sm text-text-muted">
            Round-by-round transcript across {data.groups.length}{" "}
            {data.groups.length === 1 ? "group" : "groups"}. Stance pills
            collapse each agent&apos;s position to Receptive / Uncertain
            / Resistant; ▲▼ shows shift from their pre-discussion stance.
          </p>
        </div>
      </header>

      {/* Group selector */}
      <div
        className="flex flex-wrap gap-2 text-xs"
        data-testid="transcript-group-tabs"
      >
        {data.groups.map((g, i) => (
          <button
            key={g.group_index}
            type="button"
            onClick={() => {
              setOpenGroup(i);
              setOpenRound(null);
            }}
            className={`rounded-md border px-3 py-1.5 transition-colors ${
              openGroup === i
                ? "border-accent-border bg-accent-soft text-accent"
                : "border-border text-text-muted hover:border-accent-border/40"
            }`}
          >
            Group {g.group_index + 1}
            <span className="ml-2 text-text-muted">
              {g.personas.length} agents
            </span>
          </button>
        ))}
      </div>

      {/* Persona roster (private ballots) */}
      <PersonaRoster personas={group.personas} ballots={data.private_ballots} />

      {/* Round selector */}
      <div
        className="flex flex-wrap gap-1.5 text-xs"
        data-testid="transcript-round-tabs"
      >
        {group.rounds.map((r) => (
          <button
            key={r.round_number}
            type="button"
            onClick={() => setOpenRound(r.round_number)}
            className={`rounded-md border px-2.5 py-1 transition-colors ${
              activeRound === r.round_number
                ? "border-accent-border bg-accent-soft text-accent"
                : "border-border text-text-muted hover:border-accent-border/40"
            }`}
          >
            Round {r.round_number}
            <span className="ml-1.5 text-text-muted">
              · {r.turns.length}
            </span>
          </button>
        ))}
      </div>

      {/* Active round */}
      {group.rounds
        .filter((r) => r.round_number === activeRound)
        .map((round) => (
          <RoundView
            key={round.round_number}
            round={round}
            preStanceByPid={preStanceByPid}
          />
        ))}
    </section>
  );
}

function RoundView({
  round,
  preStanceByPid,
}: {
  round: TranscriptRound;
  preStanceByPid: Record<string, string | null>;
}) {
  const counts = useMemo(() => {
    let f = 0;
    let a = 0;
    let n = 0;
    for (const t of round.turns) {
      const bucket = bucketStance(t.stance);
      if (bucket === "for") f += 1;
      else if (bucket === "against") a += 1;
      else n += 1;
    }
    return { for: f, against: a, neutral: n, total: round.turns.length };
  }, [round]);

  const segWidth = (n: number) =>
    counts.total > 0 ? `${(n / counts.total) * 100}%` : "0%";

  return (
    <div data-testid={`round-${round.round_number}`} className="space-y-3">
      <header className="space-y-2">
        <h4 className="flex items-baseline gap-2 text-sm font-medium text-text-primary">
          <span
            aria-hidden
            className="inline-block h-1.5 w-1.5 rounded-full bg-accent"
          />
          {ROUND_TITLES[round.round_label] ??
            `Round ${round.round_number} · ${round.round_label}`}
          <span className="text-xs text-text-muted">
            · {round.turns.length} turns
          </span>
        </h4>
        <div
          className="flex h-1.5 w-full overflow-hidden rounded-sm bg-border"
          data-testid="round-distribution-bar"
        >
          <span
            className="bg-accent"
            style={{ width: segWidth(counts.for) }}
            title={`Receptive ${counts.for}`}
          />
          <span
            className="bg-text-muted"
            style={{ width: segWidth(counts.neutral) }}
            title={`Uncertain ${counts.neutral}`}
          />
          <span
            className="bg-danger"
            style={{ width: segWidth(counts.against) }}
            title={`Resistant ${counts.against}`}
          />
        </div>
        <p className="text-[11px] text-text-muted">
          <span className="text-accent">Receptive {counts.for}</span>
          {"  ·  "}
          <span>Uncertain {counts.neutral}</span>
          {"  ·  "}
          <span className="text-danger">Resistant {counts.against}</span>
        </p>
      </header>
      <ul className="space-y-2 border-l border-border pl-4">
        {round.turns.map((t) => (
          <TurnRow
            key={t.turn_id}
            turn={t}
            preStance={preStanceByPid[t.speaker_persona_id] ?? null}
          />
        ))}
      </ul>
    </div>
  );
}

function PersonaRoster({
  personas,
  ballots,
}: {
  personas: TranscriptGroup["personas"];
  ballots: Record<
    string,
    { pre?: PrivateBallotView; reflection?: PrivateBallotView; final?: PrivateBallotView }
  >;
}) {
  return (
    <details
      className="rounded-md border border-border bg-surface-elevated p-4"
      data-testid="persona-roster"
    >
      <summary className="cursor-pointer text-sm font-medium text-text-primary">
        Personas in this group ({personas.length}) — click to expand
        private ballots
      </summary>
      <ul className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
        {personas.map((p) => {
          const b = ballots[p.persona_id] ?? {};
          return (
            <li
              key={p.persona_id}
              className="rounded-md border border-border bg-surface p-3 text-sm"
            >
              <p className="font-medium text-text-primary">
                {p.display_name}
              </p>
              <p className="text-xs text-text-muted">
                {humanizeRole(p.role)}
              </p>
              {b.final ? (
                <PrivateBallotBlock label="Final" b={b.final} />
              ) : null}
              {b.reflection ? (
                <PrivateBallotBlock label="Reflection" b={b.reflection} />
              ) : null}
              {b.pre ? <PrivateBallotBlock label="Pre" b={b.pre} /> : null}
            </li>
          );
        })}
      </ul>
    </details>
  );
}

function PrivateBallotBlock({
  label,
  b,
}: {
  label: string;
  b: PrivateBallotView;
}) {
  const bucket = bucketStance(b.stance);
  const style = bucketStyle(bucket);
  return (
    <div className="mt-2 rounded-md border border-border/60 bg-surface-elevated p-2 text-xs text-text-body">
      <p className="mb-1 flex items-center gap-2">
        <span className="text-text-muted">{label}</span>
        <span
          className={`rounded border px-1.5 py-px text-[10px] uppercase tracking-wider ${style.borderClass} ${style.textClass}`}
          title={humanizeStance(b.stance)}
        >
          {style.label}
        </span>
        {b.is_repaired ? (
          <span
            className="rounded border border-warning/50 px-1.5 py-px text-[10px] text-warning"
            title="This ballot was created by the final-ballot repair gate (LLM strict / stricter / deterministic fallback)."
          >
            repaired
          </span>
        ) : null}
      </p>
      {(() => {
        const cleanedReasoning = stripPersonaSystemCaveats(b.reasoning);
        const cleanedObjection = stripPersonaSystemCaveats(b.top_objection ?? "");
        const cleanedProof = stripPersonaSystemCaveats(b.top_proof_need ?? "");
        return (
          <>
            {cleanedReasoning ? (
              <p
                className="text-text-body"
                data-testid="ballot-reasoning"
              >
                &ldquo;{cleanedReasoning}&rdquo;
              </p>
            ) : null}
            {cleanedObjection ? (
              <p className="mt-1 text-text-muted">
                <span className="text-text-muted">objection:</span>{" "}
                {cleanedObjection}
              </p>
            ) : null}
            {cleanedProof ? (
              <p className="text-text-muted">
                <span className="text-text-muted">proof needed:</span>{" "}
                {cleanedProof}
              </p>
            ) : null}
          </>
        );
      })()}
    </div>
  );
}

function TurnRow({
  turn,
  preStance,
}: {
  turn: TranscriptTurn;
  preStance: string | null;
}) {
  const bucket = bucketStance(turn.stance);
  const style = bucketStyle(bucket);
  const shift = stanceShift(preStance, turn.stance);
  const formatted = formatShift(shift);
  const isPeerResponse =
    turn.turn_type === "peer_response" && turn.referenced_turn_ids.length > 0;
  return (
    <li
      data-testid="transcript-turn"
      className="rounded-md border border-border bg-surface-elevated p-4"
    >
      <header className="mb-1.5 flex flex-wrap items-baseline gap-2 text-sm">
        <span
          aria-hidden
          className={`inline-block h-2 w-2 rounded-full ${style.dotClass}`}
        />
        <span className="font-medium text-text-primary">
          {turn.speaker_name}
        </span>
        <span
          className={`rounded border px-1.5 py-px text-[10px] uppercase tracking-wider ${style.borderClass} ${style.textClass}`}
          title={turn.stance ?? "no stance"}
          data-testid="turn-stance"
        >
          {style.label}
        </span>
        {formatted.arrow ? (
          <span
            className={`font-mono text-xs ${formatted.toneClass}`}
            data-testid="turn-shift"
            title={`stance shift relative to pre-discussion ballot`}
          >
            {formatted.arrow} {formatted.magnitude}
          </span>
        ) : null}
        {isPeerResponse ? (
          <span
            className="text-[10px] text-text-muted"
            title={`responding to turn ${turn.referenced_turn_ids[0]?.slice(0, 8)}`}
          >
            ↩{" "}
            {turn.referenced_turn_ids
              .slice(0, 2)
              .map((id) => id.slice(0, 8))
              .join(", ")}
          </span>
        ) : null}
        <span className="ml-auto text-[10px] text-text-muted">
          {humanizeRole(turn.speaker_role)}
        </span>
      </header>
      <p
        className="whitespace-pre-line text-sm text-text-body"
        data-testid="turn-text"
      >
        {stripPersonaSystemCaveats(turn.public_text)}
      </p>
    </li>
  );
}
