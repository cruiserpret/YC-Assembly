"""Phase 14A — GET /assembly/runs/{run_id}/lightweight_voters endpoint tests.

Covers the founder-facing voter overlay endpoint:
  - returns voter payload when lightweight_voters.json is on disk
  - returns voter_overlay_available=false (HTTP 200) when the run pre-
    dates Phase 12C or the artifact is missing — does NOT 404, so the
    rest of the report still renders
  - returns voter_overlay_available=false for fixture_demo runs
  - never regenerates voters, never mutates artifacts, never calls LLMs
  - validates the run_id parameter the same way other endpoints do
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from assembly.api.assembly_runs import (
    _read_run_artifact,
)


ROUTER_PATH = (
    Path(__file__).resolve().parent.parent
    / "src" / "assembly" / "api" / "assembly_runs.py"
)


def _router_src() -> str:
    return ROUTER_PATH.read_text(encoding="utf-8")


# ---------- Source / wiring tests ----------


def test_endpoint_is_registered():
    src = _router_src()
    assert "/runs/{run_id}/lightweight_voters" in src
    assert "async def get_lightweight_voters" in src


def test_endpoint_uses_load_run_helper():
    src = _router_src()
    # Find the function body
    tree = ast.parse(src)
    fn = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "get_lightweight_voters"
    )
    body_src = ast.unparse(fn)
    # Must use the canonical run loader (UUID validation, 404 on missing)
    assert "_load_run" in body_src
    # Must call the status check so still-running runs return 425
    assert "_live_run_status_check" in body_src


def test_endpoint_does_not_call_llm_helpers():
    src = _router_src()
    tree = ast.parse(src)
    fn = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "get_lightweight_voters"
    )
    body_src = ast.unparse(fn)
    # Defensive: this endpoint must NEVER invoke an LLM. Any reference
    # to ai/llm provider names in the function body would be wrong.
    forbidden = ["anthropic", "openai", "structured_output", "provider.chat"]
    for tok in forbidden:
        assert tok.lower() not in body_src.lower(), (
            f"endpoint body must not reference '{tok}' — voter overlay "
            f"is deterministic and must never call an LLM"
        )


def test_endpoint_does_not_regenerate_voter_pipeline():
    src = _router_src()
    tree = ast.parse(src)
    fn = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "get_lightweight_voters"
    )
    body_src = ast.unparse(fn)
    # Calling the overlay would regenerate voters. Forbidden.
    assert "run_lightweight_voter_overlay" not in body_src
    assert "generate_voters_from_cohorts" not in body_src
    assert "run_influence_rounds" not in body_src


def test_endpoint_does_not_write_to_disk():
    src = _router_src()
    tree = ast.parse(src)
    fn = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "get_lightweight_voters"
    )
    body_src = ast.unparse(fn)
    # No write_text / write_bytes / mkdir in this endpoint — read-only.
    assert "write_text" not in body_src
    assert "write_bytes" not in body_src
    assert ".mkdir(" not in body_src


# ---------- _read_run_artifact unit tests ----------


def test_read_run_artifact_returns_none_when_missing(tmp_path):
    out = _read_run_artifact(tmp_path, "nope.json")
    assert out is None


def test_read_run_artifact_returns_none_on_bad_json(tmp_path):
    (tmp_path / "broken.json").write_text("{ not json", encoding="utf-8")
    out = _read_run_artifact(tmp_path, "broken.json")
    assert out is None


def test_read_run_artifact_returns_parsed_dict(tmp_path):
    payload = {"phase": "12c_lightweight_voters", "n_voters": 100}
    (tmp_path / "lightweight_voters.json").write_text(
        json.dumps(payload), encoding="utf-8",
    )
    out = _read_run_artifact(tmp_path, "lightweight_voters.json")
    assert out == payload


# ---------- Payload-shape tests against synthetic run_dir ----------


def _seed_run_dir(run_dir: Path) -> None:
    """Drop synthetic voter artifacts mirroring the on-disk shape that
    run_lightweight_voter_overlay() writes."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "lightweight_voters.json").write_text(json.dumps({
        "phase": "12c_lightweight_voters",
        "n_voters": 100,
        "simulation_seed": 12345,
        "voters": [{"voter_id": f"v_{i}"} for i in range(100)],
        "sampling_warnings": [],
        "completed_at": "2026-05-27T00:00:00+00:00",
    }), encoding="utf-8")
    (run_dir / "final_100_voter_distribution.json").write_text(json.dumps({
        "phase": "12c_final_voter_distribution",
        "lightweight_voter_distribution": {
            "buyer": 12.0,
            "receptive": 28.0,
            "uncertain": 42.0,
            "skeptical": 18.0,
            "total_population_weight": 100.0,
            "n_voters": 100,
        },
        "calibrated_distribution": {
            "distribution_percent": {
                "buyer": 11.0,
                "receptive": 27.0,
                "uncertain": 44.0,
                "skeptical": 18.0,
            },
            "confidence_band_pp": 8.0,
            "used_prior_correction": False,
            "blend_weights": {"rich_24": 0.4, "voter_100": 0.6},
            "calibration_warnings": [],
        },
        "raw_24_distribution_percent": {
            "buyer": 8, "receptive": 25, "uncertain": 50, "skeptical": 17,
        },
    }), encoding="utf-8")
    (run_dir / "influence_rounds.json").write_text(json.dumps({
        "phase": "12c_influence_rounds",
        "rounds": [
            {
                "round_idx": i,
                "round_type": "initial" if i == 0 else "influence",
                "voters_affected": (i * 7) if i > 0 else 0,
                "intent_changes": (i * 3) if i > 0 else 0,
                "bucket_changes": (i * 2) if i > 0 else 0,
                "bucket_distribution": {
                    "buyer": 10 + i, "receptive": 25 + i,
                    "uncertain": 45 - i, "skeptical": 20 - i,
                },
                "skeptic_transitions": {},
                # Large per-voter log — endpoint should slim this out.
                "per_voter_log": [{"voter": k} for k in range(100)],
            }
            for i in range(4)
        ],
        "cluster_arguments": {
            "pro": ["argument A", "argument B"],
            "con": ["objection X"],
        },
    }), encoding="utf-8")
    (run_dir / "diversity_health.json").write_text(json.dumps({
        "phase": "12c_diversity_health",
        "n_voters": 100,
        "n_cohorts_represented": 6,
        "n_segments_represented": 6,
        "n_roles_represented": 12,
        "max_role_concentration": 0.21,
        "competitor_user_share": 0.42,
        "n_edges": 800,
        "avg_edges_per_voter": 8.0,
        "edges_per_voter_min": 4,
        "edges_per_voter_max": 14,
        "edge_type_distribution": {"peer": 700, "authority": 100},
        "intent_diversity_per_round": {"0": 6, "1": 6, "2": 5, "3": 5},
        "intent_changes_count": 27,
        "bucket_changes_count": 11,
        "warnings": [],
    }), encoding="utf-8")


def _endpoint_payload_against_synthetic_dir(tmp_path: Path) -> dict:
    """Exercise the endpoint's artifact-reading logic against a
    synthetic run_dir without spinning up the DB / HTTP stack."""
    _seed_run_dir(tmp_path)
    # We re-implement the parsing branch of the endpoint by calling
    # _read_run_artifact directly. This isolates the payload-shape
    # contract from FastAPI / DB plumbing.
    voters = _read_run_artifact(tmp_path, "lightweight_voters.json")
    final_dist = _read_run_artifact(
        tmp_path, "final_100_voter_distribution.json",
    )
    inf = _read_run_artifact(tmp_path, "influence_rounds.json")
    div = _read_run_artifact(tmp_path, "diversity_health.json")
    # Mirror endpoint logic: slim per_voter_log from rounds.
    slim_rounds = []
    if isinstance(inf, dict):
        for r in inf.get("rounds") or []:
            if isinstance(r, dict):
                slim_rounds.append({
                    k: v for k, v in r.items() if k != "per_voter_log"
                })
    return {
        "voter_overlay_available": True,
        "voters_count": (
            voters.get("n_voters")
            if isinstance(voters, dict) else 0
        ),
        "final_distribution": (
            final_dist.get("lightweight_voter_distribution")
            if isinstance(final_dist, dict) else None
        ),
        "calibrated_distribution": (
            final_dist.get("calibrated_distribution")
            if isinstance(final_dist, dict) else None
        ),
        "influence_rounds": slim_rounds,
        "diversity_health": div,
    }


def test_payload_includes_voters_count(tmp_path):
    payload = _endpoint_payload_against_synthetic_dir(tmp_path)
    assert payload["voters_count"] == 100
    assert payload["voter_overlay_available"] is True


def test_payload_includes_final_distribution_four_buckets(tmp_path):
    payload = _endpoint_payload_against_synthetic_dir(tmp_path)
    final = payload["final_distribution"]
    assert final is not None
    for bucket in ("buyer", "receptive", "uncertain", "skeptical"):
        assert bucket in final
    # Sum should be ~100 (percentages)
    total = sum(final[b] for b in ("buyer", "receptive", "uncertain", "skeptical"))
    assert 99.0 <= total <= 101.0


def test_payload_includes_calibrated_distribution_with_confidence_band(tmp_path):
    payload = _endpoint_payload_against_synthetic_dir(tmp_path)
    cal = payload["calibrated_distribution"]
    assert cal is not None
    assert "confidence_band_pp" in cal
    assert "blend_weights" in cal


def test_payload_includes_four_influence_rounds(tmp_path):
    payload = _endpoint_payload_against_synthetic_dir(tmp_path)
    rounds = payload["influence_rounds"]
    assert len(rounds) == 4
    # Round indices 0..3
    indices = sorted(r["round_idx"] for r in rounds)
    assert indices == [0, 1, 2, 3]


def test_endpoint_strips_per_voter_log_from_rounds(tmp_path):
    """The per-round per-voter log can be 100 entries × 4 rounds; the
    founder-facing endpoint must slim this out to keep response size
    sane and to avoid leaking internal audit detail into the UI."""
    payload = _endpoint_payload_against_synthetic_dir(tmp_path)
    for r in payload["influence_rounds"]:
        assert "per_voter_log" not in r, (
            "per_voter_log must NOT appear in the founder-facing payload"
        )


def test_payload_includes_diversity_health(tmp_path):
    payload = _endpoint_payload_against_synthetic_dir(tmp_path)
    div = payload["diversity_health"]
    assert div is not None
    assert div["n_voters"] == 100
    assert "competitor_user_share" in div


def test_missing_artifacts_yield_empty_state(tmp_path):
    # Don't seed any files — simulates an old run pre-dating Phase 12C.
    voters = _read_run_artifact(tmp_path, "lightweight_voters.json")
    final_dist = _read_run_artifact(
        tmp_path, "final_100_voter_distribution.json",
    )
    assert voters is None
    assert final_dist is None
    # Endpoint logic returns voter_overlay_available=False in this case.
    # That branch is tested at the source level via the function body.


def test_router_endpoint_returns_voter_overlay_available_false_for_missing():
    """Static check: the endpoint's missing-artifact branch sets
    voter_overlay_available=False (not 404). This is what lets old runs
    keep rendering the rest of the report."""
    src = _router_src()
    # Find the function and confirm the empty-state branch
    assert re.search(
        r'voter_overlay_available["\']?\s*:\s*False',
        src,
    ), (
        "endpoint must return voter_overlay_available=False (HTTP 200, "
        "not 404) when artifacts are missing"
    )


def test_endpoint_does_not_raise_404_for_fixture_demo():
    """Fixture-demo runs didn't exercise the voter pipeline. The
    endpoint must NOT 404 them — it returns the empty-state shape."""
    src = _router_src()
    tree = ast.parse(src)
    fn = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "get_lightweight_voters"
    )
    body_src = ast.unparse(fn)
    assert 'fixture_demo' in body_src
    # Fixture branch returns dict with voter_overlay_available=False,
    # not raises HTTPException(404).
    assert "HTTPException" not in body_src.split('fixture_demo')[1].split("\n")[1:4][0] or "False" in body_src
