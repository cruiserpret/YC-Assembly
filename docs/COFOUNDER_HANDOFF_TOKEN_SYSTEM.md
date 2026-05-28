# Cofounder Handoff — Simulation Token / Credit System

> **You are picking this repo up to add a credit/token system that gates simulation runs. Read this whole file before writing code.** Every section is load-bearing.

---

## 0. What Assembly is

Assembly is a **synthetic market-reaction simulator** for product, price, and positioning decisions. A founder submits a product brief (name, description, price, target customers, competitors, geography, launch state). The backend:

1. Plans evidence queries
2. Retrieves market evidence from Brave / Tavily / YouTube / Firecrawl
3. Scores and compresses the evidence into 21–30 evidence-anchored synthetic personas ("deep debate agents")
4. Runs a 7-stage multi-round debate (4 groups × 4 rounds of LLM conversation)
5. Computes a 100-voter influence overlay on top of the 24 deep agents (Phase 12C — bounded-confidence influence loop, deterministic, no LLM per voter)
6. Generates a founder report (HTML, PDF, JSON)

Each completed simulation costs **~$11–$12 in LLM + retrieval API spend** (Anthropic Claude Sonnet for role-play + Opus for synthesis, plus Brave/Tavily/Firecrawl). End-to-end wall time: 12–20 minutes.

**Calibration principles (hard rules — do not break)**: no numeric forecasts, no forced "build / kill / pivot" verdicts, no fake data, no claims that personas have "bought / used / owned" the unlaunched product. The downloaded reports surface this explicitly.

---

## 1. Production URLs

| | |
|---|---|
| **Frontend** | https://run.assemblysimulator.com (hosted on Vercel, deploys from `main` on push) |
| **API** | https://yc-assembly-production.up.railway.app (hosted on Railway, deploys from `main` on push, uses Nixpacks) |
| **Vercel default URL** | https://yc-assembly.vercel.app (kept in CORS allowlist for legacy) |
| **GitHub** | https://github.com/cruiserpret/YC-Assembly |
| **Database** | PostgreSQL on Railway (private to API container; not directly reachable from outside) |

CORS on the API allows `run.assemblysimulator.com` and `yc-assembly.vercel.app`. If you add a new custom domain, update the `ASSEMBLY_CORS_ORIGINS` env var on the Railway project (comma-separated). Don't change anything else in CORS.

---

## 2. Repo layout

```
assembly-v0/
├── apps/
│   ├── api/                          # FastAPI backend (Python 3.12 + uv)
│   │   ├── src/assembly/
│   │   │   ├── api/                  # HTTP routes (assembly_runs.py is the main one)
│   │   │   ├── orchestration/        # live_founder_brief.py is the 13-stage pipeline
│   │   │   ├── pipeline/             # voter overlay + helpers
│   │   │   ├── sources/              # retrieval providers + compressors
│   │   │   ├── llm/                  # cost guard, provider adapters
│   │   │   ├── models/               # SQLAlchemy ORM models
│   │   │   ├── schemas/              # Pydantic request/response shapes
│   │   │   └── config.py             # env-driven Settings
│   │   ├── alembic/versions/         # DB migrations
│   │   ├── tests/                    # pytest suite (3500+ tests on main)
│   │   ├── railway.toml              # Railway Nixpacks deploy config
│   │   └── pyproject.toml
│   └── web/                          # Next.js 14 frontend (TypeScript)
│       ├── src/app/                  # App-router pages
│       │   ├── /                     # Landing + BriefForm submit
│       │   ├── /new                  # Dedicated submit page (same BriefForm)
│       │   ├── /run/[runId]          # Run progress + cockpit + report dashboard
│       │   ├── /sample-report        # Static sample with embedded transcript
│       │   └── /simulations/[id]/    # Legacy Phase 7 surface (kept for compat)
│       ├── src/components/           # React components
│       ├── src/lib/                  # API client, types, hooks
│       └── tests/                    # vitest suite (160+ tests on main)
├── packages/types/                   # TS types generated from API openapi
├── docs/                             # Top-level docs (this file lives here)
├── docker-compose.yml                # Local Postgres + Redis
└── README.md
```

---

## 3. What is completed and IN main (production)

All of the following is live on `https://run.assemblysimulator.com`:

| | |
|---|---|
| Phase 10A.3 live founder-brief pipeline | 13 stages from validation → evidence retrieval → persona build → 7-round debate → cohort/intent → report |
| Phase 12A.10 — intent cascade + influence loop + lightweight voters | Backend ships `lightweight_voter_pipeline.py` running 100 voters with a 4-round bounded-confidence influence loop on every simulation |
| Phase 12F.1 — explainability surface | Persona reasoning cards + niche signals in the founder report |
| Phase 12 — Full Debate & Conversations | All 4 groups × 4 rounds × ~6 turns persisted, surfaced in the on-screen Discussion Transcript AND the downloaded HTML/PDF |
| Phase 12-side — One-click PDF download | Real .pdf via `@react-pdf/renderer` (client-side); includes Full Debate section |
| Phase 12-side — Downloadable HTML report | Self-contained .html via `<DownloadReportButton>` |
| Phase 13B-rev3 competitor-heavy gate recovery | Customer-facing path uses 0.75 competitor-share ceiling and surfaces a "competitor-heavy market" warning instead of aborting (validation harness keeps strict 0.60) |
| User-facing language scan scope | Scans LLM-summary fields only; persona-voice subtrees (full debate, persona cards, etc.) are not scanned because they are verbatim evidence quotes |
| Phase 14A — 100-voter influence layer in the frontend | New backend endpoint `GET /assembly/runs/{run_id}/lightweight_voters` + `LightweightVoterPanel` between Intent Snapshot and Debate Transcript + voter section in downloaded HTML + downloaded PDF + Society Composition card showing "24 debate agents + 100 voters" + Brief Form copy "Debate agents (21-30)" with voter-overlay footnote |
| Voter panel visible empty/error/loading states | Never silently `return null` — always shows a visible state to the user when artifact missing |

The most recent commits on `origin/main`:
```
93155e1  Merge fix/voter-panel-visible-empty-states: visible states, no silent hiding
0f19b17  Merge fix/language-scan-skip-persona-voice: scope language scan to LLM-summary fields
58634b4  Merge feat/surface-100-voter-layer: Phase 14A — 100-voter layer in frontend
807d20e  Merge ui/new-page-uses-brief-form: /new now uses the polished BriefForm
b27ef04  Merge feat/full-debate-in-report: Full Debate transcript + real PDF report
```

---

## 4. What is INTENTIONALLY EXCLUDED from main — do NOT add it

The previous operator (with Claude) ran an experimental research track called **Phase 13 — Behavioral Agent Mind Layer** (TPB, Prospect Theory, Diffusion, Cialdini, Hegselmann-Krause, etc.). The replay diagnostics did not pass the calibration gates, so all of it was kept local-only and was never merged.

You will NOT find any of the following in the repo on GitHub. Do NOT clone them in, do NOT reinstate them, do NOT delete anything from `.gitignore` to expose them.

| Excluded item | Status |
|---|---|
| `apps/api/src/assembly/pipeline/behavioral_mind_layer/` | Local-only, never merged. Failed replay diagnostics |
| `apps/api/tests/test_behavioral_mind_layer.py` | Local-only |
| `apps/api/scripts/phase_13d_behavioral_layer_replay.py` | Local-only |
| `apps/api/docs/phase_13*.md` | Local-only |
| `ASSEMBLY_BEHAVIORAL_*` env flags in `config.py` | Not on main — main's `config.py` has none of these |
| `_audit/calibration/`, `_audit/proof_packets/`, `_audit/evidence_snapshots/` runtime artifacts | Local-only |

**Rule**: do not push experimental behavioral-layer code under the assumption "it might help." It changes simulation accuracy and was rejected by validation. Stay focused on the token system.

---

## 5. How simulations currently start (so you know where to gate)

### Frontend submit flow

`apps/web/src/components/BriefForm.tsx` is rendered on:
- `/` (the landing page, scrolled-to section)
- `/new` (dedicated submit page)

On submit, the form calls `createAssemblyRun(...)` from `apps/web/src/lib/api.ts`:

```
POST {API_BASE}/assembly/runs
{
  "mode": "live_founder_brief",
  "brief": { product_name, product_description, price, target_customers, ... }
}
```

The API base is set at build time via `NEXT_PUBLIC_ASSEMBLY_API_BASE` (production = `https://yc-assembly-production.up.railway.app`).

The frontend then redirects to `/run/{run_id}` and polls `GET /assembly/runs/{run_id}` until the run is complete.

### Backend flow (`apps/api/src/assembly/api/assembly_runs.py`)

```python
@router.post("/runs", status_code=202)
async def create_run(
    payload: CreateAssemblyRunRequest,
    session: SessionDep,
    background_tasks: BackgroundTasks,
) -> CreateAssemblyRunResponse:
    run_id = uuid.uuid4()
    # ... persist a row in assembly_runs table with status='running'
    background_tasks.add_task(_spawn_live_pipeline, run_id)
    return CreateAssemblyRunResponse(run_id=run_id, ...)
```

The actual LLM/API spend happens inside `_spawn_live_pipeline` → `run_live_founder_brief_pipeline` → the 13 stages in `apps/api/src/assembly/orchestration/live_founder_brief.py`. **The first paid LLM call happens early in the `building_personas` stage** (LLM-driven role inference). Stages before it (`validating_brief`, `planning_evidence`, `retrieving_evidence`, `scoring_evidence`) call Brave/Tavily/etc. and cost a fraction of a cent each.

### Where to gate

Put the credit check **inside `create_run` BEFORE `background_tasks.add_task(_spawn_live_pipeline, ...)`**. That guarantees:
- Frontend cannot bypass by hitting the endpoint directly
- The decision is recorded server-side
- A credit is reserved before any heavy LLM spend happens
- A refund path can fire if the run fails before reaching paid stages (`validating_brief` or `planning_evidence` failures should refund; `building_personas` and later should consume the credit)

---

## 6. Recommended token / credit system architecture

### Design principle

**Server-side enforcement.** The frontend is never the gate. The frontend may show "you have 3 credits" for UX, but the API independently verifies the credit balance every time `POST /assembly/runs` is called.

### Unit of account

**Simulation credits**, not raw dollars. 1 simulation credit = the right to start 1 simulation run. Behind the scenes the operator can calibrate "credits per dollar granted" however they want; the credit balance is the only currency the API checks.

### Suggested lifecycle

```
[reserve]   POST /assembly/runs  →  reserve 1 credit (status: reserved)
                                     ↓
                               start pipeline
                                     ↓
[consume]   pipeline reaches building_personas / first paid LLM call  →  convert reservation to consumed
                                     ↓
                               run continues OR fails
                                     ↓
[refund]    if pipeline failed BEFORE consume  →  refund the reservation
            if pipeline completed normally     →  consumed credit stays consumed
```

This sequence avoids two problems:
1. A user with 1 credit accidentally clicking Submit twice → second click sees 0 reserved-or-better credits and is refused.
2. A user whose run failed in the cheap stages (e.g. retrieval returned nothing) is not punished.

### Suggested DB schema

#### `simulation_credit_accounts`
| column | type | notes |
|---|---|---|
| `id` | UUID PK | |
| `email` | TEXT | nullable for accounts created via invite-only flow |
| `owner_label` | TEXT | display name |
| `created_at` | TIMESTAMPTZ | default now() |
| `updated_at` | TIMESTAMPTZ | |
| `is_active` | BOOLEAN | default true; suspend abusers without deleting history |

#### `simulation_credit_grants`
| column | type | notes |
|---|---|---|
| `id` | UUID PK | |
| `account_id` | UUID FK → simulation_credit_accounts.id | |
| `credits_granted` | INTEGER | positive integer |
| `granted_by` | TEXT | admin identifier or "invite_code:<id>" |
| `note` | TEXT | free-text reason |
| `created_at` | TIMESTAMPTZ | |

#### `simulation_credit_ledger`
| column | type | notes |
|---|---|---|
| `id` | UUID PK | |
| `account_id` | UUID FK → simulation_credit_accounts.id | indexed |
| `run_id` | UUID FK → assembly_runs.id NULLABLE | indexed |
| `delta` | INTEGER | positive for grant/refund, negative for reserve/consume |
| `reason` | ENUM | `grant`, `reserve`, `consume`, `refund`, `admin_adjustment` |
| `created_at` | TIMESTAMPTZ | |

The current balance is `sum(delta) where account_id = ?` — always derived, never stored in the account row. Avoid stored-balance drift.

#### `access_codes`
| column | type | notes |
|---|---|---|
| `id` | UUID PK | |
| `code_hash` | TEXT | bcrypt or sha256 — **never store raw code** |
| `credits` | INTEGER | credits to grant on redemption |
| `max_redemptions` | INTEGER | 1 for single-use, higher for shared codes |
| `redeemed_count` | INTEGER | default 0 |
| `expires_at` | TIMESTAMPTZ NULLABLE | |
| `is_active` | BOOLEAN | default true |
| `created_at` | TIMESTAMPTZ | |
| `created_by` | TEXT | admin identifier |

Add a `code_redemptions` join table if you want per-redemption audit (recommended).

### Suggested API endpoints

| Method + path | Auth | Purpose |
|---|---|---|
| `POST /access-codes/redeem` | None — but rate-limit by IP | Body: `{ code, email? }`. Validates `code_hash`, creates account if needed, writes a `grant` ledger row, returns `{ account_id, credits_remaining, session_token }` |
| `GET /credits/me` | session_token header | Returns `{ account_id, credits_remaining }` |
| `POST /admin/credits/grant` | `x-admin-token` header | Body: `{ account_id, credits, note }`. Writes a `grant` ledger row |
| `POST /admin/access-codes/create` | `x-admin-token` header | Body: `{ credits, max_redemptions, expires_at? }`. Returns the raw code ONE TIME (operator screenshots it) |
| `POST /assembly/runs` (existing) | session_token header | NEW: validates ≥1 reservable credit before scheduling; writes a `reserve` ledger row in same DB transaction as the run insert |

### Suggested concurrency model

The `POST /assembly/runs` flow must look like:

```python
async with session.begin():
    # SELECT FOR UPDATE the account row OR use a serializable transaction
    balance = await sum_ledger(session, account_id)
    if balance < 1:
        raise HTTPException(402, "Insufficient credits")
    run = AssemblyRun(...)
    session.add(run)
    await session.flush()
    session.add(SimulationCreditLedger(
        account_id=account_id, run_id=run.id, delta=-1, reason="reserve"
    ))
    # commit before scheduling background work
```

Then the background task starts the pipeline; on success/failure it appends `consume` or `refund` (NEVER deletes ledger rows).

### Suggested frontend changes

| Surface | Change |
|---|---|
| `apps/web/src/app/page.tsx` and `/new` page | Add an "Access code" input modal or screen above the BriefForm. On valid redemption, store `session_token` (httpOnly cookie preferred, localStorage as a fallback) + show credit balance |
| `apps/web/src/components/BriefForm.tsx` | Disable Submit when credits=0. Show clear inline message "0 credits remaining — redeem an access code to run another simulation" |
| New `apps/web/src/lib/useCredits.ts` | React Query hook fetching `GET /credits/me` |
| `/sample-report` | KEEP PUBLIC. No gate. This is what drives signups |
| Old run pages `/run/{id}` | KEEP VIEWABLE without credits. A user who already paid to generate a report should be able to revisit it indefinitely. Maybe even allow link-sharing |
| Admin surfaces | DO NOT expose publicly. Keep behind the `x-admin-token` header. Optionally build a tiny CLI script in `apps/api/scripts/grant_credits.py` to call the admin endpoints with the token from an env var |

### Security checklist

- Store `code_hash`, not raw codes. Generate codes with `secrets.token_urlsafe(16)`.
- All admin endpoints require `x-admin-token` header. The token lives in Railway env vars; the API rejects unknown tokens with 403.
- Server validates the credit balance on every `POST /assembly/runs`. Never trust the frontend's claim of "I have credits."
- Use `SELECT FOR UPDATE` or a serializable transaction around the reserve step. Without this, a user could double-spend by submitting two briefs in the same millisecond.
- Rate-limit `POST /access-codes/redeem` by IP — defaults like 5 attempts per hour are reasonable.
- Never log raw access codes. Log only their hashes or a redacted form.

---

## 7. Local development

### Requirements
- Docker (for Postgres + Redis)
- Python 3.12 + `uv` (`brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Node 20+ and `pnpm` (`npm install -g pnpm`)

### Bring up the stack

```bash
# from repo root
cp .env.example .env             # if missing, look at apps/api/.env.example
docker compose up -d              # postgres on :5433, redis on :6379

# Backend
cd apps/api
uv sync                           # install Python deps
uv run alembic upgrade head       # apply DB migrations
uv run uvicorn assembly.main:app --reload --port 8000
# → http://localhost:8000/docs for OpenAPI

# Frontend (new terminal)
cd apps/web
pnpm install
pnpm dev
# → http://localhost:3000
```

Set `NEXT_PUBLIC_ASSEMBLY_API_BASE=http://localhost:8000` in `apps/web/.env.local` (or it defaults there). For a real run end-to-end you need `ANTHROPIC_API_KEY` + `BRAVE_SEARCH_API_KEY` + `TAVILY_API_KEY` in `apps/api/.env`.

### Run the test suites locally

```bash
# Backend (3500+ tests)
cd apps/api
.venv/bin/pytest tests/ -q

# Frontend (160+ tests)
cd apps/web
pnpm test                         # vitest
pnpm tsc --noEmit                 # TypeScript check
pnpm build                        # Next.js production build
```

All four must be green before opening a PR.

---

## 8. Testing checklist for the token system

When you implement, verify each:

**Backend**:
- [ ] DB migration applies cleanly on `alembic upgrade head` AND reverses cleanly on `alembic downgrade -1`
- [ ] `POST /access-codes/redeem` with a valid code grants credits and returns a session
- [ ] Redeeming the same single-use code twice returns 4xx (idempotency / max-redemptions)
- [ ] Redeeming an expired or inactive code returns 4xx
- [ ] `GET /credits/me` returns balance for the authenticated account
- [ ] `POST /assembly/runs` rejects with HTTP 402 when balance < 1
- [ ] `POST /assembly/runs` writes a `reserve` ledger row in the SAME transaction as the run insert
- [ ] Pipeline failure before `building_personas` triggers a `refund` ledger row
- [ ] Pipeline reaching `building_personas` (paid LLM call) triggers a `consume` ledger row
- [ ] Concurrent double-submit from the same account does NOT both succeed (transaction isolation works)
- [ ] Admin endpoints require `x-admin-token` and 403 without it

**Frontend**:
- [ ] Submit button is disabled when `useCredits` returns 0
- [ ] Inline error shown when API returns 402 (e.g. "0 credits remaining — redeem an access code")
- [ ] Credit balance shown in the UI updates after a redemption
- [ ] Existing `/sample-report` remains publicly accessible
- [ ] Old run pages remain viewable without credits
- [ ] No admin controls in the public bundle

**Production smoke after deploy**:
- [ ] Anonymous user cannot start a new simulation
- [ ] User with a valid code can redeem + run
- [ ] User with 0 credits sees the error path, not a silently broken submit
- [ ] CORS still works for the new redeem endpoint from `run.assemblysimulator.com`

---

## 9. Deployment

### Both services auto-deploy from `main`

Pushing to `origin/main` triggers BOTH:
- **Vercel** rebuilds the frontend (≈60 seconds)
- **Railway** rebuilds the API via Nixpacks (≈90 seconds) AND runs `alembic upgrade head` before starting uvicorn

### Branch + PR workflow (recommended)

Do NOT push token-system work directly to `main`. Use a branch:

```bash
git checkout -b feat/simulation-token-system
# … work …
git push -u origin feat/simulation-token-system
# open a PR, run all tests, then merge with --no-ff or via GitHub UI
```

When you merge to `main`:
- The DB migration will run automatically on Railway's next deploy. Test it locally first.
- Vercel will rebuild and your new redeem screen will be live.
- The API will reject `POST /assembly/runs` for unauthenticated/no-credit users.

### Railway env vars

Set these on Railway *before* the migration deploy:
- `ASSEMBLY_ADMIN_TOKEN` — long random string. Required to call `/admin/*` endpoints.
- (Optional) `ASSEMBLY_DEFAULT_CREDITS_PER_INVITE` — used when you create codes from a CLI script.

Do NOT change `ANTHROPIC_API_KEY`, `BRAVE_SEARCH_API_KEY`, `TAVILY_API_KEY`, `ASSEMBLY_CORS_ORIGINS`. They are already set correctly.

### Warning: Railway ephemeral filesystem

`_audit/live_runs/{run_id}/` artifacts are written to the container's local disk. **They are wiped on every Nixpacks rebuild.** This means past runs lose their voter-overlay artifacts on every deploy. The frontend voter panel handles this gracefully (shows an "unavailable" state). For the token system this should NOT bite you — credit ledger lives in Postgres, which IS persistent.

If you ever want to persist `_audit/live_runs/` across deploys, attach a Railway Volume to the API service. That's a separate phase.

---

## 10. What NOT to do

- Do not change the simulation pipeline. The token system is purely an access-control layer.
- Do not gate `/sample-report`. It's the demo.
- Do not gate `/run/{id}` for already-completed runs. Once paid, the report stays viewable.
- Do not store raw access codes in the DB. Hash them.
- Do not trust the frontend's "I have credits" claim — re-check server-side on every `POST /assembly/runs`.
- Do not reinstate any Phase 13 / `behavioral_mind_layer/` work. It failed validation and is excluded for a reason.
- Do not raise the strict validation gates (competitor share, persona quality, language scan scope). They are calibration-first guardrails.
- Do not push directly to `main` without merging through a PR + running tests.

---

## 11. Quick orientation cheat sheet

| You want to … | Look at … |
|---|---|
| Add a route to the API | `apps/api/src/assembly/api/` (FastAPI router) |
| Add a DB table | New Alembic migration in `apps/api/alembic/versions/`, model in `apps/api/src/assembly/models/`, schema in `apps/api/src/assembly/schemas/` |
| Add a frontend page | `apps/web/src/app/<route>/page.tsx` |
| Add a frontend hook | `apps/web/src/lib/<useThing>.ts` |
| Add a component | `apps/web/src/components/<Component>.tsx` + tests in `apps/web/tests/unit/` |
| Find the run-creation endpoint | `apps/api/src/assembly/api/assembly_runs.py::create_run` |
| Find the brief form | `apps/web/src/components/BriefForm.tsx` |
| Find the API client | `apps/web/src/lib/api.ts` |
| Run a single backend test | `cd apps/api && .venv/bin/pytest tests/test_X.py -v` |
| Run a single frontend test | `cd apps/web && pnpm vitest run tests/unit/X.test.ts` |

---

## 12. Recommended first commit

Start by getting yourself oriented with a no-op PR:

```bash
git checkout main && git pull origin main --ff-only
git checkout -b feat/simulation-token-system
# Add a short note in docs/PROGRESS.md or similar saying "Day 1: read handoff doc, ran local stack"
git add docs/PROGRESS.md
git commit -m "Day 1: handoff received, local stack verified"
git push -u origin feat/simulation-token-system
```

Then start by writing the Alembic migration for the four new tables (it's the foundation everything else hangs off). Get the migration applying cleanly locally before writing any HTTP routes.

---

**Questions?** All architectural decisions in this repo are documented in `apps/api/docs/` (V0 plan, phase plans, anti-overfit gates). The CLAUDE-authored audit thread that produced this handoff lives in `/Users/hamza40/.claude/projects/` on the prior operator's machine. Ask for whichever thread is relevant.

Good luck. Don't burn the API budget. 🟢
