"""Exercise the friend-facing runner with Windows PowerShell 5.1, offline."""
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell runner")
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops" / "run_on_B.ps1"
STEPS = {
    "preflight", "sync", "pytest", "memory-snapshot", "memory-series",
    "model-download", "model-verify", "llama-check", "pydantic-probe", "security-gate",
}


def run_script(*args):
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT), *args],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )


def test_dry_run_bundle(tmp_path):
    out = tmp_path / "friend reports"
    # Exercise scrubbing with a synthetic token and a real profile prefix;
    # DryRun must never try to open this nonexistent model.
    pretend_model = Path(os.environ.get("USERPROFILE", str(tmp_path))) / "ghp_TestSecret123456789.gguf"
    before = set(ROOT.glob("report_B_*.zip"))
    try:
        result = run_script("-DryRun", "-OutDir", str(out), "-ModelPath", str(pretend_model))
        assert result.returncode == 0, result.stdout + result.stderr
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8-sig"))
        assert summary["schema_version"] == 1
        assert summary["hostname"] and summary["timestamp"]
        assert {step["name"] for step in summary["steps"]} == STEPS
        assert all(step["status"] == "skipped" and step["seconds"] >= 0 for step in summary["steps"])
        assert {step["name"] for step in summary["plan"]} == STEPS
        assert "Send this file to ADMIN:" in result.stdout
        archives = sorted(ROOT.glob(f"report_B_{summary['hostname']}_*.zip"), key=lambda p: p.stat().st_mtime)
        assert archives
        with zipfile.ZipFile(archives[-1]) as archive:
            assert "summary.json" in archive.namelist()
            assert all(Path(name).suffix in {".json", ".csv", ".log", ".txt"} for name in archive.namelist())
            outputs = result.stdout + result.stderr + "".join(archive.read(name).decode("utf-8-sig") for name in archive.namelist())
            assert json.loads(archive.read("summary.json")) == summary
        lowered = outputs.lower()
        assert "gho_" not in lowered and "ghp_" not in lowered
        profile = os.environ.get("USERPROFILE", "")
        if profile:
            assert profile.lower() not in lowered
            assert profile.replace("\\", "\\\\").lower() not in lowered
    finally:
        for archive in set(ROOT.glob("report_B_*.zip")) - before:
            archive.unlink()


@pytest.mark.parametrize("reserve_delta", [-1, 0, 1])
def test_auto_pick_at_reserve_boundary(reserve_delta):
    models = json.loads((ROOT / "ops" / "catalogue.json").read_text())["models"]
    models.sort(key=lambda model: model["est_bytes_ctx8192"])
    for model in models:
        free = model["est_bytes_ctx8192"] + 512 * 1024**2 + reserve_delta
        fits = [m for m in models if m["est_bytes_ctx8192"] <= free - 512 * 1024**2]
        expected = (fits or models[:1])[-1]
        result = run_script("-PlanOnly", "-FreeVramBytes", str(free))
        assert result.returncode == 0, result.stdout + result.stderr
        plan = json.loads(result.stdout)
        assert plan["model_id"] == expected["model_id"]
        assert plan["build"] == "cuda12"
        assert plan["steps"] == []


def test_auto_pick_without_gpu_and_explicit_overrides():
    models = json.loads((ROOT / "ops" / "catalogue.json").read_text())["models"]
    smallest = min(models, key=lambda m: m["est_bytes_ctx8192"])
    result = run_script("-PlanOnly", "-FreeVramBytes", "0", "-Build", "cpu")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["model_id"] == smallest["model_id"]
    result = run_script("-PlanOnly", "-DryRun", "-ModelId", models[0]["model_id"], "-Build", "vulkan")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["model_id"] == models[0]["model_id"]
    assert json.loads(result.stdout)["build"] == "vulkan"
