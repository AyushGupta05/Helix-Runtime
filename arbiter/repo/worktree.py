from __future__ import annotations

import subprocess
from pathlib import Path


class WorktreeManager:
    def __init__(self, repo_path: str, worktree_path: str, branch_name: str) -> None:
        self.repo_path = Path(repo_path).resolve()
        self.worktree_path = Path(worktree_path).resolve()
        self.branch_name = branch_name

    def ensure(self) -> None:
        self.worktree_path.parent.mkdir(parents=True, exist_ok=True)
        if self.worktree_path.exists() and (self.worktree_path / ".git").exists():
            return
        subprocess.run(
            ["git", "worktree", "add", "-b", self.branch_name, str(self.worktree_path), "HEAD"],
            cwd=str(self.repo_path),
            check=True,
            capture_output=True,
            text=True,
        )

    def remove(self) -> None:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(self.worktree_path)],
            cwd=str(self.repo_path),
            check=False,
            capture_output=True,
            text=True,
        )

