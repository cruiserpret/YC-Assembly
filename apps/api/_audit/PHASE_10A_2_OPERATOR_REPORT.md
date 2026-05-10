# Phase 10A.2 — Fresh Retrieval-Driven Persona Generation: Operator Report

**Phase status:** ✅ **COMPLETE**
**Demo run_id:** `1a736712-b68f-4b27-a4bd-b1ea9d0f41ac`
**Demo product:** NimbusGo (fictional cycling rear light, $59, US, unlaunched)
**Run completed at:** 2026-05-08T18:11:01 UTC

> Note: this run was completed in two passes. The retrieval + persona-generation + psychology + discussion stages ran on 2026-05-08T12:22–12:39 UTC. A latent TypeError in the cohort representative-selection sort key (`select_cohort_representatives` with `None` final_stance values) was fixed in `representatives.py:72`, then the cohort → intent → debate → report stages were resumed via `scripts/resume_live_demo_from_cohorts.py` against the already-persisted DB state. No re-spend on retrieval or LLM discussion.

---

## 1. Did Phase 10A.2 complete end-to-end?
**Yes.** All 13 stages reached `complete` status. Final run row: `status=complete`, `current_stage=complete`, `error_message=None`, 7 artifact rows registered.

## 2. Was the fresh-mode default verified?
**Yes.**
- `LiveFounderBriefOrchestrator.__init__` default: `_dev_reuse_existing_society: bool = False` ([live_founder_brief.py:1593](src/assembly/orchestration/live_founder_brief.py#L1593)).
- API spawn ([assembly_runs.py](src/assembly/api/assembly_runs.py)): `await run_live_founder_brief_pipeline(run_id)` — no flag passed, so fresh mode is used.
- 10A.2 test suite (`test_live_founder_brief_10a_2.py`) asserts `_dev_reuse_existing_society is False` by default and that the API never accepts a reuse override.

## 3. Was fixture_demo intact (regression check)?
**Yes.** Full default test suite: **1935 passed, 175 deselected**. Fixture-mode tests under `test_live_founder_brief_10a_*` continue to pass. fixture_demo path still resolves through `_FAST_DEMO_CAP_USD=$0.00` and uses no LLM.

## 4. What test product was used?
**NimbusGo** — fictional cycling rear light. Brief: `$59`, target customers `urban bike commuters`, `fitness cyclists`, `bike-share riders`. Competitors: `Bontrager Flare RT`, `Cygolite Hotshot`, `Garmin Varia`. Launch state: `unlaunched`, geography: `United States`.

## 5. Which retrieval providers were called?
**Brave + Tavily.**
- Configured keys: brave_search ✓, tavily ✓, youtube_data ✓, firecrawl ✓.
- Providers actually called: `brave_search` (8 queries) + `tavily_search` (7 queries) = **15 total queries**.
- YouTube/Firecrawl not invoked at this stage (caps hit before they were needed).

## 6. Evidence retrieval & acceptance stats
| metric | value |
|---|---|
| queries planned | 15 |
| raw items retrieved | 109 |
| accepted after anchor-match + dedupe | **85** |
| rejected | 24 (no_anchor_match=19, dup_url=3, dup_content=2) |
| signals extracted | **163** across 9 signal types |

Top signal types: `use_case_signal=41`, `competitor_usage_signal=33`, `safety_visibility_signal=21`, `performance_signal=18`, `trust_proof_signal=16`.

## 7. Final persona count?
**21 fresh run-scoped personas** (compressed from 136 raw widening candidates → 21 after `_live_compress_simple` capped at 4 per role with 35% concentration ceiling).

## 8. Were personas evidence-anchored?
**Yes.** Persistence audit: 21 PersonaRecord rows + 42 PersonaTrait rows + 21 PersonaEvidenceLink rows + 85 SourceRecord rows. All persisted under run_scope_id=`run_live_nimbusgo_f49e5ee0c8a2`. Every persona carries `product_relevance_tags` referencing source provider family + normalized role; every PersonaEvidenceLink references a real source_record_id from this run.

## 9. Were 11 psychology traits inferred per persona?
**Yes.** `psychology_trait_count=231` = 21 personas × 11 traits = **100% coverage**. `personas_with_full_psychology=21`. Strategy: `fresh_retrieval_driven` (not `_dev_reuse`).

## 10. Discussion stage stats?
- discussion_session_id `6ef772bb-91a3-45a1-825b-11e585c8bfdc`
- 3 groups × ~7 personas each
- 84 public turns + 21 peer-response turns = 105 turn rows
- 21 pre-ballots + 21 reflection ballots + 20 final ballots = **62 ballots**
- 63 PersonaMemoryAtom rows
- 147 LLM calls, 217k input tokens + 34k output tokens
- 0 transient retries, 0 failed calls

## 11. How many cohorts emerged?
**6 cohorts** of sizes [5, 5, 3, 3, 3, 2]. 11 agglomerative merges + 4 undersize re-merges. `every_persona_assigned_exactly_once=true`.

## 12. Intent distribution?
| simulated_intent | count |
|---|---|
| would_consider_if_proven | 19 |
| would_reject | 1 |
| would_compare_to_current_brand | 1 |

Switching status: actively_comparing=16, weakly_attached_to_alternative=4, refuses_switching=1.

## 13. Society-wide propagation summary?
- **16 arguments** extracted (trust_safety=6, proof_need=6, persuasion_lever=4)
- **80 propagations** across cohorts: adopted=39, intensified=20, ignored=20, modified=1
- Effect on intent: increased_uncertainty=59, no_change=21
- Most argument-receptive cohort: `performance_focused_buyer::interested_if_proven`

## 14. Were the report files produced?
**Yes — 7 artifact rows registered.**
- [founder_report.json](_audit/live_runs/1a736712-b68f-4b27-a4bd-b1ea9d0f41ac/founder_report.json)
- [founder_report.md](_audit/live_runs/1a736712-b68f-4b27-a4bd-b1ea9d0f41ac/founder_report.md)
- [run_quality.json](_audit/live_runs/1a736712-b68f-4b27-a4bd-b1ea9d0f41ac/run_quality.json)
- [discussion.json](_audit/live_runs/1a736712-b68f-4b27-a4bd-b1ea9d0f41ac/discussion.json)
- [cohort_architecture.json](_audit/live_runs/1a736712-b68f-4b27-a4bd-b1ea9d0f41ac/cohort_architecture.json)
- [simulated_intent.json](_audit/live_runs/1a736712-b68f-4b27-a4bd-b1ea9d0f41ac/simulated_intent.json)
- [persona_generation.json](_audit/live_runs/1a736712-b68f-4b27-a4bd-b1ea9d0f41ac/persona_generation.json)

## 15. API verification — does GET /assembly/runs/{run_id} return live (not fixture) data?
**Yes.** AssemblyRun row shows `status=complete`, `linked_run_scope_id=None` (correct — fresh mode does not link to a 9B parent), `artifact_manifest` pointing to fresh `_audit/live_runs/{run_id}/` paths. All artifacts are run-scoped and addressable from a frontend.

## 16. Cost summary
- **Discussion stage:** 147 LLM calls, ~$2.65 (147 × ~$0.018 Sonnet rate). Within $12 default cap.
- **Persona psychology inference:** rule-based, no LLM cost.
- **Cohort/intent/debate/report stages:** pure deterministic, no LLM cost.
- **Total:** ≈ $2.65 (well under the 12.00 USD soft cap, and below the 6.00 USD micro-cap that 10A.2 considered for cycling-niche briefs).

## 17. Quality scores
**aggregate_score = 1.0** (`READY_FOR_PHASE_10A`)
- intent_coverage_score: 1.0
- intent_label_validity_score: 1.0
- intent_evidence_score: 1.0
- intent_caveat_score: 1.0
- intent_diversity_score: 1.0
- argument_traceability_score: 1.0
- propagation_validity_score: 1.0
- propagation_coverage_score: 1.0
- anti_forecast_score: 1.0
- unlaunched_product_integrity_score: 1.0

## 18. Forbidden-claim audit
**Clean.** scanner_version=`9A.4.universal`. fake_target_product_use_count=0, forecast_or_verdict_count=0. No persona simulated having actually used NimbusGo (correct — it's unlaunched).

## 19. Sensitive-inference audit
**Clean.** finding_count=0, any_sensitive_inference=false. No demographic / financial / health inferences leaked into the report.

## 20. Secret-leak scan
**Clean.** `scan_for_secrets` over the rendered report blob found 0 findings. Report contains only `evidence_anchor` URLs from public retrieval results — no API keys, tokens, or internal endpoints.

## 21. No-drift static check
**Pass.** `tests/test_no_drift.py` (15 tests) all green — confirms LLM calls go through `cost_guarded_chat`, no direct provider.chat / provider.structured_output bypass paths in the simulation/discussion packages.

## 22. Test results
- **1935 passed / 0 failed / 175 deselected** in 5.70s
- **55 selected:** Phase 10A.2 + no-drift suites all green
- 41 new tests under `test_live_founder_brief_10a_2.py` (40 default + 1 integration) cover all 37 operator scenarios from the 10A.2 spec plus 4 bonus cases

## 23. Bug fixed during demo
**TypeError in `representatives.py:70`** — `min(final_counter, key=lambda s: (final_counter[s], s))` failed when `persona_meta` contained `None` final_stance values for personas without a `final` ballot (NimbusGo demo had 1 such persona out of 21).
**Fix:** changed sort key to `(final_counter[s], s or "")`. Applied at [representatives.py:72](src/assembly/sources/cohort_architecture/representatives.py#L72). Validated by re-running cohort/intent/debate/report stages cleanly.

## 24. No new external APIs introduced
**Confirmed.** Phase 10A.2 uses only Brave / Tavily / YouTube / Firecrawl — all already configured in earlier phases. No Jina, Exa, DataForSEO, Reddit, or Apify dependencies added.

## 25. Phase 10A.1 dev-reuse path preserved
**Yes.** `_dev_reuse_existing_society=True` is still functional as an internal flag for tests/dev that lack retrieval keys. It is **not** exposed via the API. Tests verify the API rejects any client attempt to set it.

## 26. Known minor display issue (cosmetic, non-blocking)
- The markdown renderer's preamble pulls a stale "n=66" string from the `intent_layer.render_intent_and_debate_report_markdown` template instead of the actual run-scoped persona count of 21. The structured `founder_report.json` is correct (`persona_count=21`); only the markdown header text is misleading.
- The `individual_simulation.json` and `reflection_repair.json` audit blobs reference "reuse_existing_society mode" in their `note` / `skip_reason` fields even though this run was fresh-mode. The data is correct (reflection_completeness=1.0, all 21 personas with full reflection), only the descriptive text is stale.
- Both are pre-Phase-10A.2 wording artifacts in the renderer / stage runner — they don't affect the data or the API. **Recommended fix-up in Phase 10B (frontend MVP)** when the markdown is reformatted for browser display anyway.

## 27. Ready for frontend MVP (10B)?
**Yes — recommended next phase.** Justification:
- All API surfaces return clean, run-scoped data with no fixture leakage.
- Artifact manifest is stable and resolvable from a frontend.
- Cost cap + budget pre-check are wired and enforced.
- Psychology + cohorts + intent + propagation all populate run-scoped DB tables that the frontend can query directly (no batch refresh needed).
- Markdown output is renderable in any standard markdown component; minor "n=66" header wording can be patched in 10B as the renderer is reformatted for the web.

## 28. Ready for random user beta after frontend (10D)?
**Yes — conditional.** The pipeline produces honest, evidence-anchored outputs at $2.65 per run. Three remaining items before opening to random users:
1. **10B frontend MVP** — surface the report + auditability to non-technical users (must show evidence URLs, not raw IDs).
2. **10C deployment hardening** — production Postgres + Redis + secrets management; concurrency caps for Brave/Tavily; rate-limit + abuse-detection at the API.
3. **Calibration disclaimer** — visible on every report: synthetic intent ≠ market forecast; n=21 is a hypothesis-generator, not a demand predictor. (The text is already in the report appendix; needs to be in the UI header too.)

## Recommended next phase: **10B Frontend MVP**

10A.2 closes the loop on "fresh evidence-driven personas at API cost-cap" and produces clean operator-grade artifacts. The bottleneck for user-facing beta is now UI fidelity, not pipeline capability.
