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

