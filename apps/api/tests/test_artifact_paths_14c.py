"""Phase 14C — durable run-artifact path resolution tests.

Covers the artifact_paths helper + the readers/writers that route through
it, so that completed-run artifacts can live under a configurable durable
root (ASSEMBLY_ARTIFACT_ROOT → Railway Volume) and survive redeploys.

These tests are path-resolution only: no DB, no HTTP, no LLM, and they
assert that the helper introduces NO simulation / calibration / persona
logic.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from assembly.api.assembly_runs import (
    _load_live_artifact_json,
    _read_run_artifact,
    _resolve_live_run_dir,
)
from assembly.artifact_paths import (
    ARTIFACT_ROOT_ENV,
    artifact_root,
    atomic_write_text,
    live_runs_root,
    run_artifact_dir,
    run_artifact_path,
)

_RID = "11111111-1111-4111-8111-111111111111"  # uuid-shaped, safe segment


# --------------------------------------------------------------------------
# Root resolution: default vs ASSEMBLY_ARTIFACT_ROOT
# --------------------------------------------------------------------------


def test_artifact_root_defaults_to_apps_api_audit(monkeypatch):
    monkeypatch.delenv(ARTIFACT_ROOT_ENV, raising=False)
    # This test lives at apps/api/tests/, so parents[1] == apps/api.
    expected = Path(__file__).resolve().parents[1] / "_audit"
    assert artifact_root() == expected
    assert artifact_root().name == "_audit"
    assert artifact_root().parent.name == "api"


def test_artifact_root_uses_env_when_set(monkeypatch, tmp_path):
    monkeypatch.setenv(ARTIFACT_ROOT_ENV, str(tmp_path))
    assert artifact_root() == tmp_path.resolve()


def test_artifact_root_ignores_blank_env(monkeypatch, tmp_path):
    monkeypatch.setenv(ARTIFACT_ROOT_ENV, "   ")
    # Blank/whitespace env must fall back to the default, not "" cwd.
    expected = Path(__file__).resolve().parents[1] / "_audit"
    assert artifact_root() == expected


def test_live_runs_root_is_under_artifact_root(monkeypatch, tmp_path):
    monkeypatch.setenv(ARTIFACT_ROOT_ENV, str(tmp_path))
    assert live_runs_root() == tmp_path.resolve() / "live_runs"


# --------------------------------------------------------------------------
# run_id scoping
# --------------------------------------------------------------------------


def test_run_artifact_dir_is_run_id_scoped(monkeypatch, tmp_path):
    monkeypatch.setenv(ARTIFACT_ROOT_ENV, str(tmp_path))
    d = run_artifact_dir(_RID)
    assert d == tmp_path.resolve() / "live_runs" / _RID
    assert d.parent == live_runs_root()
    assert d.name == _RID


def test_run_artifact_dir_does_not_create_or_require_existence(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv(ARTIFACT_ROOT_ENV, str(tmp_path))
    d = run_artifact_dir(_RID)
    # Pure resolution — must not have created anything on disk.
    assert not d.exists()


def test_run_artifact_path_joins_filename(monkeypatch, tmp_path):
    monkeypatch.setenv(ARTIFACT_ROOT_ENV, str(tmp_path))
    p = run_artifact_path(_RID, "founder_report.json")
    assert p == run_artifact_dir(_RID) / "founder_report.json"


# --------------------------------------------------------------------------
# Path-traversal rejection
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["..", ".", "../x", "a/b", "a/../b", "", "/etc/passwd", "x\\y", "..\\.."],
)
def test_run_artifact_dir_rejects_traversal(monkeypatch, tmp_path, bad):
    monkeypatch.setenv(ARTIFACT_ROOT_ENV, str(tmp_path))
    with pytest.raises(ValueError):
        run_artifact_dir(bad)


@pytest.mark.parametrize("bad", ["../secret.json", "a/b.json", "..", ""])
def test_run_artifact_path_rejects_bad_filename(monkeypatch, tmp_path, bad):
    monkeypatch.setenv(ARTIFACT_ROOT_ENV, str(tmp_path))
    with pytest.raises(ValueError):
        run_artifact_path(_RID, bad)


def test_valid_uuid_run_id_is_accepted(monkeypatch, tmp_path):
    monkeypatch.setenv(ARTIFACT_ROOT_ENV, str(tmp_path))
    # Should not raise.
    run_artifact_dir(_RID)
    run_artifact_dir("abc_DEF-123.4")


# --------------------------------------------------------------------------
# Atomic write + read-back, and "survives restart" (same root, re-resolve)
# --------------------------------------------------------------------------


def test_atomic_write_text_round_trips(monkeypatch, tmp_path):
    monkeypatch.setenv(ARTIFACT_ROOT_ENV, str(tmp_path))
    p = run_artifact_path(_RID, "founder_report.json")
    payload = {"phase": "14c", "ok": True}
    atomic_write_text(p, json.dumps(payload))
    assert p.exists()
    assert json.loads(p.read_text(encoding="utf-8")) == payload
    # No stray temp file left behind.
    assert not (p.parent / f".{p.name}.tmp").exists()


def test_artifact_survives_simulated_restart(monkeypatch, tmp_path):
    """Write under a durable root, then re-resolve the path from scratch
    (env + run_id only — no in-memory state, as a fresh process would) and
    confirm the artifact is still readable. This is the redeploy contract:
    same mounted root ⇒ same artifacts."""
    monkeypatch.setenv(ARTIFACT_ROOT_ENV, str(tmp_path))
    payload = {"founder_report": "durable", "n_voters": 100}
    atomic_write_text(
        run_artifact_path(_RID, "founder_report.json"), json.dumps(payload),
    )
    # "Restart": forget everything, re-derive the dir purely from env+run_id.
    rederived = run_artifact_dir(_RID)
    assert _read_run_artifact(rederived, "founder_report.json") == payload


# --------------------------------------------------------------------------
# Readers honour the configured root
# --------------------------------------------------------------------------


def test_resolve_live_run_dir_uses_configured_root(monkeypatch, tmp_path):
    monkeypatch.setenv(ARTIFACT_ROOT_ENV, str(tmp_path))
    seed = run_artifact_dir(_RID)
    seed.mkdir(parents=True, exist_ok=True)
    (seed / "lightweight_voters.json").write_text(
        json.dumps({"n_voters": 100}), encoding="utf-8",
    )
    run = SimpleNamespace(id=_RID)
    resolved = _resolve_live_run_dir(run)
    assert resolved == seed
    assert _read_run_artifact(resolved, "lightweight_voters.json") == {
        "n_voters": 100,
    }


def test_resolve_live_run_dir_missing_is_graceful(monkeypatch, tmp_path):
    """Missing artifacts must NOT crash — the resolver returns the durable
    path (which doesn't exist) and the reader returns None, which the
    endpoints turn into the voter_overlay_available=false / 503 contract."""
    monkeypatch.setenv(ARTIFACT_ROOT_ENV, str(tmp_path))
    run = SimpleNamespace(id=_RID)
    resolved = _resolve_live_run_dir(run)
    assert not resolved.exists()
    assert _read_run_artifact(resolved, "lightweight_voters.json") is None


def test_report_loader_reads_from_configured_root(monkeypatch, tmp_path):
    """_load_live_artifact_json reads the manifest's absolute path. For a
    run written under the durable root, that path lives under the
    configured root — proving report/discussion loaders are durable."""
    monkeypatch.setenv(ARTIFACT_ROOT_ENV, str(tmp_path))
    run_dir = run_artifact_dir(_RID)
    run_dir.mkdir(parents=True, exist_ok=True)
    report = {"executive_summary": ["durable"], "synthetic_society_size": 24}
    discussion = {"groups": [{"group_index": 0, "rounds": [1, 2, 3, 4]}]}
    (run_dir / "founder_report.json").write_text(
        json.dumps(report), encoding="utf-8",
    )
    (run_dir / "discussion.json").write_text(
        json.dumps(discussion), encoding="utf-8",
    )
    run = SimpleNamespace(
        id=_RID,
        artifact_manifest={
            "report_json": str(run_dir / "founder_report.json"),
            "discussion_json": str(run_dir / "discussion.json"),
        },
    )
    assert _load_live_artifact_json(run, "report_json") == report
    assert _load_live_artifact_json(run, "discussion_json") == discussion


def test_report_loader_missing_manifest_path_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv(ARTIFACT_ROOT_ENV, str(tmp_path))
    # Manifest points at a path that doesn't exist (old run after redeploy).
    run = SimpleNamespace(
        id=_RID,
        artifact_manifest={
            "report_json": str(run_artifact_dir(_RID) / "founder_report.json"),
        },
    )
    assert _load_live_artifact_json(run, "report_json") is None


# --------------------------------------------------------------------------
# Safety: the helper adds NO simulation / calibration / LLM logic
# --------------------------------------------------------------------------


def _artifact_paths_src() -> str:
    p = (
        Path(__file__).resolve().parents[1]
        / "src" / "assembly" / "artifact_paths.py"
    )
    return p.read_text(encoding="utf-8")


def test_artifact_paths_imports_only_stdlib():
    """The path helper must be pure plumbing: it may import only stdlib —
    never assembly.simulation / calibration / persona / LLM modules."""
    tree = ast.parse(_artifact_paths_src())
    top_mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                top_mods.add(n.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_mods.add(node.module.split(".")[0])
    assert top_mods <= {"os", "re", "pathlib", "__future__"}, (
        f"artifact_paths must import only stdlib, got: {sorted(top_mods)}"
    )


def test_artifact_paths_has_no_llm_or_sim_calls():
    src = _artifact_paths_src().lower()
    for forbidden in (
        "anthropic", "openai", "structured_output",
        "generate_voters", "run_influence_rounds", "compress_to_live_society",
    ):
        assert forbidden not in src


def test_writer_uses_run_artifact_dir():
    """The orchestrator must resolve its write dir via run_artifact_dir so
    writes land under the durable root."""
    p = (
        Path(__file__).resolve().parents[1]
        / "src" / "assembly" / "orchestration" / "live_founder_brief.py"
    )
    src = p.read_text(encoding="utf-8")
    assert "from assembly.artifact_paths import" in src
    assert "run_artifact_dir(str(self.run_id))" in src


def test_reader_uses_run_artifact_dir():
    p = (
        Path(__file__).resolve().parents[1]
        / "src" / "assembly" / "api" / "assembly_runs.py"
    )
    src = p.read_text(encoding="utf-8")
    assert "run_artifact_dir" in src
