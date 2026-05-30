"use client";
// Phase 10B+ — live agent relationship cluster.
//
// Renders the synthetic society as a tight force-directed cluster.
// Every persona is a node colored by its final-ballot stance bucket
// (FOR / AGAINST / NEUTRAL) and surrounded by a soft dashed halo.
// Every (within-group) pair is wired with a thin accent-tinted edge
// — the network is dense by design so the founder can see the
// society as a whole, not a ring of disconnected agents.
//
// Continuous particle flow rides those edges. The particles are
// sequenced from the actual round-by-round transcript when an
// explicit peer-reference exists; otherwise we sample random pairs
// so the cluster stays alive at all times. Multiple particles are
// in-flight simultaneously, mimicking a live conversation.

import { useEffect, useMemo, useRef, useState } from "react";
import { stripPersonaSystemCaveats } from "@/lib/caveatFilter";
import { humanizeRole, humanizeStance } from "@/lib/labels";
import { bucketStance } from "@/lib/stance";
import type {
  DiscussionTranscriptPayload,
  PrivateBallotView,
  TranscriptPersona,
  TranscriptTurn,
} from "@/lib/types";

type Bucket = "for" | "against" | "neutral";

interface Node {
  persona: TranscriptPersona;
  bucket: Bucket;
  // Force-directed simulation state
  x: number;
  y: number;
  vx: number;
  vy: number;
  // Cosmetic phase offset so halos pulse out of phase per node
  phase: number;
}

interface Edge {
  from: string;
  to: string;
  /** True when the two endpoints land in different stance buckets;
   *  shifted edges render brighter and emit more particles. */
  shifted: boolean;
  /** Per-edge multiplier on the spring's ideal length. Adding a
   *  random factor here breaks the radial symmetry that turns
   *  complete subgraphs into perfect circles. */
  lengthFactor: number;
}

interface Particle {
  edgeKey: string; // "from|to" — used to lookup endpoint nodes
  reverse: boolean; // travel direction
  bornAt: number; // ms timestamp when emitted
  durationMs: number;
  size: number;
  /** 1 = bright (peer-response or shifted edge), 0.3 = ambient */
  brightness: number;
}

interface SimulationState {
  nodes: Node[];
  edges: Edge[];
  edgeIndex: Edge[]; // same as edges, kept for fast random sampling
  nodeById: Map<string, Node>;
  particles: Particle[];
  eventCursor: number;
  nextEventAt: number;
  lastFrame: number;
  lastAmbientAt: number;
  pulseUntilByPid: Map<string, number>;
}

const COLOR = {
  for: "#AAFF00",
  against: "#FF5C5C",
  neutral: "#9aa0a6",
} satisfies Record<Bucket, string>;

const NODE_R = 18; // node radius
const HALO_R = 27; // halo ring radius
const CANVAS_DPR =
  typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;

export interface AgentGraphProps {
  transcript: DiscussionTranscriptPayload;
  /** Optional fixed dimensions. If omitted the canvas auto-sizes to
   *  fill its container (height clamped to a sensible range). */
  width?: number;
  height?: number;
  /** The debate-agent count the founder requested (preferred_society_size).
   *  Optional — absent on default runs. When present AND different from the
   *  actual rendered count, an honest mismatch explainer is shown. */
  requestedAgentCount?: number;
}

export function AgentGraph({
  transcript,
  width: propWidth,
  height: propHeight,
  requestedAgentCount,
}: AgentGraphProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [size, setSize] = useState<{ width: number; height: number }>({
    width: propWidth ?? 520,
    height: propHeight ?? 520,
  });

  // Auto-size to container width whenever propWidth not specified.
  useEffect(() => {
    if (propWidth && propHeight) {
      setSize({ width: propWidth, height: propHeight });
      return;
    }
    const el = containerRef.current;
    if (!el) return;
    const measure = () => {
      const rect = el.getBoundingClientRect();
      const w = Math.max(280, Math.floor(rect.width));
      const h = Math.min(720, Math.max(420, Math.floor(w * 0.95)));
      setSize({ width: w, height: h });
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [propWidth, propHeight]);

  const { width, height } = size;
  const { nodes, edges, turnEvents, withinGroupEdgeCount } = useMemo(
    () => buildGraph(transcript, width, height),
    [transcript, width, height],
  );

  // Persistent simulation state
  const stateRef = useRef<SimulationState>({
    nodes: [],
    edges: [],
    edgeIndex: [],
    nodeById: new Map(),
    particles: [],
    eventCursor: 0,
    nextEventAt: 0,
    lastFrame: 0,
    lastAmbientAt: 0,
    pulseUntilByPid: new Map(),
  });

  // Re-seed when transcript / size changes — and run a settling pass
  // so the cluster is already spread out on first paint.
  useEffect(() => {
    const s = stateRef.current;
    s.nodes = nodes.map((n) => ({ ...n }));
    s.edges = edges;
    s.edgeIndex = edges;
    s.nodeById = new Map(s.nodes.map((n) => [n.persona.persona_id, n]));
    s.particles = [];
    s.eventCursor = 0;
    s.nextEventAt = performance.now() + 250;
    s.lastFrame = 0;
    s.lastAmbientAt = 0;
    s.pulseUntilByPid = new Map();
    // ~600 settling iterations so the spring + Coulomb forces
    // converge to a spacious organic layout. With dt=16ms each
    // step this is "fast-forward" — at runtime forces are much
    // gentler so the layout stays put.
    for (let i = 0; i < 600; i++) {
      stepForces(s.nodes, s.edges, width, height, 16, /* settling */ true);
    }
  }, [nodes, edges, width, height]);

  // Animation loop
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let ctx: CanvasRenderingContext2D | null = null;
    try {
      ctx = canvas.getContext("2d");
    } catch {
      return;
    }
    if (!ctx) return;
    const ctx2d = ctx;
    canvas.width = width * CANVAS_DPR;
    canvas.height = height * CANVAS_DPR;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    ctx2d.scale(CANVAS_DPR, CANVAS_DPR);

    let raf = 0;
    // Pace turn-event playback. Faster than before so the cluster
    // visibly hums; we loop indefinitely so the graph always feels
    // active.
    const TURN_INTERVAL_MS = 350;

    function step(now: number) {
      const s = stateRef.current;
      const dt = Math.min(48, s.lastFrame ? now - s.lastFrame : 16);
      s.lastFrame = now;

      // Mild live forces — keeps nodes from drifting indefinitely.
      stepForces(s.nodes, s.edges, width, height, dt, false);

      // 1. Replay the transcript as a busy stream of turn events.
      while (
        s.eventCursor < turnEvents.length &&
        now >= s.nextEventAt
      ) {
        emitTurnEvent(turnEvents[s.eventCursor], s);
        s.eventCursor += 1;
        s.nextEventAt = now + TURN_INTERVAL_MS;
      }
      // Loop the playback so the graph stays alive after the last
      // event has fired.
      if (
        s.eventCursor >= turnEvents.length &&
        turnEvents.length > 0
      ) {
        s.eventCursor = 0;
        s.nextEventAt = now + 600;
      }

      // 2. Continuous ambient flow — multiple particles per tick on
      //    random edges, regardless of explicit turn events. This
      //    keeps the cluster looking alive between turn-driven
      //    bursts and across many edges simultaneously.
      maybeEmitAmbient(s, now);

      // 3. Age out expired particles.
      s.particles = s.particles.filter(
        (p) => now - p.bornAt < p.durationMs,
      );

      paint(ctx2d, s, now, width, height, hoverId);
      raf = requestAnimationFrame(step);
    }

    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [width, height, turnEvents, hoverId]);

  function onMouseMove(e: React.MouseEvent<HTMLCanvasElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const s = stateRef.current;
    let nearest: Node | null = null;
    let bestD = (NODE_R + 4) * (NODE_R + 4);
    for (const n of s.nodes) {
      const dx = n.x - x;
      const dy = n.y - y;
      const d = dx * dx + dy * dy;
      if (d < bestD) {
        bestD = d;
        nearest = n;
      }
    }
    setHoverId(nearest ? nearest.persona.persona_id : null);
  }

  return (
    <section
      data-testid="agent-graph"
      className="space-y-3 rounded-md border border-border bg-surface p-4"
    >
      <header className="flex items-baseline justify-between gap-4">
        <div>
          <h3 className="text-sm font-semibold uppercase tracking-wider text-text-muted">
            Deep-agent debate graph
          </h3>
          <p className="text-xs text-text-muted">
            {nodes.length} debate agents · live particle flow
          </p>
        </div>
        <Legend />
      </header>
      <p
        data-testid="deep-agent-graph-note"
        className="text-xs text-text-muted"
      >
        These are the agents who generated the public debate. The 100
        voters are shown separately above as the influence layer.
      </p>
      {/* Phase 14B — honest requested-vs-actual explainer. Only shown
          when the founder set a specific debate-agent count AND it
          differs from the count actually run. We state both real numbers
          and the actual group count WITHOUT attributing the gap to any
          single cause: the deep-agent society is capped/compressed, so we
          must not claim group-splitting produced the difference. */}
      {typeof requestedAgentCount === "number" &&
      nodes.length > 0 &&
      requestedAgentCount !== nodes.length ? (
        <p
          data-testid="deep-agent-count-explainer"
          className="text-xs text-text-muted"
        >
          You asked for {requestedAgentCount} debate agents. This run
          debated with {nodes.length} agents across{" "}
          {transcript.groups.length} discussion group
          {transcript.groups.length === 1 ? "" : "s"}.
        </p>
      ) : null}
      <div
        ref={containerRef}
        className="relative w-full overflow-hidden rounded-md"
      >
        <canvas
          ref={canvasRef}
          onMouseMove={onMouseMove}
          onMouseLeave={() => setHoverId(null)}
          aria-label={`Live agent graph with ${nodes.length} synthetic personas`}
          role="img"
          className="block rounded-md"
          data-testid="agent-graph-canvas"
        />
        <HoverCard
          nodes={nodes}
          hoverId={hoverId}
          ballots={transcript.private_ballots}
          containerWidth={width}
          containerHeight={height}
        />
      </div>
      <GraphGuide />
    </section>
  );
}

function GraphGuide() {
  return (
    <details
      data-testid="graph-guide"
      className="rounded-md border border-border bg-surface-elevated px-3 py-2 text-xs text-text-body"
    >
      <summary className="cursor-pointer select-none font-medium text-text-primary">
        Graph guide
      </summary>
      <p className="mt-2 leading-relaxed">
        Each node is a synthetic persona. Color shows the
        persona&apos;s final stance —{" "}
        <span className="text-accent">green</span> for receptive,{" "}
        <span className="text-text-muted">gray</span> for uncertain,
        and <span className="text-danger">red</span> for resistant.
        Lines show discussion influence — argument references and
        within-group connections from the synthetic debate. Brighter
        accent lines mark agents whose stance bucket{" "}
        <em className="not-italic text-accent">shifted</em> through
        the discussion. Particles flowing along the lines replay the
        round-by-round conversation. Hover any node to see who that
        persona is and what they thought.
      </p>
    </details>
  );
}

// -----------------------------------------------------------------------
// Sub-components
// -----------------------------------------------------------------------

function Legend() {
  return (
    <ul className="flex flex-wrap items-center gap-3 text-[10px] uppercase tracking-wider text-text-muted">
      <li className="flex items-center gap-1">
        <span className="inline-block h-2 w-2 rounded-full bg-accent" />
        For
      </li>
      <li className="flex items-center gap-1">
        <span className="inline-block h-2 w-2 rounded-full bg-danger" />
        Against
      </li>
      <li className="flex items-center gap-1">
        <span className="inline-block h-2 w-2 rounded-full bg-text-muted" />
        Neutral
      </li>
      <li className="flex items-center gap-1">
        <span className="inline-block h-px w-3 bg-accent" />
        Shifted
      </li>
    </ul>
  );
}

function HoverCard({
  nodes,
  hoverId,
  ballots,
  containerWidth,
  containerHeight,
}: {
  nodes: Node[];
  hoverId: string | null;
  ballots: Record<
    string,
    { pre?: PrivateBallotView; reflection?: PrivateBallotView; final?: PrivateBallotView }
  >;
  containerWidth: number;
  containerHeight: number;
}) {
  if (!hoverId) return null;
  const n = nodes.find((nd) => nd.persona.persona_id === hoverId);
  if (!n) return null;
  const ballot = ballots[n.persona.persona_id] ?? {};
  const finalBallot = ballot.final ?? ballot.reflection ?? null;
  const preBallot = ballot.pre ?? null;
  // Position below the node. Clamp to canvas bounds so the card
  // never overflows the viewport edges.
  const cardWidth = 280;
  const cardOffsetY = NODE_R + 14;
  let leftPx = n.x - cardWidth / 2;
  if (leftPx < 6) leftPx = 6;
  if (leftPx + cardWidth > containerWidth - 6) {
    leftPx = containerWidth - cardWidth - 6;
  }
  let topPx = n.y + cardOffsetY;
  // If the card would overflow the bottom edge, place above the node
  // instead.
  if (topPx + 160 > containerHeight) {
    topPx = n.y - 160 - 8;
    if (topPx < 6) topPx = 6;
  }
  return (
    <div
      className="pointer-events-none absolute z-10 rounded-md border border-border bg-surface-elevated px-3 py-2.5 text-xs leading-relaxed text-text-body shadow-lg"
      style={{
        left: `${leftPx}px`,
        top: `${topPx}px`,
        width: `${cardWidth}px`,
      }}
      data-testid="agent-graph-hover"
    >
      <header className="space-y-0.5">
        <p className="font-medium text-text-primary">
          {n.persona.display_name}
        </p>
        <p className="text-text-muted">{humanizeRole(n.persona.role)}</p>
      </header>
      <p className="mt-1.5 flex items-center gap-2 uppercase tracking-wider text-[10px]">
        <span style={{ color: COLOR[n.bucket] }}>
          {n.bucket.toUpperCase()}
        </span>
        {finalBallot ? (
          <span className="text-text-muted">
            · final:{" "}
            <span className="text-text-body normal-case tracking-normal">
              {humanizeStance(finalBallot.stance)}
            </span>
          </span>
        ) : null}
      </p>
      {(() => {
        const cleanedFinalReasoning = stripPersonaSystemCaveats(
          finalBallot?.reasoning ?? "",
        );
        const cleanedPreReasoning = stripPersonaSystemCaveats(
          preBallot?.reasoning ?? "",
        );
        const cleanedObjection = stripPersonaSystemCaveats(
          finalBallot?.top_objection ?? "",
        );
        const cleanedProof = stripPersonaSystemCaveats(
          finalBallot?.top_proof_need ?? "",
        );
        return (
          <>
            {cleanedFinalReasoning ? (
              <p className="mt-2 text-text-body">
                &ldquo;{truncate(cleanedFinalReasoning, 220)}&rdquo;
              </p>
            ) : cleanedPreReasoning ? (
              <p className="mt-2 text-text-body">
                &ldquo;{truncate(cleanedPreReasoning, 220)}&rdquo;
              </p>
            ) : null}
            {cleanedObjection ? (
              <p className="mt-1.5 text-text-muted">
                <span>objection:</span>{" "}
                <span className="text-text-body">
                  {truncate(cleanedObjection, 90)}
                </span>
              </p>
            ) : null}
            {cleanedProof ? (
              <p className="text-text-muted">
                <span>proof needed:</span>{" "}
                <span className="text-text-body">
                  {truncate(cleanedProof, 90)}
                </span>
              </p>
            ) : null}
          </>
        );
      })()}
    </div>
  );
}

function truncate(s: string, max: number): string {
  if (!s) return "";
  if (s.length <= max) return s;
  return s.slice(0, max - 1).trimEnd() + "…";
}

// -----------------------------------------------------------------------
// Build graph from transcript
// -----------------------------------------------------------------------

interface TurnEvent {
  speaker_id: string;
  target_ids: string[];
  bright: boolean;
}

function buildGraph(
  transcript: DiscussionTranscriptPayload,
  width: number,
  height: number,
): {
  nodes: Node[];
  edges: Edge[];
  turnEvents: TurnEvent[];
  withinGroupEdgeCount: number;
} {
  // 1. Collect personas (deduped, preserving group order).
  const personas: TranscriptPersona[] = [];
  const seen = new Set<string>();
  const groupIndexByPid = new Map<string, number>();
  for (const g of transcript.groups) {
    for (const p of g.personas) {
      if (!seen.has(p.persona_id)) {
        seen.add(p.persona_id);
        personas.push(p);
        groupIndexByPid.set(p.persona_id, g.group_index);
      }
    }
  }

  // 2. Final stance per persona for coloring.
  const finalStanceByPid: Record<string, string | null> = {};
  for (const [pid, ballots] of Object.entries(transcript.private_ballots)) {
    finalStanceByPid[pid] =
      ballots.final?.stance ??
      ballots.reflection?.stance ??
      ballots.pre?.stance ??
      null;
  }

  // 3. Seed positions — random scatter around the canvas center so
  //    the settling pass has no symmetry hint to lock onto. Group
  //    membership no longer determines the seed location, which
  //    avoids the "four ring" lock-in and produces an organic blob
  //    once the forces settle.
  const cx = width / 2;
  const cy = height / 2;
  const baseR = Math.min(width, height) * 0.32;
  const nodes: Node[] = personas.map((persona, i) => {
    const angle = Math.random() * 2 * Math.PI;
    const r = baseR * (0.4 + Math.random() * 0.6);
    return {
      persona,
      bucket: bucketStance(finalStanceByPid[persona.persona_id]),
      x: cx + r * Math.cos(angle),
      y: cy + r * Math.sin(angle),
      vx: 0,
      vy: 0,
      phase: i * 0.37,
    };
  });

  // 4. Edges — designed to produce an irregular organic layout
  //    instead of four perfect circles. Three sources:
  //
  //    a) Explicit peer-references from the transcript (deduped,
  //       undirected). Real conversation backbone.
  //    b) Within-group sparse backbone — instead of the full
  //       complete graph (which minimizes energy at a regular
  //       polygon), we use a chain through the group plus 1 random
  //       shortcut per persona. Each group becomes an irregular
  //       cluster shape, not a ring.
  //    c) Cross-group "rumor" edges — for every persona we add 1
  //       random connection to a persona in a different group with
  //       50% probability. These pull the four groups together
  //       into one connected mass with an organic outline.
  //
  //    Every edge gets a random length factor (0.7..1.4) so the
  //    spring forces don't lock to symmetric configurations.
  const turnToSpeaker: Record<string, string> = {};
  for (const g of transcript.groups) {
    for (const r of g.rounds) {
      for (const t of r.turns) {
        turnToSpeaker[t.turn_id] = t.speaker_persona_id;
      }
    }
  }
  const edgeMap = new Map<string, Edge>();
  function jitter(): number {
    return 0.7 + Math.random() * 0.7;
  }
  function addEdge(a: string, b: string, shifted: boolean) {
    if (a === b) return;
    const k = a < b ? `${a}|${b}` : `${b}|${a}`;
    const existing = edgeMap.get(k);
    if (!existing) {
      edgeMap.set(k, {
        from: a,
        to: b,
        shifted,
        lengthFactor: jitter(),
      });
    } else if (shifted && !existing.shifted) {
      existing.shifted = true;
    }
  }
  // a) explicit peer references
  for (const g of transcript.groups) {
    for (const r of g.rounds) {
      for (const t of r.turns) {
        for (const refId of t.referenced_turn_ids) {
          const target = turnToSpeaker[refId];
          if (!target) continue;
          const fromBucket = bucketStance(
            finalStanceByPid[t.speaker_persona_id],
          );
          const toBucket = bucketStance(finalStanceByPid[target]);
          addEdge(t.speaker_persona_id, target, fromBucket !== toBucket);
        }
      }
    }
  }
  // b) within-group sparse backbone: chain + one random shortcut
  //    per persona
  let withinGroupEdges = 0;
  for (const g of transcript.groups) {
    const ids = g.personas.map((p) => p.persona_id);
    if (ids.length < 2) continue;
    // chain (n-1 edges)
    for (let i = 0; i < ids.length - 1; i++) {
      const before = edgeMap.size;
      addEdge(ids[i], ids[i + 1], false);
      if (edgeMap.size > before) withinGroupEdges += 1;
    }
    // 1 random shortcut per persona
    for (let i = 0; i < ids.length; i++) {
      let attempts = 0;
      while (attempts < 4) {
        const j = Math.floor(Math.random() * ids.length);
        if (j !== i && Math.abs(j - i) > 1) {
          const before = edgeMap.size;
          addEdge(ids[i], ids[j], false);
          if (edgeMap.size > before) {
            withinGroupEdges += 1;
            break;
          }
        }
        attempts += 1;
      }
    }
  }
  // c) cross-group rumor edges — pull the four clusters together
  //    into one connected mass with an irregular outline
  const groupsList = transcript.groups;
  if (groupsList.length > 1) {
    const allPersonaIds = personas.map((p) => p.persona_id);
    for (const persona of personas) {
      if (Math.random() > 0.55) continue; // ~45% of personas get one cross-group hop
      const myGroup = groupIndexByPid.get(persona.persona_id);
      // Pick a random persona NOT in the same group
      let attempts = 0;
      while (attempts < 8) {
        const candidate =
          allPersonaIds[Math.floor(Math.random() * allPersonaIds.length)];
        if (
          candidate !== persona.persona_id &&
          groupIndexByPid.get(candidate) !== myGroup
        ) {
          addEdge(persona.persona_id, candidate, false);
          break;
        }
        attempts += 1;
      }
    }
  }
  const edges = [...edgeMap.values()];

  // 5. Build the playback timeline — one TurnEvent per public turn
  //    in (group, round, turn_number) order.
  const turnEvents: TurnEvent[] = [];
  for (const g of transcript.groups) {
    const peerIds = g.personas.map((p) => p.persona_id);
    const ordered = [...g.rounds].sort(
      (a, b) => a.round_number - b.round_number,
    );
    for (const r of ordered) {
      const turnsSorted = [...r.turns].sort(
        (a, b) => a.turn_number - b.turn_number,
      );
      for (const t of turnsSorted) {
        turnEvents.push(buildTurnEvent(t, peerIds, turnToSpeaker));
      }
    }
  }
  return {
    nodes,
    edges,
    turnEvents,
    withinGroupEdgeCount: withinGroupEdges,
  };
}

function buildTurnEvent(
  turn: TranscriptTurn,
  groupPeerIds: string[],
  turnToSpeaker: Record<string, string>,
): TurnEvent {
  const explicit = turn.referenced_turn_ids
    .map((rid) => turnToSpeaker[rid])
    .filter((pid): pid is string => !!pid && pid !== turn.speaker_persona_id);
  if (explicit.length > 0) {
    return {
      speaker_id: turn.speaker_persona_id,
      target_ids: explicit.slice(0, 3),
      bright: true,
    };
  }
  // No explicit reference — fan two ambient particles to random group peers
  return {
    speaker_id: turn.speaker_persona_id,
    target_ids: pickPeers(turn.speaker_persona_id, groupPeerIds, 2),
    bright: false,
  };
}

function pickPeers(
  selfId: string,
  peers: string[],
  k: number,
): string[] {
  const pool = peers.filter((p) => p !== selfId);
  if (!pool.length) return [];
  const out: string[] = [];
  for (let i = 0; i < k; i++) {
    out.push(pool[Math.floor(Math.random() * pool.length)]);
  }
  return out;
}

// -----------------------------------------------------------------------
// Force step — clustered packing, with optional aggressive settling
// -----------------------------------------------------------------------

function stepForces(
  nodes: Node[],
  edges: Edge[],
  width: number,
  height: number,
  dt: number,
  settling: boolean,
) {
  const cx = width / 2;
  const cy = height / 2;
  const k = dt / 16; // normalize forces to ~60fps frames
  // Standard force-directed layout: very weak centering, every pair
  // of nodes repels every other (Coulomb-style), and connected
  // pairs are pulled toward an ideal edge length. This produces a
  // spacious organic layout that fills the canvas instead of
  // clumping in the middle.
  const center = settling ? 0.0035 : 0.0012;
  const spring = settling ? 0.05 : 0.012;
  const damping = settling ? 0.78 : 0.86;
  const idealLen = 110;
  // Coulomb constant — strong enough that nodes never overlap their
  // halos and clusters spread to fill the canvas.
  const coulomb = settling ? 5200 : 2400;
  const minPairDist = HALO_R * 1.6; // ~43 — softens 1/r² near 0

  // Centering force toward viewport mid (very weak — just keeps the
  // cluster from drifting off-screen entirely)
  for (const n of nodes) {
    n.vx += (cx - n.x) * center * k;
    n.vy += (cy - n.y) * center * k;
  }

  // Edge spring — pulls every connected pair toward its own ideal
  // length. Per-edge `lengthFactor` (random 0.7..1.4) breaks the
  // radial symmetry that would otherwise turn each subgraph into a
  // regular polygon.
  for (const e of edges) {
    const a = nodes.find((n) => n.persona.persona_id === e.from);
    const b = nodes.find((n) => n.persona.persona_id === e.to);
    if (!a || !b) continue;
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const dist = Math.sqrt(dx * dx + dy * dy) || 1;
    const target = idealLen * e.lengthFactor;
    const f = spring * k * (dist - target);
    const fx = (dx / dist) * f;
    const fy = (dy / dist) * f;
    a.vx += fx;
    a.vy += fy;
    b.vx -= fx;
    b.vy -= fy;
  }

  // Full pairwise Coulomb repulsion — every pair pushes apart with
  // force ∝ 1/r². This is what lets the layout fill the available
  // space instead of clumping at the center.
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i];
      const b = nodes[j];
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const d2 = dx * dx + dy * dy;
      if (d2 < 1) continue;
      const dist = Math.sqrt(d2);
      // Soften repulsion for very-close pairs to avoid explosive
      // overlap correction
      const safeDist = Math.max(minPairDist, dist);
      const f = (coulomb / (safeDist * safeDist)) * k;
      a.vx -= (dx / dist) * f;
      a.vy -= (dy / dist) * f;
      b.vx += (dx / dist) * f;
      b.vy += (dy / dist) * f;
    }
  }

  // Integrate + damping + viewport clamp
  for (const n of nodes) {
    // Cap velocity so settling iterations don't fling nodes off-canvas
    const vmag = Math.sqrt(n.vx * n.vx + n.vy * n.vy);
    const vmax = settling ? 18 : 6;
    if (vmag > vmax) {
      n.vx = (n.vx / vmag) * vmax;
      n.vy = (n.vy / vmag) * vmax;
    }
    n.vx *= damping;
    n.vy *= damping;
    n.x += n.vx;
    n.y += n.vy;
    const m = HALO_R + 6;
    if (n.x < m) {
      n.x = m;
      n.vx = 0;
    }
    if (n.y < m) {
      n.y = m;
      n.vy = 0;
    }
    if (n.x > width - m) {
      n.x = width - m;
      n.vx = 0;
    }
    if (n.y > height - m) {
      n.y = height - m;
      n.vy = 0;
    }
  }
}

// -----------------------------------------------------------------------
// Particle emission
// -----------------------------------------------------------------------

function emitTurnEvent(ev: TurnEvent, s: SimulationState) {
  const now = performance.now();
  s.pulseUntilByPid.set(ev.speaker_id, now + 900);
  for (const target of ev.target_ids) {
    if (!s.nodeById.has(ev.speaker_id) || !s.nodeById.has(target)) continue;
    const a = ev.speaker_id;
    const b = target;
    const k = a < b ? `${a}|${b}` : `${b}|${a}`;
    s.particles.push({
      edgeKey: k,
      reverse: a >= b,
      bornAt: now,
      durationMs: ev.bright ? 1100 + Math.random() * 400 : 1500,
      size: ev.bright ? 2.6 : 2,
      brightness: ev.bright ? 1 : 0.55,
    });
  }
}

function maybeEmitAmbient(s: SimulationState, now: number) {
  // Several ambient particles per tick. Targets ~12-20 simultaneous
  // particles in flight on a typical 24-agent society.
  if (now - s.lastAmbientAt < 95) return;
  s.lastAmbientAt = now;
  const edgeCount = s.edgeIndex.length;
  if (!edgeCount) return;
  const burst = 3;
  for (let i = 0; i < burst; i++) {
    const e = s.edgeIndex[Math.floor(Math.random() * edgeCount)];
    const k = e.from < e.to ? `${e.from}|${e.to}` : `${e.to}|${e.from}`;
    s.particles.push({
      edgeKey: k,
      reverse: Math.random() < 0.5,
      bornAt: now,
      durationMs: 1400 + Math.random() * 1200,
      size: 1.6 + Math.random() * 1.1,
      brightness: e.shifted ? 0.7 : 0.4,
    });
  }
}

// -----------------------------------------------------------------------
// Painter
// -----------------------------------------------------------------------

function paint(
  ctx: CanvasRenderingContext2D,
  s: SimulationState,
  now: number,
  width: number,
  height: number,
  hoverId: string | null,
) {
  ctx.clearRect(0, 0, width, height);

  // Edges — every edge solid + accent-tinted; shifted edges brighter.
  for (const e of s.edges) {
    const a = s.nodeById.get(e.from);
    const b = s.nodeById.get(e.to);
    if (!a || !b) continue;
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    if (e.shifted) {
      ctx.strokeStyle = "rgba(170, 255, 0, 0.55)";
      ctx.lineWidth = 1.3;
    } else {
      ctx.strokeStyle = "rgba(170, 255, 0, 0.18)";
      ctx.lineWidth = 0.7;
    }
    ctx.stroke();
  }

  // Particles
  for (const p of s.particles) {
    const [from, to] = p.edgeKey.split("|");
    const aNode = s.nodeById.get(p.reverse ? to : from);
    const bNode = s.nodeById.get(p.reverse ? from : to);
    if (!aNode || !bNode) continue;
    const t = (now - p.bornAt) / p.durationMs;
    if (t < 0 || t > 1) continue;
    const eased = 1 - Math.pow(1 - t, 3);
    const x = aNode.x + (bNode.x - aNode.x) * eased;
    const y = aNode.y + (bNode.y - aNode.y) * eased;
    const fadeIn = Math.min(1, t * 4);
    const fadeOut = Math.min(1, (1 - t) * 4);
    const alpha = fadeIn * fadeOut * p.brightness;
    if (p.brightness > 0.65) {
      ctx.fillStyle = `rgba(170, 255, 0, ${alpha})`;
      ctx.beginPath();
      ctx.arc(x, y, p.size, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = `rgba(170, 255, 0, ${alpha * 0.18})`;
      ctx.beginPath();
      ctx.arc(x, y, p.size + 5, 0, Math.PI * 2);
      ctx.fill();
    } else {
      ctx.fillStyle = `rgba(204, 204, 204, ${alpha})`;
      ctx.beginPath();
      ctx.arc(x, y, p.size, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  // Halos + nodes
  for (const n of s.nodes) {
    const fillColor = COLOR[n.bucket];
    const isHover = hoverId === n.persona.persona_id;
    const pulseUntil = s.pulseUntilByPid.get(n.persona.persona_id) ?? 0;
    const pulseFraction =
      now < pulseUntil ? (pulseUntil - now) / 900 : 0;
    const breathe =
      0.5 + 0.5 * Math.sin(now * 0.0018 + n.phase);

    // Outer dashed halo — thin accent ring around every node
    ctx.beginPath();
    ctx.arc(n.x, n.y, HALO_R, 0, Math.PI * 2);
    ctx.setLineDash([3, 4]);
    ctx.lineWidth = 1;
    const haloAlpha =
      0.18 + 0.12 * breathe + 0.4 * pulseFraction + (isHover ? 0.3 : 0);
    ctx.strokeStyle =
      n.bucket === "neutral"
        ? `rgba(170, 255, 0, ${haloAlpha * 0.45})`
        : `rgba(170, 255, 0, ${haloAlpha})`;
    ctx.stroke();
    ctx.setLineDash([]);

    // Soft inner glow
    const grad = ctx.createRadialGradient(
      n.x,
      n.y,
      NODE_R * 0.2,
      n.x,
      n.y,
      NODE_R * 1.6,
    );
    grad.addColorStop(0, `${fillColor}55`);
    grad.addColorStop(1, `${fillColor}00`);
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(n.x, n.y, NODE_R * 1.6, 0, Math.PI * 2);
    ctx.fill();

    // Body
    ctx.beginPath();
    ctx.arc(n.x, n.y, NODE_R, 0, Math.PI * 2);
    ctx.fillStyle = fillColor;
    ctx.globalAlpha = n.bucket === "neutral" ? 0.85 : 1;
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.lineWidth = 2;
    ctx.strokeStyle = "#0A0A0A";
    ctx.stroke();

    // Initials
    const initials = (n.persona.display_name || "?")
      .split(/\s+/)
      .map((part) => part[0])
      .join("")
      .slice(0, 2)
      .toUpperCase();
    ctx.fillStyle = "#0A0A0A";
    ctx.font = "700 12px Inter, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(initials, n.x, n.y + 0.5);

    // First-name caption below halo
    ctx.fillStyle = "#8A8A8A";
    ctx.font = "10px Inter, system-ui, sans-serif";
    ctx.fillText(
      n.persona.display_name.split(" ")[0].slice(0, 11),
      n.x,
      n.y + HALO_R + 12,
    );
  }
}
