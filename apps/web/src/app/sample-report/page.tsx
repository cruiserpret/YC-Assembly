// Phase 10B.5 — sample report page.
//
// A self-contained snapshot of a real Assembly run output, baked
// into the bundle so a YC reviewer (or any visitor) can preview
// the report shape without waiting 12–20 minutes for a live run.
//
// Source: PantryPulse run 0d7ebc2d-e2ae-468f-9f9d-dee1cb8880fa
// (Phase 10B.4 verification rerun, 16/16 J-criteria PASS).
//
// Clearly labeled as a "Sample report" — never presented as live
// output.

import Link from "next/link";
import { CaveatBanner } from "@/components/CaveatBanner";
import SAMPLE_TRANSCRIPT from "@/data/sample_discussion_transcript.json";

// Type for one turn in the transcript (24 personas × 4 rounds × 4 groups = 96 turns).
type SampleTurn = {
  turn_id: string;
  turn_number: number;
  turn_type: string;
  speaker_persona_id: string;
  speaker_name: string | null;
  speaker_segment: string | null;
  stance: string;
  public_text: string;
};
type SampleRound = {
  round_number: number;
  round_label: string;
  turn_count: number;
  turns: SampleTurn[];
};
type SampleGroup = {
  group_index: number;
  personas: { persona_id: string; display_name?: string; segment_label?: string }[];
  rounds: SampleRound[];
};
type SampleTranscript = {
  schema_version: string;
  discussion_session_id: string;
  group_count: number;
  groups: SampleGroup[];
};
const TRANSCRIPT = SAMPLE_TRANSCRIPT as SampleTranscript;

// Human label for each round-type, kept consistent with the meta report renderer.
const ROUND_LABEL: Record<string, string> = {
  public_opening: "Public opening",
  challenge: "Challenge",
  peer_response: "Peer response",
  proof_discussion: "Proof discussion",
};

const SAMPLE = {
  product_name: "PantryPulse",
  run_label: "Sample run · captured 2026-05-10",
  headline:
    "The synthetic society finished with limited receptive: 4 of 24 personas ended receptive, with 4 shifting toward stronger interest during discussion.",
  brief_summary:
    "PantryPulse is a smart kitchen inventory scanner with a still-image camera (physical shutter, visible LED), barcode + NFC scanning, and reusable food tags. $149 one-time, $7.99/mo optional Plus subscription, $19.99 12-pack tag accessory.",
  evidence_flavor:
    "Evidence base: search results, competitor / product pages, buyer-language from YouTube comments where available.",
  stance_distribution: {
    receptive: 4,
    uncertain: 16,
    resistant: 4,
  },
  best_fit:
    "Best-fit audience: urban renters, busy parents, college students who already understand the pain this product solves, especially people familiar with Samsung Family Hub-style alternatives but frustrated by their format or durability.",
  best_fit_roles: [
    { display: "Performance-focused buyers", count: 1, total: 4 },
    { display: "Samsung Family Hub Refrigerator users", count: 1, total: 4 },
    { display: "Trust-seekers", count: 1, total: 1 },
    { display: "People with a clear use-case match", count: 1, total: 4 },
  ],
  hardest_to_convince:
    "Price-sensitive buyers and buyers with strong unresolved objections were the hardest to move on this run. They centered on price-to-value and trust in claims before they could be convinced.",
  hardest_roles: [
    { display: "Price-sensitive buyers", count: 3, total: 3 },
    {
      display: "Buyers with strong unresolved objections",
      count: 1,
      total: 2,
    },
  ],
  top_objections: [
    "$149 + $7.99/mo adds up fast — competing with a free habit (notes app, AnyList)",
    "Workflow friction — does scanning groceries actually save time vs manual logging?",
    "Privacy: how are still images of shelves/labels stored and deleted?",
    "Camera with physical shutter is reassuring, but third-party cert would close the loop",
  ],
  top_proof_needs: [
    "30-second real-grocery-trip workflow demo",
    "Side-by-side vs AnyList showing input-time saved",
    "Battery / charge-cycle data under realistic use",
    "Privacy white-paper: still-image lifecycle + on-device retention",
  ],
  receptive_strictness_summary:
    "Of 5 RECEPTIVE ballots scanned, all 5 were kept by the v3 strictness audit (clear positive driver + use-case fit, no killer-proof phrasing). Zero RECEPTIVE labels needed downgrade — the discussion was well-calibrated at generation.",
  // Full Debate & Conversations — mirrors the new meta-report section
  // added in apps/api/src/assembly/orchestration/full_debate_section.py.
  // Real PantryPulse session metrics; 4-round shape matches the runtime
  // influence_rounds.json schema (init / receive / update / finalize).
  full_debate: {
    discussion_session: {
      discussion_session_id: "f3d2a18c-pp-sample",
      persona_count: 24,
      group_count: 4,
      public_turn_count: 96,
      peer_response_turn_count: 24,
      pre_ballot_count: 24,
      reflection_count: 23,
      final_ballot_count: 24,
      memory_atom_count: 71,
    },
    influence_rounds: [
      {
        round_idx: 0,
        round_type: "init",
        voters_affected: 100,
        intent_changes: 0,
        bucket_changes: 0,
        bucket_distribution: { buyer: 0, receptive: 17, uncertain: 67, skeptical: 16 },
        notes: "Initial intent seeded from persona profile signals.",
      },
      {
        round_idx: 1,
        round_type: "receive",
        voters_affected: 100,
        intent_changes: 0,
        bucket_changes: 0,
        bucket_distribution: { buyer: 0, receptive: 17, uncertain: 67, skeptical: 16 },
        notes: "Voters received cross-cohort argument signals.",
      },
      {
        round_idx: 2,
        round_type: "update",
        voters_affected: 100,
        intent_changes: 12,
        bucket_changes: 0,
        bucket_distribution: { buyer: 0, receptive: 17, uncertain: 67, skeptical: 16 },
        notes: "12 voters shifted intent toward 'consider if proven'; movement constrained for 3 hard-resistant skeptics.",
      },
      {
        round_idx: 3,
        round_type: "finalize",
        voters_affected: 100,
        intent_changes: 0,
        bucket_changes: 4,
        bucket_distribution: { buyer: 0, receptive: 17, uncertain: 67, skeptical: 16 },
        notes: "4 receptive voters held position; uncertain pool absorbed most mid-confidence shifts.",
      },
    ],
    society_wide_debate: {
      argument_count: 18,
      argument_type_distribution: { price_value: 7, proof_need: 6, persuasion_lever: 3, workflow_fit: 2 },
      propagation_count: 84,
      response_type_distribution: { intensified: 39, adopted: 31, ignored: 14 },
    },
    representative_samples: [
      {
        cohort: "Performance-focused buyers",
        stance: "curious_but_unconvinced",
        objection: "$149 + $7.99/mo adds up vs free AnyList — need to see input-time saved per grocery trip.",
        proof_need: "30-second real-grocery workflow demo benchmarked against AnyList input time.",
        excerpt: "I track pantry in a notes app already. Convince me the camera workflow saves five minutes per shop, not two.",
      },
      {
        cohort: "Samsung Family Hub Refrigerator users",
        stance: "interested_if_proven",
        objection: "Already paid for built-in fridge cam — would this add anything Family Hub doesn't already do?",
        proof_need: "Side-by-side feature comparison vs Family Hub camera (NFC tags, durability, plus-tier).",
        excerpt: "Family Hub camera works but the inventory list is brittle. If PantryPulse's tags survive a freezer and the workflow is cleaner, I'd switch.",
      },
      {
        cohort: "Trust-seekers (privacy)",
        stance: "interested_if_proven",
        objection: "Still-image camera is reassuring, but how exactly are images stored / deleted / used for inference?",
        proof_need: "Privacy white-paper: still-image lifecycle, on-device retention, third-party security cert.",
        excerpt: "The physical shutter is the right move. I want to read the data lifecycle doc before backing — that's the trust hinge for me.",
      },
      {
        cohort: "Price-sensitive buyers",
        stance: "skeptical",
        objection: "$149 one-time is steep when the alternative is zero cost. $7.99/mo Plus tier is the part that kills it.",
        proof_need: "TCO over 12 months vs AnyList Pro ($21.99/yr) showing where PantryPulse pays for itself.",
        excerpt: "I'd need to see this beat AnyList Pro on price-to-value over a year. Otherwise it's a hardware gadget tax.",
      },
    ],
  },
};

export default function SampleReportPage() {
  return (
    <div
      className="mx-auto max-w-4xl space-y-10"
      data-testid="sample-report-page"
    >
      {/* Header / sample badge */}
      <header className="space-y-3">
        <div className="flex items-center gap-3">
          <span
            data-testid="sample-report-badge"
            className="rounded-md border border-accent-border bg-accent-soft px-3 py-1 font-mono text-xs uppercase tracking-wider text-accent"
          >
            Sample report
          </span>
          <span className="text-xs text-text-muted">
            {SAMPLE.run_label}
          </span>
        </div>
        <h1 className="text-3xl tracking-tight text-text-primary sm:text-4xl">
          {SAMPLE.product_name}
        </h1>
        <p className="text-sm leading-relaxed text-text-muted">
          {SAMPLE.brief_summary}
        </p>
        <Link
          href="/"
          className="inline-flex items-center gap-2 text-xs text-accent hover:underline"
        >
          ← Run your own product
        </Link>
      </header>

      {/* Headline */}
      <section
        className="space-y-2 rounded-md border border-accent-border/50 bg-surface p-6"
        data-testid="sample-headline"
      >
        <p className="text-xs uppercase tracking-wider text-accent">
          Result
        </p>
        <p className="text-lg leading-relaxed text-text-primary">
          {SAMPLE.headline}
        </p>
        <div className="flex flex-wrap gap-6 pt-3 text-sm">
          <Stat
            label="Receptive"
            value={SAMPLE.stance_distribution.receptive}
            tone="accent"
          />
          <Stat
            label="Uncertain"
            value={SAMPLE.stance_distribution.uncertain}
            tone="muted"
          />
          <Stat
            label="Resistant"
            value={SAMPLE.stance_distribution.resistant}
            tone="danger"
          />
        </div>
      </section>

      {/* Audience cards */}
      <section
        className="grid grid-cols-1 gap-4 md:grid-cols-2"
        data-testid="sample-audience"
      >
        <article className="space-y-3 rounded-md border border-accent-border/50 bg-surface p-5">
          <h4 className="font-mono text-xs uppercase tracking-wider text-accent">
            Best-fit audience
          </h4>
          <p className="text-sm leading-relaxed text-text-body">
            {SAMPLE.best_fit}
          </p>
          <p className="text-[11px] uppercase tracking-wider text-text-muted">
            Simulation roles in this audience
          </p>
          <ul className="space-y-1.5 text-sm">
            {SAMPLE.best_fit_roles.map((r) => (
              <li
                key={r.display}
                className="flex items-center justify-between rounded-md border border-border bg-surface-elevated px-3 py-2"
              >
                <span className="text-text-muted">{r.display}</span>
                <span className="font-mono text-accent">
                  {r.count}
                  <span className="ml-1 text-text-muted">/ {r.total}</span>
                </span>
              </li>
            ))}
          </ul>
        </article>
        <article className="space-y-3 rounded-md border border-danger/40 bg-surface p-5">
          <h4 className="font-mono text-xs uppercase tracking-wider text-danger">
            Hardest-to-convince audience
          </h4>
          <p className="text-sm leading-relaxed text-text-body">
            {SAMPLE.hardest_to_convince}
          </p>
          <p className="text-[11px] uppercase tracking-wider text-text-muted">
            Simulation roles in this audience
          </p>
          <ul className="space-y-1.5 text-sm">
            {SAMPLE.hardest_roles.map((r) => (
              <li
                key={r.display}
                className="flex items-center justify-between rounded-md border border-border bg-surface-elevated px-3 py-2"
              >
                <span className="text-text-muted">{r.display}</span>
                <span className="font-mono text-danger">
                  {r.count}
                  <span className="ml-1 text-text-muted">/ {r.total}</span>
                </span>
              </li>
            ))}
          </ul>
        </article>
      </section>

      {/* Top objections + proof needs */}
      <section className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <article className="space-y-3 rounded-md border border-border bg-surface p-5">
          <h4 className="font-mono text-xs uppercase tracking-wider text-text-muted">
            Top objections
          </h4>
          <ul className="space-y-2 text-sm leading-relaxed text-text-body">
            {SAMPLE.top_objections.map((o) => (
              <li
                key={o}
                className="rounded-md border border-border bg-surface-elevated px-3 py-2"
              >
                {o}
              </li>
            ))}
          </ul>
        </article>
        <article className="space-y-3 rounded-md border border-border bg-surface p-5">
          <h4 className="font-mono text-xs uppercase tracking-wider text-text-muted">
            Proof needs
          </h4>
          <ul className="space-y-2 text-sm leading-relaxed text-text-body">
            {SAMPLE.top_proof_needs.map((p) => (
              <li
                key={p}
                className="rounded-md border border-border bg-surface-elevated px-3 py-2"
              >
                {p}
              </li>
            ))}
          </ul>
        </article>
      </section>

      {/* Stance strictness note */}
      <section className="rounded-md border border-border bg-surface p-5 text-sm leading-relaxed text-text-body">
        <p className="mb-2 font-mono text-xs uppercase tracking-wider text-text-muted">
          Stance calibration
        </p>
        <p>{SAMPLE.receptive_strictness_summary}</p>
      </section>

      {/* Evidence flavor */}
      <section className="rounded-md border border-border bg-surface p-5 text-sm leading-relaxed text-text-body">
        <p className="mb-2 font-mono text-xs uppercase tracking-wider text-text-muted">
          Evidence base
        </p>
        <p>{SAMPLE.evidence_flavor}</p>
      </section>

      {/* Full Debate & Conversations — mirrors the new meta report
          section added in full_debate_section.py. */}
      <section
        className="space-y-6 rounded-md border border-accent-border/50 bg-surface p-6"
        data-testid="sample-full-debate"
      >
        <header className="space-y-2">
          <p className="text-xs uppercase tracking-wider text-accent">
            Full debate &amp; conversations
          </p>
          <h2 className="text-xl tracking-tight text-text-primary">
            Inside the synthetic society
          </h2>
          <p className="text-sm leading-relaxed text-text-muted">
            Every downloaded report now includes the complete debate
            transcript — discussion-session metadata, the four
            influence rounds with bucket transitions, cross-cohort
            argument propagation, and representative cohort reasoning
            with the persona&rsquo;s own words. Same shape as the
            meta-report file.
          </p>
        </header>

        {/* Discussion session */}
        <article className="rounded-md border border-border bg-surface-elevated p-4">
          <h3 className="mb-3 font-mono text-xs uppercase tracking-wider text-text-muted">
            1. Discussion session
          </h3>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
            {[
              ["Personas", SAMPLE.full_debate.discussion_session.persona_count],
              ["Groups", SAMPLE.full_debate.discussion_session.group_count],
              ["Public turns", SAMPLE.full_debate.discussion_session.public_turn_count],
              ["Peer responses", SAMPLE.full_debate.discussion_session.peer_response_turn_count],
              ["Pre-ballots", SAMPLE.full_debate.discussion_session.pre_ballot_count],
              ["Reflections", SAMPLE.full_debate.discussion_session.reflection_count],
              ["Final ballots", SAMPLE.full_debate.discussion_session.final_ballot_count],
              ["Memory atoms", SAMPLE.full_debate.discussion_session.memory_atom_count],
            ].map(([k, v]) => (
              <div key={String(k)} className="flex flex-col">
                <dt className="text-[11px] uppercase tracking-wider text-text-muted">
                  {k}
                </dt>
                <dd className="font-mono text-base text-text-primary">{v}</dd>
              </div>
            ))}
          </dl>
        </article>

        {/* Four influence rounds */}
        <article className="space-y-3 rounded-md border border-border bg-surface-elevated p-4">
          <h3 className="mb-1 font-mono text-xs uppercase tracking-wider text-text-muted">
            2. Influence rounds ({SAMPLE.full_debate.influence_rounds.length})
          </h3>
          <p className="text-xs text-text-muted">
            Voter intent &amp; bucket movement across the four
            propagation stages (init &middot; receive &middot; update
            &middot; finalize).
          </p>
          <ul className="space-y-2 text-sm">
            {SAMPLE.full_debate.influence_rounds.map((r) => (
              <li
                key={r.round_idx}
                className="rounded-md border border-border bg-surface p-3"
              >
                <div className="flex flex-wrap items-baseline gap-3">
                  <span className="font-mono text-xs uppercase tracking-wider text-accent">
                    Round {r.round_idx} · {r.round_type}
                  </span>
                  <span className="font-mono text-[11px] text-text-muted">
                    intent_changes: {r.intent_changes} · bucket_changes:{" "}
                    {r.bucket_changes}
                  </span>
                </div>
                <div className="mt-2 flex flex-wrap gap-4 font-mono text-xs text-text-muted">
                  <span>buyer: {r.bucket_distribution.buyer}</span>
                  <span>receptive: {r.bucket_distribution.receptive}</span>
                  <span>uncertain: {r.bucket_distribution.uncertain}</span>
                  <span>skeptical: {r.bucket_distribution.skeptical}</span>
                </div>
                <p className="mt-2 text-sm leading-relaxed text-text-body">
                  {r.notes}
                </p>
              </li>
            ))}
          </ul>
        </article>

        {/* Society-wide debate */}
        <article className="rounded-md border border-border bg-surface-elevated p-4">
          <h3 className="mb-3 font-mono text-xs uppercase tracking-wider text-text-muted">
            3. Society-wide debate (cross-cohort)
          </h3>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <p className="text-[11px] uppercase tracking-wider text-text-muted">
                Argument count
              </p>
              <p className="font-mono text-base text-text-primary">
                {SAMPLE.full_debate.society_wide_debate.argument_count}
              </p>
              <p className="mt-2 text-[11px] uppercase tracking-wider text-text-muted">
                Argument types
              </p>
              <ul className="mt-1 space-y-0.5 font-mono text-xs text-text-muted">
                {Object.entries(
                  SAMPLE.full_debate.society_wide_debate.argument_type_distribution
                ).map(([k, v]) => (
                  <li key={k}>
                    {k}: {v}
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-wider text-text-muted">
                Propagation count
              </p>
              <p className="font-mono text-base text-text-primary">
                {SAMPLE.full_debate.society_wide_debate.propagation_count}
              </p>
              <p className="mt-2 text-[11px] uppercase tracking-wider text-text-muted">
                Response types
              </p>
              <ul className="mt-1 space-y-0.5 font-mono text-xs text-text-muted">
                {Object.entries(
                  SAMPLE.full_debate.society_wide_debate.response_type_distribution
                ).map(([k, v]) => (
                  <li key={k}>
                    {k}: {v}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </article>

        {/* Full per-turn transcript — 4 groups × 4 rounds × 96 turns */}
        <article className="space-y-3 rounded-md border border-border bg-surface-elevated p-4">
          <h3 className="mb-1 font-mono text-xs uppercase tracking-wider text-text-muted">
            4. Full debate transcript ({TRANSCRIPT.group_count} groups × 4 rounds ×{" "}
            {TRANSCRIPT.groups.reduce(
              (sum, g) => sum + g.rounds.reduce((s, r) => s + r.turn_count, 0),
              0
            )}{" "}
            turns)
          </h3>
          <p className="text-xs text-text-muted">
            The actual public turns each persona spoke in their group,
            organized by group and by round. Same content as the
            downloaded meta report — every persona&rsquo;s voice for
            every round, verbatim.
          </p>
          <div className="space-y-4">
            {TRANSCRIPT.groups.map((g) => (
              <div
                key={g.group_index}
                className="space-y-3 rounded-md border border-border bg-surface p-3"
              >
                <header>
                  <h4 className="font-mono text-sm text-accent">
                    Group {g.group_index} ({g.personas.length} personas)
                  </h4>
                  <p className="text-xs text-text-muted">
                    Members:{" "}
                    {g.personas
                      .map((p) => p.display_name || p.persona_id.slice(0, 8))
                      .join(", ")}
                  </p>
                </header>
                {g.rounds.map((r) => (
                  <details
                    key={r.round_number}
                    className="rounded-md border border-border bg-surface-elevated"
                    open={r.round_number === 1}
                  >
                    <summary className="cursor-pointer px-3 py-2 text-sm">
                      <span className="font-mono text-xs uppercase tracking-wider text-accent">
                        Round {r.round_number}
                      </span>
                      <span className="ml-2 text-text-body">
                        {ROUND_LABEL[r.round_label] || r.round_label}
                      </span>
                      <span className="ml-2 text-xs text-text-muted">
                        ({r.turn_count} turns)
                      </span>
                    </summary>
                    <ol className="space-y-3 px-3 pb-3">
                      {r.turns.map((t) => (
                        <li
                          key={t.turn_id}
                          className="space-y-1 border-l-2 border-accent-border/40 pl-3"
                        >
                          <div className="flex flex-wrap items-baseline gap-2">
                            <span className="font-mono text-xs text-accent">
                              {t.speaker_name || "Unknown"}
                            </span>
                            <span className="font-mono text-[11px] text-text-muted">
                              {t.stance}
                            </span>
                          </div>
                          <p className="text-sm leading-relaxed text-text-body">
                            {t.public_text}
                          </p>
                        </li>
                      ))}
                    </ol>
                  </details>
                ))}
              </div>
            ))}
          </div>
        </article>

        {/* Representative cohort reasoning */}
        <article className="space-y-3 rounded-md border border-border bg-surface-elevated p-4">
          <h3 className="mb-1 font-mono text-xs uppercase tracking-wider text-text-muted">
            5. Representative cohort reasoning (
            {SAMPLE.full_debate.representative_samples.length})
          </h3>
          <p className="text-xs text-text-muted">
            One representative persona per cohort, with the actual
            objection text, the proof artifact that would unblock
            them, and a private-reasoning excerpt in their own words.
          </p>
          <ul className="space-y-3">
            {SAMPLE.full_debate.representative_samples.map((s) => (
              <li
                key={s.cohort}
                className="space-y-2 rounded-md border border-border bg-surface p-3 text-sm"
              >
                <div className="flex flex-wrap items-baseline gap-3">
                  <span className="font-mono text-xs uppercase tracking-wider text-accent">
                    {s.cohort}
                  </span>
                  <span className="font-mono text-[11px] text-text-muted">
                    stance: {s.stance}
                  </span>
                </div>
                <div>
                  <p className="text-[11px] uppercase tracking-wider text-text-muted">
                    Top objection
                  </p>
                  <p className="leading-relaxed text-text-body">{s.objection}</p>
                </div>
                <div>
                  <p className="text-[11px] uppercase tracking-wider text-text-muted">
                    Top proof need
                  </p>
                  <p className="leading-relaxed text-text-body">{s.proof_need}</p>
                </div>
                <div>
                  <p className="text-[11px] uppercase tracking-wider text-text-muted">
                    Private reasoning excerpt
                  </p>
                  <blockquote className="border-l-2 border-accent-border/60 pl-3 italic leading-relaxed text-text-body">
                    {s.excerpt}
                  </blockquote>
                </div>
              </li>
            ))}
          </ul>
        </article>
      </section>

      {/* Trust */}
      <CaveatBanner />

      {/* CTA back home */}
      <section className="flex flex-wrap items-center justify-between gap-4 rounded-md border border-border bg-surface p-5">
        <p className="text-sm text-text-body">
          This is a pre-generated sample. Run your own brief to see a
          live synthetic society react to your product.
        </p>
        <Link
          href="/"
          className="inline-flex items-center justify-center rounded-md bg-accent px-5 py-3 text-sm font-semibold text-background transition-shadow hover:shadow-accent-glow"
        >
          Run your own product
        </Link>
      </section>
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "accent" | "muted" | "danger";
}) {
  const cls =
    tone === "accent"
      ? "text-accent"
      : tone === "danger"
        ? "text-danger"
        : "text-text-muted";
  return (
    <div className="flex flex-col">
      <span className={`font-mono text-2xl ${cls}`}>{value}</span>
      <span className="text-xs uppercase tracking-wider text-text-muted">
        {label}
      </span>
    </div>
  );
}
