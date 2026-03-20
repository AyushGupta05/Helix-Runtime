from __future__ import annotations

import subprocess
from pathlib import Path
from shutil import rmtree


class WorktreeSetupError(RuntimeError):
    """Raised when Arbiter cannot create an isolated worktree."""


class WorktreeManager:
    def __init__(self, repo_path: str, worktree_path: str, branch_name: str) -> None:
        self.repo_path = Path(repo_path).resolve()
        self.worktree_path = Path(worktree_path).resolve()
        self.branch_name = branch_name

    def ensure(self) -> None:
        self.worktree_path.parent.mkdir(parents=True, exist_ok=True)
        if self.worktree_path.exists() and (self.worktree_path / ".git").exists():
            return
        if self.worktree_path.exists() and not (self.worktree_path / ".git").exists():
            rmtree(self.worktree_path, ignore_errors=True)

        repo_check = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(self.repo_path),
            check=False,
            capture_output=True,
            text=True,
        )
        if repo_check.returncode != 0 or repo_check.stdout.strip().lower() != "true":
            raise WorktreeSetupError(
                f"Target path is not a git repository: {self.repo_path}"
            )

        head_check = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=str(self.repo_path),
            check=False,
            capture_output=True,
            text=True,
        )
        if head_check.returncode != 0:
            raise WorktreeSetupError(
                "Target repo must have at least one commit before Arbiter can create an isolated worktree. "
                "Run `git add -A && git commit -m \"initial\"` in the target repo, then try again."
            )

        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=str(self.repo_path),
            check=False,
            capture_output=True,
            text=True,
        )
        try:
            subprocess.run(
                ["git", "worktree", "add", "-b", self.branch_name, str(self.worktree_path), "HEAD"],
                cwd=str(self.repo_path),
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            stdout = (exc.stdout or "").strip()
            details = stderr or stdout or "git worktree add failed"
            raise WorktreeSetupError(
                f"Failed to create isolated worktree for {self.repo_path}: {details}"
            ) from exc

    def remove(self) -> None:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(self.worktree_path)],
            cwd=str(self.repo_path),
            check=False,
            capture_output=True,
            text=True,
        )
