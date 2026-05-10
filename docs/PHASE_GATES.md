# Phase Entry Conditions

Tracked corrections and prerequisites that must be satisfied before specific
future phases begin. Each entry references the phase it gates and the source
of the requirement (Critic observation, user directive, etc.).

---

## Phase 6 entry conditions

### O1 — Every orchestrated LLM call must go through `with_cost_guard`

**Source:** Architecture Critic post-implementation review of Phase 3 + 4
(2026-05-01). Confirmed by user directive when approving Phase 5.

**Requirement:** When the Phase 6 worker orchestrates the simulation pipeline
(parser → society → simulation rounds → aggregation), **every** LLM call must
be wrapped by [`with_cost_guard`](../apps/api/src/assembly/llm/cost_guard.py).
No direct `provider.chat(...)` or `provider.structured_output(...)` calls in
the worker. Otherwise the per-simulation cost cap is silently un-enforced.

**Affected call sites that currently bypass the guard** (intentional layering;
the orchestrator must wrap them):

| Call site | File |
|---|---|
| `parse_brief` calls `provider.chat(...)` directly | [pipeline/intake_parser.py](../apps/api/src/assembly/pipeline/intake_parser.py) |
| `extract_category_language` calls `provider.structured_output(...)` directly | [pipeline/evidence_builder.py](../apps/api/src/assembly/pipeline/evidence_builder.py) |
| (Phase 5 — to be added) `build_society` calls the LLM | [pipeline/society_builder.py](../apps/api/src/assembly/pipeline/society_builder.py) |
| (Phase 6 — to be added) per-round per-agent role-play calls | `pipeline/rounds/*.py` |
| (Phase 7 — to be added) per-section aggregation calls | `pipeline/aggregation/*.py` |

**Phase 6 acceptance test:** the worker must have a single shared helper —
e.g. `async def call_llm_for_simulation(stage, ...)` — that internally wraps
`with_cost_guard`, and **every** LLM call site in the worker must go through
it. A static lint test (`tests/test_no_drift.py`) should grep the worker
package for `provider.chat(`, `provider.structured_output(` and fail if found
outside the helper.

### Pending: arq worker integration

The `simulation_worker.py` arq entry point is currently a stub. Phase 6 wires
it.

---

## Pre-Phase-8 (frontend) hardening

### O2 — Replace static user-content fence markers with randomized per-call sentinels

**Source:** Architecture Critic post-implementation review of Phase 3 + 4
(2026-05-01).

**Requirement:** [`wrap_user_content_as_data`](../apps/api/src/assembly/llm/provider.py)
currently uses static fence markers `<<<USER_INPUT_START>>>` /
`<<<USER_INPUT_END>>>`. A user brief that contained those exact strings could
escape the fence and inject instructions. Not exploitable in V0 (single-user,
internal), but **must be hardened before Phase 8** when external users
submit briefs.

**Mitigation:** generate a per-call random sentinel (e.g. 16 hex chars
prefixed/suffixed with `<<<…>>>`) and pass it into the system prompt so the
LLM knows the exact fence for that call. Verify the user content does not
contain the sentinel; if it does, regenerate the sentinel.

**Phase 8 acceptance test:** add a test where the user input itself contains
`<<<USER_INPUT_END>>>` and confirm the wrapper either escapes / regenerates
the sentinel and the wrapped output cannot be misparsed.

---

## How to use this document

The CTO agent reads this file at the start of each phase. The Critic agent
checks that gating conditions for the entering phase are satisfied before
approving the phase plan.
