// Phase 10B.5+ — landing-page Live Simulation Preview.
//
// A static, marketing-style mock showing what Assembly produces for
// every topic. NOT a real running simulation — clearly labeled
// "preview", uses a canned TikTok-ban topic with fictional agents.
// The center column auto-scrolls so the visual feels alive on the
// landing page; the right column shows the God's Eye View snapshot.

const AGENTS = [
  { id: "S", name: "Sarah", color: "accent" as const, x: 18, y: 30 },
  { id: "M", name: "Marcus", color: "danger" as const, x: 38, y: 22 },
  { id: "E", name: "Elena", color: "accent" as const, x: 58, y: 32 },
  { id: "J", name: "James", color: "muted" as const, x: 22, y: 62 },
  { id: "P", name: "Priya", color: "accent" as const, x: 46, y: 64 },
  { id: "T", name: "Tom", color: "accent" as const, x: 70, y: 58 },
];

const EDGES: [string, string][] = [
  ["S", "M"],
  ["M", "E"],
  ["E", "T"],
  ["P", "T"],
  ["P", "E"],
  ["J", "P"],
  ["S", "J"],
];

type StanceTag = "for" | "against" | "neutral";
const STATEMENTS: { agent: string; stance: StanceTag; text: string }[] = [
  {
    agent: "S",
    stance: "for",
    text:
      "At $79 this slots between an Apple Watch reminder and an Apollo Neuro band — for me, the screenless angle is the whole point.",
  },
  {
    agent: "M",
    stance: "against",
    text:
      "Wearing something that pings my wrist all day sounds like another notification source dressed up as wellness.",
  },
  {
    agent: "E",
    stance: "for",
    text:
      "I work from home and my breathing gets ragged during back-to-back calls. A passive nudge is exactly what I'd try.",
  },
  {
    agent: "J",
    stance: "neutral",
    text:
      "I get the use case, but $79 + a possible subscription is real money. I'd want to know how often the nudges actually fire.",
  },
  {
    agent: "P",
    stance: "for",
    text:
      "Coming from a Muse S household, I'd buy this as the always-on counterpart to my sit-down sessions.",
  },
  {
    agent: "T",
    stance: "for",
    text:
      "I shifted after Priya's point — paired with a meditation app, not against one, this stops feeling redundant.",
  },
];

const STANCE_STYLES: Record<StanceTag, { border: string; tagBg: string; tagText: string; label: string }> = {
  for: {
    border: "border-l-accent",
    tagBg: "bg-accent-soft",
    tagText: "text-accent",
    label: "FOR",
  },
  against: {
    border: "border-l-danger",
    tagBg: "bg-danger/15",
    tagText: "text-danger",
    label: "AGAINST",
  },
  neutral: {
    border: "border-l-text-muted",
    tagBg: "bg-surface-elevated",
    tagText: "text-text-muted",
    label: "NEUTRAL",
  },
};

function agentById(id: string) {
  return AGENTS.find((a) => a.id === id) ?? AGENTS[0];
}

export function LiveSimulationPreview() {
  // Two copies of the statement list so the CSS auto-scroll loops
  // seamlessly without a visible reset jump.
  const looped = [...STATEMENTS, ...STATEMENTS];

  return (
    <section
      data-testid="live-simulation-preview"
      className="space-y-4"
      aria-label="Live simulation preview"
    >
      <p className="flex items-center gap-2 font-mono text-xs uppercase tracking-[0.2em] text-text-muted">
        <span
          aria-hidden
          className="live-dot inline-block h-1.5 w-1.5 rounded-full bg-accent shadow-[0_0_8px_rgba(170,255,0,0.6)]"
        />
        LIVE SIMULATION PREVIEW
      </p>

      <article className="overflow-hidden rounded-xl border border-border bg-surface/80 shadow-[0_0_40px_rgba(0,0,0,0.5)] backdrop-blur-sm">
        {/* Topic bar */}
        <header className="flex items-center justify-between gap-3 border-b border-border bg-surface-elevated/50 px-4 py-2">
          <p className="truncate font-mono text-xs text-text-body sm:text-sm">
            Will urban commuters pay $79 for a screenless wellness wearable?
          </p>
          <span className="flex shrink-0 items-center gap-1.5 rounded border border-accent-border bg-accent-soft px-2 py-0.5 text-[10px] font-mono uppercase tracking-wider text-accent">
            <span
              aria-hidden
              className="live-dot inline-block h-1.5 w-1.5 rounded-full bg-accent"
            />
            LIVE
          </span>
        </header>

        <div className="grid grid-cols-1 md:grid-cols-[1fr_1.4fr_1fr]">
          {/* LEFT — agent network graph */}
          <div className="relative h-[260px] border-b border-border p-4 md:border-b-0 md:border-r">
            <svg
              viewBox="0 0 100 100"
              preserveAspectRatio="none"
              className="absolute inset-4 h-[calc(100%-2rem)] w-[calc(100%-2rem)]"
              aria-hidden
            >
              {EDGES.map(([from, to], i) => {
                const a = agentById(from);
                const b = agentById(to);
                return (
                  <line
                    key={`${from}-${to}-${i}`}
                    x1={a.x}
                    y1={a.y}
                    x2={b.x}
                    y2={b.y}
                    stroke="rgba(170,255,0,0.25)"
                    strokeWidth="0.3"
                    strokeDasharray="0.8 0.8"
                  />
                );
              })}
            </svg>
            <div className="relative h-full">
              {AGENTS.map((a) => {
                const tone =
                  a.color === "accent"
                    ? "border-accent bg-accent-soft text-accent"
                    : a.color === "danger"
                      ? "border-danger bg-danger/15 text-danger"
                      : "border-border bg-surface-elevated text-text-body";
                return (
                  <div
                    key={a.id}
                    className="absolute -translate-x-1/2 -translate-y-1/2 text-center"
                    style={{ left: `${a.x}%`, top: `${a.y}%` }}
                  >
                    <div
                      className={`flex h-7 w-7 items-center justify-center rounded-full border font-mono text-[10px] ${tone}`}
                    >
                      {a.id}
                    </div>
                    <p className="mt-0.5 font-mono text-[9px] text-text-muted">
                      {a.name}
                    </p>
                  </div>
                );
              })}
            </div>
            <p className="absolute bottom-2 left-4 font-mono text-[10px] text-text-muted">
              4 for · 1 against · 1 neutral
            </p>
          </div>

          {/* MIDDLE — auto-scrolling agent statements */}
          <div className="relative h-[260px] overflow-hidden border-b border-border md:border-b-0 md:border-r">
            <p className="sticky top-0 z-10 bg-surface/95 px-5 py-2 font-mono text-[10px] uppercase tracking-wider text-text-muted">
              Round 3
            </p>
            <div className="scroll-up space-y-3 px-5 pb-5">
              {looped.map((s, i) => {
                const a = agentById(s.agent);
                const ss = STANCE_STYLES[s.stance];
                return (
                  <div
                    key={`${s.agent}-${i}`}
                    className={`rounded-md border border-border border-l-2 bg-surface-elevated/50 p-3 ${ss.border}`}
                  >
                    <div className="mb-1.5 flex items-center gap-2">
                      <span
                        className={`flex h-5 w-5 items-center justify-center rounded-full border text-[10px] font-mono ${
                          a.color === "accent"
                            ? "border-accent bg-accent-soft text-accent"
                            : a.color === "danger"
                              ? "border-danger bg-danger/15 text-danger"
                              : "border-border bg-surface text-text-body"
                        }`}
                      >
                        {a.id}
                      </span>
                      <span className="font-mono text-xs text-text-primary">
                        {a.name}
                      </span>
                      <span
                        className={`ml-1 rounded border border-current/30 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider ${ss.tagBg} ${ss.tagText}`}
                      >
                        {ss.label}
                      </span>
                    </div>
                    <p className="text-xs leading-relaxed text-text-body">
                      {s.text}
                    </p>
                  </div>
                );
              })}
            </div>
            {/* Bottom fade so scroll feels infinite */}
            <div
              aria-hidden
              className="pointer-events-none absolute inset-x-0 bottom-0 h-12 bg-gradient-to-t from-surface to-transparent"
            />
            <div
              aria-hidden
              className="pointer-events-none absolute inset-x-0 top-7 h-8 bg-gradient-to-b from-surface to-transparent"
            />
          </div>

          {/* RIGHT — Meta Report snapshot (compact) */}
          <div className="flex h-[260px] flex-col justify-between p-4">
            <div className="space-y-1">
              <p className="font-mono text-[10px] uppercase tracking-wider text-text-muted">
                Meta Report
              </p>
              <p className="font-mono text-4xl leading-none text-accent">
                2
              </p>
              <p className="font-mono text-[10px] uppercase tracking-wider text-text-muted">
                agents shifted
              </p>
              <div
                className="mt-2 h-1 w-full overflow-hidden rounded-sm bg-border"
                aria-hidden
              >
                <div className="h-full w-1/3 bg-accent" />
              </div>
            </div>
            <p className="text-xs leading-snug text-text-body">
              Society leans receptive on the screenless angle but
              splits on the $79 / Apple Watch comparison.
            </p>
            <p className="font-mono text-[10px] text-text-muted">
              Full breakdown below &rarr;
            </p>
          </div>
        </div>
      </article>

      <p className="text-center font-mono text-[11px] text-text-muted">
        ↑ This is what Assembly produces for every topic you run
      </p>
    </section>
  );
}
