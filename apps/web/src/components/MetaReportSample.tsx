// Phase 10B.8 — landing-page "Market Reaction Report" deliverable sample.
//
// Three-pane view of a real abridged run: agent relationship graph
// on the left, round-by-round transcript in the center, distribution
// + outcome stats on the right. Static marketing visual — not a
// live run.

const PANE_AGENTS = [
  // Two concentric rings of 12 agent codes each (24 total). Each
  // entry has a polar position (rDeg, layer 0=outer, 1=inner).
  // Stance: "for" | "against" | "neutral" | "shifted"
  // Layout coords are pre-computed below in `agentPositions`.
  { id: "ER", name: "Ellis", stance: "neutral" as const },
  { id: "WB", name: "Winslow", stance: "neutral" as const },
  { id: "WS", name: "Winslow", stance: "against" as const },
  { id: "PD", name: "Phoenix", stance: "neutral" as const },
  { id: "PH", name: "Phoenix", stance: "neutral" as const },
  { id: "QE", name: "Quinn", stance: "neutral" as const },
  { id: "CD", name: "Casey", stance: "neutral" as const },
  { id: "XF", name: "Xael", stance: "neutral" as const },
  { id: "JF", name: "Jordan", stance: "neutral" as const },
  { id: "YJ", name: "Yarrow", stance: "neutral" as const },
  { id: "MA", name: "Marlowe", stance: "neutral" as const },
  { id: "MT", name: "Maya", stance: "neutral" as const },
  { id: "SS", name: "Sage", stance: "neutral" as const },
  { id: "PF", name: "Phoenix", stance: "neutral" as const },
  { id: "PG", name: "Parker", stance: "neutral" as const },
  { id: "JE", name: "Jordan", stance: "neutral" as const },
  { id: "MG", name: "Marlowe", stance: "for" as const },
  { id: "LC", name: "Lennon", stance: "neutral" as const },
  { id: "AM", name: "Avery", stance: "for" as const },
  { id: "UH", name: "Uma", stance: "neutral" as const },
  { id: "SL", name: "Sage", stance: "neutral" as const },
  { id: "PL", name: "Parker", stance: "neutral" as const },
  { id: "KS", name: "Kai", stance: "neutral" as const },
  { id: "MF", name: "Marlowe", stance: "for" as const },
];

// Pre-computed grid layout — same packing as the screenshot so it
// reads as "particle flow" without needing a physics engine.
const POSITIONS: { x: number; y: number }[] = [
  { x: 12, y: 14 },
  { x: 28, y: 14 },
  { x: 44, y: 14 },
  { x: 64, y: 14 },
  { x: 80, y: 14 },
  { x: 50, y: 22 },
  { x: 30, y: 30 },
  { x: 50, y: 30 },
  { x: 70, y: 30 },
  { x: 12, y: 38 },
  { x: 22, y: 48 },
  { x: 56, y: 38 },
  { x: 80, y: 42 },
  { x: 38, y: 50 },
  { x: 48, y: 56 },
  { x: 62, y: 50 },
  { x: 80, y: 56 },
  { x: 14, y: 60 },
  { x: 28, y: 64 },
  { x: 28, y: 76 },
  { x: 46, y: 76 },
  { x: 62, y: 72 },
  { x: 80, y: 72 },
  { x: 12, y: 78 },
];

const EDGES: [number, number][] = [
  [0, 1], [1, 2], [2, 3], [3, 4], [2, 5], [5, 7],
  [6, 7], [7, 8], [6, 9], [9, 17], [10, 17], [10, 18],
  [11, 7], [11, 13], [13, 14], [14, 15], [15, 8], [15, 11],
  [17, 18], [18, 19], [18, 23], [19, 20], [20, 14], [20, 21],
  [21, 22], [22, 8], [22, 16], [16, 8], [4, 16],
];

const SHIFTED_EDGES = new Set([
  "18-19", "13-14", "14-15", "10-17", "11-13", "20-14",
]);

type Stance = "for" | "against" | "neutral";

const STANCE_DOT: Record<Stance, string> = {
  for: "border-accent bg-accent-soft text-accent",
  against: "border-danger bg-danger/15 text-danger",
  neutral: "border-border bg-surface-elevated text-text-body",
};

type StanceTag = "RECEPTIVE" | "UNCERTAIN" | "RESISTANT";

const TAG_STYLES: Record<StanceTag, { bg: string; text: string; dot: string }> = {
  RECEPTIVE: {
    bg: "bg-accent-soft border-accent-border",
    text: "text-accent",
    dot: "bg-accent",
  },
  UNCERTAIN: {
    bg: "bg-surface-elevated border-border",
    text: "text-text-muted",
    dot: "bg-text-muted",
  },
  RESISTANT: {
    bg: "bg-danger/15 border-danger/40",
    text: "text-danger",
    dot: "bg-danger",
  },
};

const TURNS: Array<{
  speaker: string;
  initials: string;
  stance: StanceTag;
  role: string;
  body: string;
  shift?: number;
}> = [
  {
    speaker: "Avery M.",
    initials: "AM",
    stance: "UNCERTAIN",
    role: "Muse S user",
    body:
      "Honestly, my Muse S already gives me solid feedback during sit-down sessions, so the appeal of a screenless wearable for me would be the passive, in-the-moment angle — something that nudges me mid-meeting without me having to strap on a headband or open an app. That said, a wristband that vibrates at me through the day is a lot to sit with, even if it doesn't record audio. At $79 it's not crazy compared to what I paid for the Muse, but I'd need a clearer sense of how it detects stress in the first place before I'd seriously consider it.",
  },
  {
    speaker: "Lennon C.",
    initials: "LC",
    stance: "UNCERTAIN",
    role: "Use case focused buyer",
    body:
      "Honestly, as someone who's been around clinical settings, I get the appeal of something passive that doesn't make me pull out my phone or stare at a watch mid-shift. But a buzzing wristband I'd wear all day gives me pause — I read that it doesn't record audio, fine, but what is the sensor actually doing then? At $79 I'm not opposed, I just want to understand what it's sensing before I'd strap it on during a meeting or a patient interaction.",
    shift: -0.13,
  },
  {
    speaker: "Phoenix F.",
    initials: "PF",
    stance: "UNCERTAIN",
    role: "Trust seeker",
    body:
      "Honestly, a wristband marketed as 'wellness' gives me pause right out of the gate. I get that it doesn't record audio, but I'd want a really clear story on what signal is actually being read and where any of that data lives. I currently lean on the Apple Watch breathe nudges and an occasional Headspace session, and those feel low-stakes because I know what they are. At $79, I'd need to see real evidence it does something my watch can't before I'd consider switching.",
  },
  {
    speaker: "Sage L.",
    initials: "SL",
    stance: "RESISTANT",
    role: "Apollo Neuro user",
    body:
      "Since the brief says the wearable captures body-signal patterns during the day, I'd want to know exactly what makes this better than my Apollo Neuro band, which already does a screenless vibration cycle for stress. The $79 price point is fine in isolation, but the comparison story isn't there yet — I'd want to see a side-by-side against Apollo before I'd switch.",
    shift: -0.38,
  },
];

export function MetaReportSample() {
  return (
    <section
      data-testid="meta-report-sample"
      className="space-y-8"
      aria-label="Market Reaction Report deliverable sample"
    >
      <header className="space-y-4 text-center">
        <p className="flex items-center justify-center gap-2 font-mono text-xs uppercase tracking-[0.2em] text-text-muted">
          <span
            aria-hidden
            className="inline-block h-1.5 w-1.5 rotate-45 bg-accent shadow-[0_0_8px_rgba(170,255,0,0.6)]"
          />
          THE DELIVERABLE
        </p>
        <h2 className="text-4xl font-bold tracking-tight text-text-primary sm:text-5xl">
          MARKET REACTION REPORT
        </h2>
        <p className="mx-auto max-w-2xl text-sm leading-relaxed text-text-muted">
          After every simulation, Assembly produces a Market Reaction
          Report — who shifted, what argument was decisive, and where
          consensus is actually headed. Below is a real run, abridged.
        </p>
      </header>

      <article className="overflow-hidden rounded-xl border border-border bg-surface/80 shadow-[0_0_40px_rgba(0,0,0,0.5)]">
        {/* Topic strip */}
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-surface-elevated/40 px-5 py-3">
          <p className="font-mono text-sm text-text-body">
            Will urban commuters pay $79 for a screenless wellness wearable?
          </p>
          <span className="flex items-center gap-2 rounded border border-border bg-surface px-2.5 py-1 text-[10px] font-mono uppercase tracking-wider text-text-muted">
            Sample run
          </span>
        </header>

        <div className="grid grid-cols-1 lg:grid-cols-[1.05fr_1.6fr_0.85fr]">
          {/* LEFT — AGENT RELATIONSHIP GRAPH */}
          <div className="border-b border-border p-5 lg:border-b-0 lg:border-r">
            <div className="mb-3 flex items-start justify-between">
              <div>
                <p className="font-mono text-[10px] uppercase tracking-wider text-text-muted">
                  AGENT RELATIONSHIP
                </p>
                <p className="font-mono text-[10px] uppercase tracking-wider text-text-muted">
                  GRAPH
                </p>
                <p className="mt-1 font-mono text-[10px] text-text-muted">
                  24 agents · live particle flow
                </p>
              </div>
              <div className="flex flex-col items-end gap-1 font-mono text-[9px] uppercase tracking-wider text-text-muted">
                <span className="flex items-center gap-1.5">
                  <span className="inline-block h-1.5 w-1.5 rounded-full bg-accent" />{" "}
                  FOR
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="inline-block h-1.5 w-1.5 rounded-full bg-danger" />{" "}
                  AGAINST
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="inline-block h-1.5 w-1.5 rounded-full bg-text-muted" />{" "}
                  NEUTRAL
                </span>
                <span className="flex items-center gap-1.5 pt-1">
                  <span
                    aria-hidden
                    className="inline-block h-px w-3 bg-accent"
                  />{" "}
                  SHIFTED
                </span>
              </div>
            </div>

            <div className="relative h-[420px] w-full">
              <svg
                viewBox="0 0 100 100"
                preserveAspectRatio="none"
                className="absolute inset-0 h-full w-full"
                aria-hidden
              >
                {EDGES.map(([i, j], idx) => {
                  const a = POSITIONS[i];
                  const b = POSITIONS[j];
                  const isShifted = SHIFTED_EDGES.has(`${i}-${j}`);
                  return (
                    <line
                      key={`${i}-${j}-${idx}`}
                      x1={a.x}
                      y1={a.y}
                      x2={b.x}
                      y2={b.y}
                      stroke={
                        isShifted
                          ? "rgba(170,255,0,0.55)"
                          : "rgba(170,255,0,0.18)"
                      }
                      strokeWidth={isShifted ? 0.35 : 0.2}
                      strokeDasharray={isShifted ? "" : "0.8 0.8"}
                    />
                  );
                })}
              </svg>
              {PANE_AGENTS.map((a, i) => {
                const p = POSITIONS[i];
                return (
                  <div
                    key={a.id}
                    className="absolute -translate-x-1/2 -translate-y-1/2 text-center"
                    style={{ left: `${p.x}%`, top: `${p.y}%` }}
                  >
                    <div
                      className={`flex h-7 w-7 items-center justify-center rounded-full border font-mono text-[10px] ${STANCE_DOT[a.stance]}`}
                    >
                      {a.id}
                    </div>
                    <p className="mt-0.5 font-mono text-[8px] text-text-muted">
                      {a.name}
                    </p>
                  </div>
                );
              })}
            </div>

            <details className="mt-3 rounded-md border border-border bg-surface-elevated/40 px-3 py-2 text-xs text-text-muted">
              <summary className="cursor-pointer font-mono text-[10px] uppercase tracking-wider text-text-muted">
                Graph guide
              </summary>
              <p className="mt-2 font-mono text-[10px] leading-relaxed">
                Each node is one synthetic agent. Solid edges are
                ballots that moved during discussion; dotted edges
                are stable relationships.
              </p>
            </details>
          </div>

          {/* CENTER — TRANSCRIPT */}
          <div className="border-b border-border p-5 lg:border-b-0 lg:border-r">
            <h3 className="text-lg font-semibold text-text-primary">
              What the synthetic agents said
            </h3>
            <p className="mt-1 text-xs leading-relaxed text-text-muted">
              Round-by-round transcript across 4 groups. Stance pills
              collapse each agent&apos;s position to Receptive /
              Uncertain / Resistant; ▲▼ shows shift from their
              pre-discussion stance.
            </p>

            {/* Group tabs */}
            <div className="mt-4 flex flex-wrap gap-2">
              {[1, 2, 3, 4].map((g) => (
                <span
                  key={g}
                  className={`rounded border px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider ${
                    g === 4
                      ? "border-accent-border bg-accent-soft text-accent"
                      : "border-border bg-surface text-text-muted"
                  }`}
                >
                  Group {g}
                  <span className="ml-2 text-text-muted">6 agents</span>
                </span>
              ))}
            </div>

            <details className="mt-4 rounded-md border border-border bg-surface-elevated/40 px-3 py-2 text-xs text-text-body">
              <summary className="cursor-pointer text-xs text-text-body">
                Personas in this group (6) — click to expand private
                ballots
              </summary>
              <p className="mt-2 text-xs text-text-muted">
                Trust seeker, Use-case focused buyer, Apollo Neuro user,
                Muse S user, Performance-focused buyer, Convenience-focused buyer.
              </p>
            </details>

            {/* Round tabs */}
            <div className="mt-4 flex flex-wrap gap-2">
              {[1, 2, 3, 4].map((r) => (
                <span
                  key={r}
                  className={`rounded border px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider ${
                    r === 1
                      ? "border-accent-border bg-accent-soft text-accent"
                      : "border-border bg-surface text-text-muted"
                  }`}
                >
                  Round {r}
                  <span className="ml-1.5 text-text-muted">· 6</span>
                </span>
              ))}
            </div>

            <div className="mt-4 space-y-2">
              <p className="font-mono text-xs text-text-body">
                <span
                  aria-hidden
                  className="mr-2 inline-block h-1.5 w-1.5 rounded-full bg-accent"
                />
                Round 1 · Public opening
                <span className="ml-3 text-text-muted">· 6 turns</span>
              </p>
              {/* Distribution bar */}
              <div className="h-1.5 w-full overflow-hidden rounded-sm bg-border">
                <div className="flex h-full">
                  <span className="h-full w-1/6 bg-accent" />
                  <span className="h-full w-4/6 bg-text-muted" />
                  <span className="h-full w-1/6 bg-danger" />
                </div>
              </div>
              <p className="font-mono text-[10px] text-text-muted">
                <span className="text-accent">Receptive 1</span> ·{" "}
                <span className="text-text-body">Uncertain 4</span> ·{" "}
                <span className="text-danger">Resistant 1</span>
              </p>
            </div>

            {/* Turn cards */}
            <div className="mt-4 space-y-3">
              {TURNS.map((t) => {
                const tag = TAG_STYLES[t.stance];
                return (
                  <article
                    key={t.speaker}
                    className="rounded-md border border-border bg-surface-elevated/30 p-3"
                  >
                    <header className="mb-2 flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <span
                          aria-hidden
                          className={`inline-block h-1.5 w-1.5 rounded-full ${tag.dot}`}
                        />
                        <span className="font-mono text-sm text-text-primary">
                          {t.speaker}
                        </span>
                        <span
                          className={`rounded border px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider ${tag.bg} ${tag.text}`}
                        >
                          {t.stance}
                        </span>
                        {t.shift ? (
                          <span className="font-mono text-[10px] text-danger">
                            ▼ {Math.abs(t.shift).toFixed(2)}
                          </span>
                        ) : null}
                      </div>
                      <span className="font-mono text-[10px] text-text-muted">
                        {t.role}
                      </span>
                    </header>
                    <p className="text-xs leading-relaxed text-text-body">
                      {t.body}
                    </p>
                  </article>
                );
              })}
            </div>
          </div>

          {/* RIGHT — DISTRIBUTION + OUTCOME */}
          <div className="space-y-6 p-5">
            {/* LIVE DISTRIBUTION */}
            <section>
              <p className="font-mono text-[10px] uppercase tracking-wider text-text-muted">
                Live distribution · <span className="text-accent">Round 4</span>
              </p>
              <ul className="mt-3 space-y-2 text-xs">
                <DistRow label="Receptive" count={6} pct={25} tone="for" />
                <DistRow label="Uncertain" count={11} pct={46} tone="neutral" />
                <DistRow label="Resistant" count={7} pct={29} tone="against" />
              </ul>
            </section>

            {/* FINAL BALLOT */}
            <section>
              <p className="font-mono text-[10px] uppercase tracking-wider text-text-muted">
                Final ballot
              </p>
              <ul className="mt-3 space-y-2 text-xs">
                <DistRow label="Receptive" count={3} pct={12} tone="for" />
                <DistRow label="Uncertain" count={20} pct={83} tone="neutral" />
                <DistRow label="Resistant" count={1} pct={4} tone="against" />
              </ul>
              <details className="mt-3 rounded-md border border-border bg-surface-elevated/30 px-3 py-2">
                <summary className="cursor-pointer font-mono text-[10px] uppercase tracking-wider text-text-muted">
                  ALL ROUNDS
                </summary>
                <p className="mt-2 text-[11px] leading-relaxed text-text-muted">
                  Round 1 → Round 4 shows the society migrating
                  from a 4-1-1 receptive opening toward a heavy
                  uncertain middle as the price-vs-mechanism
                  question hardened.
                </p>
              </details>
            </section>

            <hr className="border-border" />

            {/* OUTCOME STATS */}
            <section className="space-y-4">
              <p className="font-mono text-[10px] uppercase tracking-wider text-text-muted">
                Outcome stats
              </p>
              <div>
                <p className="font-mono text-5xl leading-none text-accent">
                  9
                </p>
                <p className="mt-1 font-mono text-[11px] uppercase tracking-wider text-text-muted">
                  agents shifted
                </p>
              </div>
              <div>
                <p className="font-mono text-5xl leading-none text-text-primary">
                  15
                </p>
                <p className="mt-1 font-mono text-[11px] uppercase tracking-wider text-text-muted">
                  agents held
                </p>
              </div>
              <div
                className="h-1 w-full overflow-hidden rounded-sm bg-border"
                aria-hidden
              >
                <div className="h-full w-[38%] bg-accent" />
              </div>
              <p className="font-mono text-[11px] text-text-muted">
                <span className="text-text-body">38%</span> opinion shift rate
              </p>
            </section>

            <p className="border-t border-border pt-3 font-mono text-[10px] text-text-muted">
              Every simulation produces this Market Reaction Report automatically.
            </p>
          </div>
        </div>
      </article>
    </section>
  );
}

function DistRow({
  label,
  count,
  pct,
  tone,
}: {
  label: string;
  count: number;
  pct: number;
  tone: "for" | "neutral" | "against";
}) {
  const fill =
    tone === "for"
      ? "bg-accent"
      : tone === "against"
        ? "bg-danger"
        : "bg-text-muted";
  const text =
    tone === "for"
      ? "text-accent"
      : tone === "against"
        ? "text-danger"
        : "text-text-body";
  return (
    <li className="flex items-center gap-3 text-xs">
      <span className={`min-w-[5.5rem] font-mono ${text}`}>{label}</span>
      <span
        className="h-1.5 flex-1 overflow-hidden rounded-sm bg-border"
        aria-hidden
      >
        <span
          className={`block h-full ${fill}`}
          style={{ width: `${pct}%` }}
        />
      </span>
      <span className="min-w-[1.5rem] text-right font-mono text-text-body">
        {count}
      </span>
    </li>
  );
}
