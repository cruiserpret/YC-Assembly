# Phase 10B — Frontend MVP / Founder Demo UI: Operator Report

**Phase status:** ✅ **PASS**
**Build:** Next.js 14.2 production build — clean (5 routes)
**Tests:** 40/40 passing (16 new 10B + 24 legacy)
**Typecheck:** clean

---

## 1. Frontend stack used

Existing app at [apps/web](apps/web) was reused (Next.js 14 App Router + TypeScript + Tailwind + React Query + Vitest + Testing Library + react-hook-form + zod). No new packages added.

## 2. Pages / components created

| file | role |
|---|---|
| [apps/web/src/styles/tokens.css](apps/web/src/styles/tokens.css) | Locked Phase 10B design tokens (LITERAL hex palette) |
| [apps/web/src/styles/globals.css](apps/web/src/styles/globals.css) | Imports tokens, sets dark `body` defaults |
| [apps/web/tailwind.config.ts](apps/web/tailwind.config.ts) | Maps Tailwind colors to CSS variables; legacy palette preserved as compat aliases for older Phase-7 routes |
| [apps/web/src/lib/types.ts](apps/web/src/lib/types.ts) | API types + closed-set `IntentLabel` |
| [apps/web/src/lib/api.ts](apps/web/src/lib/api.ts) | New `/assembly/runs/*` client; legacy `/simulations/*` kept |
| [apps/web/src/components/CaveatBanner.tsx](apps/web/src/components/CaveatBanner.tsx) | Always-visible synthetic-society caveats |
| [apps/web/src/components/BriefForm.tsx](apps/web/src/components/BriefForm.tsx) | Founder brief form with mode toggle, validation, run-cost estimate |
| [apps/web/src/components/RunProgress.tsx](apps/web/src/components/RunProgress.tsx) | 13-stage progress with polling + glowing accent for active stage + failed-run card |
| [apps/web/src/components/IntentSnapshot.tsx](apps/web/src/components/IntentSnapshot.tsx) | Closed-set intent labels, accent for high-intent rows, mandatory caveat |
| [apps/web/src/components/CohortCards.tsx](apps/web/src/components/CohortCards.tsx) | Synthetic-cohort grid + receptive/resistant lists |
| [apps/web/src/components/PersonaList.tsx](apps/web/src/components/PersonaList.tsx) | Society-level persona stats + 13 quality-gate booleans |
| [apps/web/src/components/DiscussionSummary.tsx](apps/web/src/components/DiscussionSummary.tsx) | Turn/ballot counts, public↔private shift, debate breakdown |
| [apps/web/src/components/ReportDashboard.tsx](apps/web/src/components/ReportDashboard.tsx) | Composes all above + executive summary + objection/proof cards + recommended-tests + download MD |
| [apps/web/src/app/layout.tsx](apps/web/src/app/layout.tsx) | Dark theme shell, accent dot logo, footer caveat |
| [apps/web/src/app/page.tsx](apps/web/src/app/page.tsx) | Landing/hero + brief form |
| [apps/web/src/app/run/[runId]/page.tsx](apps/web/src/app/run/[runId]/page.tsx) | Progress polling → report dashboard switch |
| [apps/web/tests/unit/phase_10b.test.tsx](apps/web/tests/unit/phase_10b.test.tsx) | 16 unit tests covering all 15 spec scenarios + 1 bonus |

## 3. Exact color tokens implemented

From [apps/web/src/styles/tokens.css](apps/web/src/styles/tokens.css):

```css
:root {
  --background: #0A0A0A;          /* page background ~60% */
  --surface: #141414;             /* cards/panels ~30% */
  --surface-elevated: #181818;
  --border: #262626;
  --text-primary: #FFFFFF;
  --text-body: #CCCCCC;           /* primary readable body text */
  --text-muted: #8A8A8A;
  --accent: #AAFF00;              /* CTA/key numbers/active stage ~10% */
  --accent-soft: rgba(170, 255, 0, 0.12);
  --accent-border: rgba(170, 255, 0, 0.35);
  --danger: #FF5C5C;
  --warning: #FFD166;
  --success: #AAFF00;
}
```

## 4. Locked palette confirmation

| color | role | usage |
|---|---|---|
| **#0A0A0A** | page background | `<html>` / `<body>` / page wrappers |
| **#141414** | cards/surfaces | every section panel + form container |
| **#AAFF00** | accent / CTA / key numbers / active progress | submit button, progress badge, intent counts (high-intent only), key-number stats, "Synthetic" trust label, accent dot logo, download-markdown link |
| **#CCCCCC** | body text | all body paragraphs, list items, descriptions |

White (`#FFFFFF`) is reserved for major headings + key persona/cohort labels. Muted gray (`#8A8A8A`) handles secondary metadata. Production CSS minifier collapses to equivalent short forms (`#af0`, `#ccc`, `#0a0a0a`) — semantically identical and rendered as the locked palette in browser.

## 5. API endpoints integrated

All eight endpoints from the 10B spec wired in [apps/web/src/lib/api.ts](apps/web/src/lib/api.ts):

- `POST /assembly/runs` — `createAssemblyRun`
- `GET /assembly/runs/{run_id}` — `getAssemblyRun` (polled by RunProgress every 5s)
- `GET /assembly/runs/{run_id}/report` — `getAssemblyReport`
- `GET /assembly/runs/{run_id}/report.md` — `getAssemblyReportMarkdown` + downloaded directly via the dashboard's "Download markdown report" link
- `GET /assembly/runs/{run_id}/personas` — `getAssemblyPersonas`
- `GET /assembly/runs/{run_id}/cohorts` — `getAssemblyCohorts`
- `GET /assembly/runs/{run_id}/discussion` — `getAssemblyDiscussion`
- `GET /assembly/runs/{run_id}/intent` — `getAssemblyIntent`

Base URL is `process.env.NEXT_PUBLIC_ASSEMBLY_API_BASE` (default `http://localhost:8000`) — already wired in the existing legacy client; reused.

## 6. Default mode

**`live_founder_brief` is the default.** BriefForm uses `defaultMode="live_founder_brief"` (and the live tab is rendered with the active accent style). `fixture_demo` is exposed as a small dev/demo toggle, not the primary path. Test 12 asserts this.

## 7. fixture_demo still works

**Yes.** Mode toggle in BriefForm switches the POST body's `mode` to `fixture_demo` and the same submit path runs. Test 11 verifies the toggle + submit emits `mode: "fixture_demo"` to `createAssemblyRun`.

## 8. Visual proof (server-rendered HTML inspection)

Smoke-tested at `http://localhost:3000` — landing page returned HTTP 200 with the following confirmed in the rendered HTML:

- `<title>Assembly · synthetic-society simulation lab</title>`
- Header logo: accent dot (`bg-accent`) + "Assembly" wordmark
- Hero accent kicker: "Synthetic society simulation" in `text-accent`
- Headline: "Watch a simulated society react to your product." in `text-text-primary`
- Brief form with all required fields + active mode pill (live_founder_brief styled with `border-accent-border bg-accent-soft text-accent`)
- Run estimate strip showing "24 personas · ~168 LLM calls · ~$3.02 · ~17 min wall time"
- Submit button "Run synthetic society" with `bg-accent text-background` and `hover:shadow-accent-glow`
- Caveat banner at bottom listing all six default caveats with the accent trust label

The compiled production CSS contains all four locked palette values (verified in `.next/static/css/3136c5d27ec05c2e.css`):

```
--background: #0a0a0a
--surface: #141414
--accent: #af0           (= #AAFF00, normalized by minifier)
--text-body: #ccc        (= #CCCCCC, normalized by minifier)
--text-primary: #fff
--text-muted: #8a8a8a
```

(I cannot embed PNG screenshots here, but the rendered DOM tree is in the smoke-test transcript above and the production build is reproducible via `npm run dev` / `npm run build`.)

## 9. Test results

```
$ npm test
✓ tests/unit/label-discipline.test.ts      (1 test)
✓ tests/unit/schema.test.ts                (5 tests)
✓ tests/unit/api.test.ts                   (5 tests)
✓ tests/unit/status-timeline.test.tsx      (3 tests)
✓ tests/unit/evidence-anchor-list.test.tsx (3 tests)
✓ tests/unit/debug-gate.test.tsx           (2 tests)
✓ tests/unit/report-shell.test.tsx         (5 tests)
✓ tests/unit/phase_10b.test.tsx            (16 tests)
Test Files  8 passed (8)
Tests       40 passed (40)
```

The 16 new 10B tests cover all 15 operator scenarios from the spec plus a bonus closed-set assertion:

| # | scenario |
|---|---|
| 1 | brief form renders |
| 2 | required fields validate |
| 3 | manual persona forcing fields are not present |
| 4 | POST /assembly/runs is called correctly with the form values |
| 5 | progress polling works (≥ 2 polls fired against the mocked endpoint) |
| 6 | failed run displays `failed_stage` and `error_message` |
| 7 | report dashboard renders against mocked endpoints |
| 8 | intent snapshot renders closed-set labels only (rejects unknown labels) |
| 9 | caveat banner is visible |
| 10 | no forecast/launch verdict language in 10B component constants |
| 11 | fixture_demo mode is selectable + submitted correctly |
| 12 | live_founder_brief is the default mode |
| 13 | API client exposes all 8 helpers for `/assembly/runs/*` |
| 14 | `tokens.css` exists with CSS variable definitions |
| 15 | locked palette literals (`#0A0A0A`, `#141414`, `#AAFF00`, `#CCCCCC`) appear in `tokens.css` |
| bonus | closed-set `IntentLabel` matches the spec's 9 labels exactly |

## 10. Build / typecheck result

- `npx tsc --noEmit` — **clean** (one pre-existing `EXEMPT_SUFFIXES: any[]` inference in legacy `label-discipline.test.ts` was fixed alongside)
- `npm run build` — **clean**, 5 routes:
  ```
  ○  /                            5.89 kB    First Load 107 kB
  ○  /_not-found                    873 B    First Load  88 kB
  ○  /new                         15.7 kB    First Load 117 kB    (legacy)
  ƒ  /run/[runId]                 8.04 kB    First Load 109 kB    (NEW)
  ƒ  /simulations/[id]/report     19.1 kB    First Load 129 kB    (legacy)
  ƒ  /simulations/[id]/status     4.17 kB    First Load 114 kB    (legacy)
  ```

## 11. Ready for 10C deployment phase?

**Yes — pending deployment-side concerns.** The pipeline + API + UI all work end-to-end locally. 10C will need to handle:

- Production hosting (Vercel for `apps/web`, Fly/Railway/etc. for `apps/api`).
- Environment variables (`NEXT_PUBLIC_ASSEMBLY_API_BASE` → real API URL).
- CORS on the FastAPI backend for the `apps/web` origin.
- Auth gate on `POST /assembly/runs` (currently unauthenticated).
- Rate limit + per-user budget tracking (the budget cap exists in code, not enforced per user).
- Migrate the inline `BackgroundTasks` to a real queue (arq).
- Persistent Postgres + Redis (already in `docker-compose.yml`, needs prod equivalents).

## 12. Recommended next phase

**10C — Deployment / auth / usage limits / cost limits.**

10B closes the founder-facing surface gap. The pipeline produces honest, evidence-anchored reports; the UI surfaces them in a dark premium AI-lab aesthetic with the locked palette and visible synthetic-society caveats. Next bottleneck before random-user beta is operational hardening (auth, queue, rate limits, cost limits, deployment).

After 10C:
- 11A — zero-data-leakage validation harness
- 11B — outcome calibration + customer pilots

---

## Spec checklist

| criterion | status |
|---|---|
| UI can submit a `live_founder_brief` product | ✅ BriefForm → POST /assembly/runs |
| UI can poll status | ✅ RunProgress polls every 5s (configurable) |
| UI can display completed report | ✅ ReportDashboard composes all sections |
| UI can display cohorts/personas/discussion/intent | ✅ Four dedicated components |
| UI has visible caveats | ✅ CaveatBanner on landing + report; per-section caveats inline |
| UI does not make fake forecast claims | ✅ Test 10 grep-asserts; closed-set intent labels only |
| Locked palette `#0A0A0A`, `#141414`, `#AAFF00`, `#CCCCCC` | ✅ Test 15 asserts literal hex in `tokens.css` |
| Frontend build/typecheck/tests pass | ✅ 40/40 tests, clean tsc, clean Next.js build |

**Decision: PASS. Proceed to Phase 10C.**
