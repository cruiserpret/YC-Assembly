# Assembly

Synthetic market-reaction infrastructure for product, price, and positioning decisions.

Assembly takes a product brief, builds a weighted population of evidence-grounded buyer-state agents, runs a multi-round simulation (baseline → first exposure → objection → competitor comparison → proof → social influence → final stance), and returns structured **subjective** market intelligence. Assembly does not emit numeric forecasts or forced build/revise/kill verdicts — output language is constrained programmatically.

This is the V0 ground-zero build. The prior `assembly-backend/` (v3-lite deterministic forecaster) is unrelated and untouched.

## Layout

```
assembly-v0/
├── apps/
│   ├── api/    # FastAPI backend + arq worker (Python)
│   └── web/    # Next.js 15 frontend (TypeScript)
└── packages/
    └── types/  # Generated TS types from the API's OpenAPI schema
```

## Local development

Requirements: Docker, Python 3.12, `uv`, Node 20+, `pnpm`.

```bash
cp .env.example .env
docker compose up -d              # postgres + redis

cd apps/api
uv sync
uv run alembic upgrade head
uv run uvicorn assembly.main:app --reload --port 8000

# in another terminal
uv run arq assembly.workers.simulation_worker.WorkerSettings

# in another terminal
cd apps/web
pnpm install
pnpm dev                          # http://localhost:3000
```

## Phases (see `/Users/hamza40/.claude/plans/you-are-claude-acting-misty-cocke.md` for the full plan)

- **Phase 0** — Repo & infra scaffold ✅
- **Phase 1** — Data layer + intake API
- **Phase 2** — Frontend intake form
- **Phase 3** — LLM provider abstraction
- **Phase 4** — Intake parser + evidence builder
- **Phase 5** — Society builder
- **Phase 6** — Simulation engine (7 rounds)
- **Phase 7** — Aggregation engine + output validator
- **Phase 8** — Report UI
- **Phase 9** — Cost & observability
- **Phase 10** — Outcome ingestion + calibration scaffolding
- **Phase 11** — Zero-leakage validation harness

## Design commitments

- **Evidence is real; interpretation is simulated.** Strict separation in the data model.
- **Agents are buyer-state models, not personas.** Each carries `current_alternatives`, `budget`, `trust_threshold`, `switching_trigger`, `fear`, `desire`, `influence_score`, `price_sensitivity`, `objection_pattern`, `emotional_state`.
- **Subjective output only.** A deterministic validator rejects numeric forecasts (`%`, `CTR`, `CAC`), absolute claims (`will`, `definitely`), forced verdicts (`build`, `kill`, `pivot`), and objective sentiment (`the market is positive`, `customers want this`).
- **Evidence ledger is mandatory.** A structural check enforces that every output surfaces direct, analogical, and missing evidence.
- **Calibration scaffolded from day one.** Every simulation persists evidence manifest, agents, round transcript, and output for future zero-leakage backtests.
- **No accuracy claims until benchmarks exist.**

## Architecture Critic

Before starting any major phase — and again after implementing it — run the **Assembly Architecture Critic** review documented in [docs/ASSEMBLY_CRITIC_AGENT.md](docs/ASSEMBLY_CRITIC_AGENT.md). The Critic is the founding-team reviewer that prevents architecture drift, fake evidence, and premature feature creep. It enforces 17 non-negotiable product rules and 8 architecture-drift checks, and it has hard veto power: if it marks a plan or change `Blocked`, implementation does not continue until the violation is corrected.

Mechanical enforcement of the language and structural rules lives in [apps/api/src/assembly/pipeline/aggregation/validator.py](apps/api/src/assembly/pipeline/aggregation/validator.py); run `cd apps/api && uv run pytest tests/test_validator.py -v` after any prompt or output-schema change.

---

## Deployment

This monorepo is laid out so the frontend ships to **Vercel** and the backend ships to **Railway**, both from the same git repo.

### Frontend → Vercel

1. Import the GitHub repo into Vercel.
2. In the project settings, set **Root Directory** to `apps/web`.
3. Vercel auto-detects Next.js — no overrides needed.
4. Add the following environment variables (Project Settings → Environment Variables):

   | Name | Example value |
   |---|---|
   | `NEXT_PUBLIC_ASSEMBLY_API_BASE` | `https://yc-assembly-api.up.railway.app` |
   | `NEXT_PUBLIC_ASSEMBLY_PUBLIC_MODE` | `true` |
   | `NEXT_PUBLIC_ASSEMBLY_DEBUG` | *(leave unset)* |

5. Deploy. The first build will compile Tailwind + Next.js and prerender `/` and `/sample-report`.

### Backend → Railway

1. Create a new Railway project.
2. Add the GitHub repo as a service. Set **Root Directory** to `apps/api` in the service settings.
3. Add a **Postgres** plugin (Railway will set `DATABASE_URL` automatically).
4. Add a **Redis** plugin (sets `REDIS_URL`).
5. The bundled [`apps/api/railway.toml`](apps/api/railway.toml) + [`apps/api/Procfile`](apps/api/Procfile) tell Railway to run `uvicorn assembly.main:app --host 0.0.0.0 --port $PORT` and hit `/health` for liveness.
6. Add the following environment variables (Variables tab):

   | Name | Required? | Notes |
   |---|---|---|
   | `ANTHROPIC_API_KEY` | yes | role-play + synthesis LLM |
   | `OPENAI_API_KEY` | optional | embedding fallback / future routing |
   | `BRAVE_SEARCH_API_KEY` | yes | Tier-1 search |
   | `TAVILY_API_KEY` | yes | Tier-1 search |
   | `YOUTUBE_DATA_API_KEY` | recommended | Tier-1 buyer-language signal |
   | `FIRECRAWL_API_KEY` | optional | Tier-2 escalation only |
   | `ASSEMBLY_DATABASE_URL` | yes | rewrite Railway's `DATABASE_URL` to `postgresql+asyncpg://…` form |
   | `ASSEMBLY_DATABASE_URL_SYNC` | yes | rewrite to `postgresql+psycopg://…` form |
   | `ASSEMBLY_REDIS_URL` | yes | mirror Railway's `REDIS_URL` |
   | `ASSEMBLY_CORS_ORIGINS` | yes | comma-separated Vercel URL(s) |
   | `ASSEMBLY_COST_HARD_USD` | optional | per-run cap; default `5.00`. Bump to `18.00` for live demo runs. |
   | `ASSEMBLY_LLM_SYNTHESIS_MODEL` | optional | default `claude-opus-4-7` |
   | `ASSEMBLY_LLM_ROLEPLAY_MODEL` | optional | default `claude-sonnet-4-6` |

7. After the first deploy, run the Alembic migrations once. Railway's "Shell" tab in the service → `alembic upgrade head`.

### Wire them together

- Copy the Railway service's public URL into Vercel's `NEXT_PUBLIC_ASSEMBLY_API_BASE`.
- Add the Vercel deployment URL to the Railway backend's `ASSEMBLY_CORS_ORIGINS`.
- Trigger a fresh Vercel build so the new API base bakes in.

### Local secrets

`.env` is gitignored — never commit it. The committed templates are [`.env.example`](.env.example) (backend) and [`apps/web/.env.example`](apps/web/.env.example) (frontend). Copy them to `.env` / `apps/web/.env.local` locally and fill in your own keys.
