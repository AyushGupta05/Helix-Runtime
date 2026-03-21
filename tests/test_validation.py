from __future__ import annotations

from arbiter.core.contracts import MissionSpec, RepoSnapshot, SuccessCriteria, TaskNode, TaskRequirementLevel, TaskType
from arbiter.repo.collector import RepoStateCollector
from arbiter.tools.local import LocalToolset
from arbiter.validators.engine import ValidationEngine


def test_public_api_guard_blocks_changes(tmp_path) -> None:
    from conftest import init_git_repo

    repo = init_git_repo(
        tmp_path / "api_repo",
        {
            "api.py": "def public_api():\n    return 1\n",
            "tests/test_api.py": "from api import public_api\n\n\ndef test_api():\n    assert public_api() == 1\n",
        },
    )
    toolset = LocalToolset(str(repo))
    toolset.apply_file_updates({"api.py": "def public_api():\n    return 2\n"})
    spec = MissionSpec(mission_id="m1", repo_path=str(repo), objective="Refactor", public_api_surface=["api.py"])
    snapshot = RepoStateCollector(str(repo)).collect(run_commands=False)
    task = TaskNode(
        task_id="T1",
        title="Refactor",
        task_type=TaskType.REFACTOR,
        requirement_level=TaskRequirementLevel.REQUIRED,
        success_criteria=SuccessCriteria(description="done"),
        allowed_tools=["edit"],
        validator_requirements=["tests"],
    )
    report = ValidationEngine(toolset, spec, snapshot).validate(task)
    assert report.api_guard_passed is False
    assert report.passed is False


def test_validation_allows_baseline_lint_failure_on_no_regression_basis(tmp_path) -> None:
    from conftest import init_git_repo

    repo = init_git_repo(
        tmp_path / "lint_baseline_repo",
        {
            "pyproject.toml": "[project]\nname = 'demo'\nversion = '0.1.0'\n",
            "calc.py": "import os\n\n\ndef add(a, b):\n    return a - b\n",
            "tests/test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        },
    )
    toolset = LocalToolset(str(repo))
    snapshot = RepoStateCollector(str(repo)).collect(run_commands=True)
    toolset.apply_file_updates({"calc.py": "import os\n\n\ndef add(a, b):\n    return a + b\n"})
    spec = MissionSpec(mission_id="m2", repo_path=str(repo), objective="Fix failing tests")
    task = TaskNode(
        task_id="T2",
        title="Bugfix",
        task_type=TaskType.BUGFIX,
        requirement_level=TaskRequirementLevel.REQUIRED,
        success_criteria=SuccessCriteria(description="done"),
        allowed_tools=["edit_file", "run_tests"],
        validator_requirements=["tests", "lint", "static"],
    )
    report = ValidationEngine(toolset, spec, snapshot).validate(task)
    assert report.passed is True
    assert "baseline_lint_failure_persisted" in report.validator_deltas
    assert any("no-regression basis" in note for note in report.notes)
