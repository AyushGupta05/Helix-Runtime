from __future__ import annotations

import os
import subprocess
from pathlib import Path
from shutil import copytree, rmtree


class WorktreeSetupError(RuntimeError):
    """Raised when Arbiter cannot create an isolated worktree."""


class WorktreeManager:
    _DEPENDENCY_DIR_CANDIDATES = (
        "node_modules",
        "frontend/node_modules",
        "backend/node_modules",
        ".venv",
        "venv",
        "env",
        "frontend/.venv",
        "frontend/venv",
        "backend/.venv",
        "backend/venv",
    )

    def __init__(self, repo_path: str, worktree_path: str, branch_name: str) -> None:
        self.repo_path = Path(repo_path).resolve()
        self.worktree_path = Path(worktree_path).resolve()
        self.branch_name = branch_name

    def ensure(self) -> None:
        self.worktree_path.parent.mkdir(parents=True, exist_ok=True)
        if self.worktree_path.exists() and (self.worktree_path / ".git").exists():
            if self._is_expected_worktree():
                self._hydrate_dependency_dirs(self.worktree_path)
                return
            self.remove_path(str(self.worktree_path))
            rmtree(self.worktree_path, ignore_errors=True)
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
            self._hydrate_dependency_dirs(self.worktree_path)
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            stdout = (exc.stdout or "").strip()
            details = stderr or stdout or "git worktree add failed"
            if "already exists" in details.lower():
                try:
                    subprocess.run(
                        ["git", "worktree", "add", str(self.worktree_path), self.branch_name],
                        cwd=str(self.repo_path),
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    self._hydrate_dependency_dirs(self.worktree_path)
                    return
                except subprocess.CalledProcessError as inner_exc:
                    details = ((inner_exc.stderr or inner_exc.stdout) or details).strip()
                    raise WorktreeSetupError(
                        f"Failed to attach existing isolated worktree for {self.repo_path}: {details}"
                    ) from inner_exc
            raise WorktreeSetupError(
                f"Failed to create isolated worktree for {self.repo_path}: {details}"
            ) from exc

    def _is_expected_worktree(self) -> bool:
        try:
            worktree_check = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=str(self.worktree_path),
                check=False,
                capture_output=True,
                text=True,
            )
            if worktree_check.returncode != 0 or worktree_check.stdout.strip().lower() != "true":
                return False
            branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=str(self.worktree_path),
                check=False,
                capture_output=True,
                text=True,
            )
            if branch.returncode != 0 or branch.stdout.strip() != self.branch_name:
                return False
            repo_common = subprocess.run(
                ["git", "rev-parse", "--git-common-dir"],
                cwd=str(self.repo_path),
                check=False,
                capture_output=True,
                text=True,
            )
            worktree_common = subprocess.run(
                ["git", "rev-parse", "--git-common-dir"],
                cwd=str(self.worktree_path),
                check=False,
                capture_output=True,
                text=True,
            )
            if repo_common.returncode != 0 or worktree_common.returncode != 0:
                return False
            repo_common_path = Path(repo_common.stdout.strip())
            worktree_common_path = Path(worktree_common.stdout.strip())
            if not repo_common_path.is_absolute():
                repo_common_path = (self.repo_path / repo_common_path).resolve()
            else:
                repo_common_path = repo_common_path.resolve()
            if not worktree_common_path.is_absolute():
                worktree_common_path = (self.worktree_path / worktree_common_path).resolve()
            else:
                worktree_common_path = worktree_common_path.resolve()
            return repo_common_path == worktree_common_path
        except OSError:
            return False

    def remove(self) -> None:
        self.remove_path(str(self.worktree_path))

    def remove_path(self, target_path: str) -> None:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(Path(target_path).resolve())],
            cwd=str(self.repo_path),
            check=False,
            capture_output=True,
            text=True,
        )

    def ensure_detached(self, target_path: str, ref: str = "HEAD") -> None:
        path = Path(target_path).resolve()
        if path.exists() and (path / ".git").exists():
            self._hydrate_dependency_dirs(path)
            return
        if path.exists() and not (path / ".git").exists():
            rmtree(path, ignore_errors=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=str(self.repo_path),
            check=False,
            capture_output=True,
            text=True,
        )
        try:
            subprocess.run(
                ["git", "worktree", "add", "--detach", str(path), ref],
                cwd=str(self.repo_path),
                check=True,
                capture_output=True,
                text=True,
            )
            self._hydrate_dependency_dirs(path)
        except subprocess.CalledProcessError as exc:
            details = (exc.stderr or exc.stdout or "git worktree add --detach failed").strip()
            raise WorktreeSetupError(f"Failed to create scratch worktree for {self.repo_path}: {details}") from exc

    def _hydrate_dependency_dirs(self, target_root: Path) -> None:
        for relative_dir in self._DEPENDENCY_DIR_CANDIDATES:
            source = self.repo_path / relative_dir
            target = target_root / relative_dir
            if not source.is_dir() or target.exists() or not self._dependency_dir_is_reusable(source):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            self._link_dependency_dir(source, target)

    @staticmethod
    def _dependency_dir_is_reusable(source: Path) -> bool:
        try:
            next(source.iterdir())
        except (OSError, StopIteration):
            return False
        return True

    def _link_dependency_dir(self, source: Path, target: Path) -> None:
        try:
            if os.name == "nt":
                subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(target), str(source)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            else:
                target.symlink_to(source, target_is_directory=True)
        except (OSError, subprocess.CalledProcessError):
            copytree(source, target)
