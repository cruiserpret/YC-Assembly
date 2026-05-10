# Phase 10B.5 — Operator Report

YC demo polish + public-facing cleanup. Frontend-only changes.
The Assembly visual identity is preserved (#0A0A0A bg, #141414
surfaces, #AAFF00 accent, dark synthetic-society lab feel) — every
change is incremental polish, not a redesign.

J-checklist verification: **24 / 24 acceptance tests PASS** plus
all 64 prior frontend tests still green.

---

## 1. Files changed

```
apps/web/
├── src/lib/debug.ts                     (added PUBLIC_MODE flag)
├── src/lib/labels.ts                    (added humanizeLabel +
│                                         switching-status entries)
├── src/components/BriefForm.tsx         (public-mode gating +
│                                         structured pricing fields +
│                                         field helpers + bigger CTA)
├── src/components/IntentSnapshot.tsx    (stance-vs-intent explainer
│                                         + humanized switching-status)
├── src/components/AudienceFitCards.tsx  (role labels demoted to
│                                         supporting detail)
├── src/components/CaveatBanner.tsx      ("How to read this report"
│                                         framing + softened copy)
├── src/components/ReportActions.tsx     (NEW — Copy link / Download /
│                                         Run another product)
├── src/components/ReportDashboard.tsx   (mount ReportActions; bumped
│                                         section spacing space-y-8)
├── src/app/page.tsx                     (View sample report CTA)
├── src/app/sample-report/page.tsx       (NEW — pre-generated PantryPulse
│                                         sample, clearly labeled)
└── tests/unit/phase_10b.test.tsx        (+24 Phase 10B.5 acceptance
                                          tests; updated 4 prior tests
                                          for new placeholders +
                                          public-mode default)
```

No backend code changed. The structured pricing fields fold back
into the existing `price_or_price_structure` flat field at submit
time, so the backend schema (Phase 10B.3 fact-lock parser) sees a
clean multi-tier description and parses it correctly.

## 2. Screenshot — landing / form (public mode)

```
SYNTHETIC SOCIETY SIMULATION

Watch a simulated society react to your product.

Assembly builds a fresh synthetic society from real market evidence
for your specific product, lets it react and debate across seven
rounds, then shows who's receptive, who resists, why, and what you
should test next — in the real world.

Evidence is real. Interpretation is simulated. The report observes
— it does not forecast or instruct.

[ View sample report → ]   Pre-generated example, not a live run.

╭─────────────── SUBMIT A PRODUCT BRIEF ────────────────╮
│ Mode: [ Live simulation ]                             │
│                                                       │
│ Product name                                          │
│ ┌────────────────────────────────────────────────┐    │
│ │ e.g. AquaSnap                                  │    │
│ └────────────────────────────────────────────────┘    │
│                                                       │
│ Product description                                   │
│ Describe what it is, who it's for, what it does, …    │  ← helper
│ ┌────────────────────────────────────────────────┐    │
│ │ What it is, who it's for, and what it does…   │    │
│ └────────────────────────────────────────────────┘    │
│ …                                                     │
│ ▼ Optional advanced fields                            │
│   ┌─ Bundle price ─┐  ┌─ Subscription price ─┐        │
│   │ $139 for 2-pack│  │ $7.99/month for Plus │        │
│   └────────────────┘  └─────────────────────┘         │
│   ┌─ Accessory / refill price ─────────────────┐      │
│   │ $19.99 for 12 NFC tags                     │      │
│   └────────────────────────────────────────────┘      │
│ …                                                     │
│ Synthetic society: 24 personas · Estimated time:      │  ← public-mode
│ ~12-20 minutes                                        │     summary
│                                                       │
│ [ Run synthetic society ]                             │
╰───────────────────────────────────────────────────────╯
```

## 3. Public-mode dev-controls hidden

Tests prove every public-mode hide:

- `mode-fixture` testid: **null** in DOM (`Phase 10B.5 #1`)
- `mode-live` testid: **null** in DOM (`Phase 10B.5 #1, #2`)
- "live_founder_brief" string: **not present** in form text
- LLM call count: **not in run-estimate** ("LLM calls" regex fails)
- Raw `~$X.XX` cost: **not in run-estimate** (`~\$\d` regex fails)
- "Live simulation" badge: **visible** at `mode-public-display`
- Society + time copy: `"Synthetic society: 24 personas · Estimated time: ~12-20 minutes"`

Dev users can still toggle into the legacy fixture-demo picker by
setting `NEXT_PUBLIC_ASSEMBLY_PUBLIC_MODE=false` at build time.

## 4. Structured pricing fields

Optional pricing fields live under "Optional advanced fields" so
they don't clutter the primary form. Three new inputs:

| Test ID | Label |
|---|---|
| `bundle-price-input` | Bundle price (optional) — "$139 for 2-pack" |
| `subscription-price-input` | Subscription price (optional) — "$7.99/month for Plus" |
| `accessory-price-input` | Accessory / refill price (optional) — "$19.99 for 12 NFC tags" |

`buildPriceText()` concatenates them into a single line:

```
INPUT:
  primary    = $149 starter kit
  bundle     = $269 2-pack
  subscription = $7.99/month
  accessory  = $19.99 for tags

OUTPUT (sent to backend):
  "$149 starter kit. Bundle: $269 2-pack. Optional subscription:
   $7.99/month. Accessory: $19.99 for tags"
```

The Phase 10B.3 fact-lock parser already understands the
`Bundle: …`, `Optional subscription: …`, `Accessory: …` keywords
and splits them into `primary_price` / `bundle_price` /
`accessory_prices` correctly (regression-tested by 28 backend
acceptance tests).

## 5. Sample-report CTA

Landing page now has the secondary CTA:

```
[ View sample report → ]   Pre-generated example, not a live run.
```

Clicking opens `/sample-report`, a self-contained page rendering:

- Sample badge (`Sample report` accent pill) + run label
- Headline: *"The synthetic society finished with limited
  receptive: 4 of 24 personas ended receptive…"* (PantryPulse
  Phase 10B.4 verification rerun)
- Receptive / Uncertain / Resistant counts (4 / 16 / 4)
- Best-fit + hardest-to-convince audience cards
- Top objections + proof needs
- Stance calibration note
- Evidence base
- "How to read this report" trust card
- Big CTA back to `/` to "Run your own product"

Always labeled "Sample report" — never presented as live output.
Static page (175 B JS, 96.2 kB First Load); no API calls.

## 6. Updated audience cards

Role labels demoted to supporting detail:

```
─── Best-fit audience ─────────────────────────────────
Best-fit audience: urban renters, busy parents, college
students who already understand the pain this product
solves, especially people familiar with Samsung Family
Hub-style alternatives but frustrated by their format
or durability.

  SIMULATION ROLES IN THIS AUDIENCE
  ─────────────────────────────────
  Performance-focused buyers              1 / 4
  Samsung Family Hub Refrigerator users   1 / 4
  Trust-seekers                           1 / 1
─────────────────────────────────────────────────────────
```

Role display switched to `text-text-muted` so the natural-language
summary at the top stays the primary read.

## 7. Intent snapshot stance-vs-intent explainer

Added directly under the "Synthetic intent snapshot" header:

```
Synthetic intent snapshot

Stance shows where personas landed after discussion. Intent shows
the next action they expressed inside the simulation. The two can
diverge — a persona may end the discussion receptive but still
need proof before they would buy.

[segmented bar: ████████ ████ █████████ ]
Positive intent · Comparing · Resistant / loyal
```

Plus the switching-status detail panel now humanizes its slugs:

| Before | After |
|---|---|
| `no_current_alternative` | "No current alternative" |
| `actively_comparing` | "Actively comparing options" |
| `weakly_attached_to_alternative` | "Weakly attached to current alternative" |
| `refuses_switching` | "Refuses to switch" |

Test 13 (`Phase 10B.5`) verifies these all render correctly AND
that no raw snake_case slug appears in `intent-snapshot` text.

## 8. Humanized labels — coverage

Same humanization applies everywhere snake_case slugs were
surfacing:

```
loyal_to_current_alternative   →  Loyal to current alternative
refuses_switching              →  Refuses to switch
actively_comparing             →  Actively comparing options
weakly_attached_to_alternative →  Weakly attached to current alternative
would_consider_if_proven       →  Would consider if proven
would_join_waitlist            →  Would join waitlist
would_reject                   →  Would reject
```

Raw slugs remain in technical JSON / DebugPanel views only (gated
by `NEXT_PUBLIC_ASSEMBLY_DEBUG=true`).

## 9. Updated trust / caveat section

Reframed from "Synthetic society — not a market forecast" to
**"How to read this report"**:

```
○ HOW TO READ THIS REPORT

Assembly simulates a run-scoped synthetic society using live
market evidence. It is not a real customer interview or revenue
prediction, but it helps surface likely objections, proof needs,
and audience reactions before launch.

— Synthetic simulation, not a real customer interview
— Not a real-world forecast or revenue prediction
— Run-scoped — not representative of the whole market
— Simulated intent is not actual purchase behavior
— No launch / kill verdict — the report surfaces objections,
  proof needs, and audience reactions
— Evidence-backed, but still needs real-world validation
```

Same visual structure, gentler tone. Still passes the
"no forecast/launch verdict language in UI constants" regression
guardrail (test 10).

## 10. Top headline / report-title behavior

**Unchanged.** Per operator decision in the Phase 10B.5 brief:

> "Do NOT change the top headline in this phase."

The `confident_headline` builder from 10B.3 still generates:

```
"The synthetic society finished {strongly|leaning|split-but|
with-limited} receptive: {N} of {M} personas ended receptive,
with {S} shifting toward stronger interest during discussion."
```

The RECEPTIVE label is preserved. Test 21 in the Phase 10B.5 suite
explicitly asserts that `apps/web/src/lib/stance.ts` still
contains "RECEPTIVE" and never "Conditionally receptive" or
"Receptive if proven".

## 11. Existing progress screen

**Unchanged.** No edits to `RunProgress.tsx`. Test 22 in the
Phase 10B.5 suite reads the file and asserts it's intact (length
> 100 chars, `run-progress-stage` testid still present). The
operator's instruction "Do NOT build a new progress screen. Only
make tiny polish fixes if something is obviously broken." was
honored — nothing in the progress screen was visibly broken so
nothing was touched.

## 12. Test results

| Suite | Tests | Result |
|---|---|---|
| `phase_10b.test.tsx` (incl. 24 Phase 10B.5 + 40 prior) | **88** | ✓ pass |
| All frontend suites | **88** | ✓ pass |
| Full backend pytest | **2086** | ✓ pass |

```
$ npm test -- --run
✓ tests/unit/phase_10b.test.tsx (64 tests) 814ms
✓ Phase 10B.5 — YC demo polish > structured pricing serializes…
Test Files  8 passed (8)
     Tests  88 passed (88)
```

## 13. Build / typecheck

```
$ npx tsc --noEmit
(clean)

$ npm run build
✓ Compiled successfully
✓ Linting and checking validity of types
✓ Generating static pages (6/6)

Route (app)                              Size     First Load JS
┌ ○ /                                    7.13 kB         117 kB
├ ○ /_not-found                          873 B          88.2 kB
├ ○ /new                                 15.9 kB         117 kB
├ ƒ /run/[runId]                         29.3 kB         139 kB
├ ○ /sample-report                       175 B          96.2 kB   ← NEW
├ ƒ /simulations/[id]/report             19.3 kB         129 kB
└ ƒ /simulations/[id]/status             4.36 kB         114 kB
```

The `/sample-report` route prerenders as fully static (175 B
incremental JS) — no API calls, no client-side run lookups.

## 14. Ready for git push + deployment prep?

**Yes — Phase 10B.5 is PASS.**

| PASS criterion | Status |
|---|---|
| site no longer exposes dev/internal controls in public mode | ✓ |
| audience cards human-readable | ✓ |
| intent snapshot understandable (stance-vs-intent explainer) | ✓ |
| snake_case labels gone from public UI | ✓ |
| structured pricing fields exist | ✓ |
| sample report CTA works | ✓ |
| share/download/run-another actions exist | ✓ |
| trust section polished but still present | ✓ |
| current report title/headline behavior preserved | ✓ |
| existing progress screen not broken | ✓ |
| frontend tests/build pass | ✓ 88/88 + clean build |

**Deployment checklist (suggestions, not blockers):**
1. Set `NEXT_PUBLIC_ASSEMBLY_PUBLIC_MODE` explicitly to `"true"`
   in production env (it's the default but explicit beats
   implicit in deploy configs).
2. Verify `NEXT_PUBLIC_ASSEMBLY_API_BASE` points at your prod
   backend (currently defaults to `http://localhost:8000`).
3. Optional: bump backend `cost_hard_usd` from `12.0` → `18.0`
   for demo accounts so live runs don't truncate the final
   ballot stage (Phase 10B.4 noted this).

Phase 10B.5 itself is fully done.
