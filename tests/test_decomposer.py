from __future__ import annotations

from pathlib import Path

from arbiter.core.contracts import CapabilitySet, CommandResult, RepoSnapshot
from arbiter.mission.decomposer import GoalDecomposer


def test_failure_evidence_maps_nested_backend_paths_and_related_sources(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "backend" / "tests").mkdir(parents=True)
    (repo / "backend" / "app" / "services").mkdir(parents=True)
    (repo / "backend" / "app" / "routes").mkdir(parents=True)
    (repo / "backend" / "tests" / "test_sla.py").write_text("def test_summary():\n    assert True\n", encoding="utf-8")
    (repo / "backend" / "app" / "services" / "sla_service.py").write_text("def summarize():\n    return {}\n", encoding="utf-8")
    (repo / "backend" / "app" / "routes" / "settings.py").write_text("def route():\n    return {}\n", encoding="utf-8")

    snapshot = RepoSnapshot(
        repo_path=str(repo),
        capabilities=CapabilitySet(runtime="python"),
        initial_test_results=[
            CommandResult(
                command=["python", "-m", "pytest"],
                exit_code=1,
                stdout="FAILED tests/test_sla.py::test_summary_honors_resolved_filter\napp/routes/settings.py:11: in get_settings\n",
                stderr="",
                duration_seconds=0.0,
            )
        ],
        initial_lint_results=[],
        initial_static_results=[],
    )

    evidence = GoalDecomposer._failure_evidence_files(snapshot)

    assert "backend/tests/test_sla.py" in evidence
    assert "backend/app/routes/settings.py" in evidence
    assert "backend/app/services/sla_service.py" in evidence
