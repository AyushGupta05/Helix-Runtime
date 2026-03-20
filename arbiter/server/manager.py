from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from arbiter.core.contracts import MissionSummary, RunState, utc_now
from arbiter.mission.runner import build_mission_spec, resume_mission, start_mission
from arbiter.runtime.migrate import migrate_legacy_mission
from arbiter.runtime.paths import build_mission_paths, resolve_repo_path
from arbiter.runtime.store import MissionStore
from arbiter.server.materializer import materialize_mission_view
from arbiter.server.schemas import MissionControlResponse, MissionCreateRequest, MissionHistoryEntry


class MissionConflictError(RuntimeError):
    pass


class MissionNotFoundError(RuntimeError):
    pass


@dataclass
class ActiveExecution:
    mission_id: str
    repo_path: str
    thread: threading.Thread


def _mission_roots(repo_path: str) -> list[Path]:
    repo = resolve_repo_path(repo_path)
    root = repo / ".arbiter" / "missions"
    if not root.exists():
        return []
    return [path for path in root.iterdir() if path.is_dir()]


class MissionService:
    def __init__(self, strategy_backend_factory=None) -> None:
        self.strategy_backend_factory = strategy_backend_factory or (lambda: None)
        self._lock = threading.Lock()
        self._active: ActiveExecution | None = None
        self._known_repos: dict[str, str] = {}

    def close(self) -> None:
        with self._lock:
            active = self._active
            self._active = None
        if active:
            self._finalize_orphaned_mission(active.repo_path, active.mission_id, reason="session_terminated")

    def start(self, request: MissionCreateRequest) -> MissionControlResponse:
        with self._lock:
            if self._active and self._active.thread.is_alive():
                raise MissionConflictError("Only one active mission is supported per process in V1.")
            spec = build_mission_spec(
                repo=request.repo,
                objective=request.objective,
                constraints=request.constraints,
                preferences=request.preferences,
                max_runtime=request.max_runtime,
                benchmark_requirement=request.benchmark_requirement,
                protected_paths=request.protected_paths,
                public_api_surface=request.public_api_surface,
            )
            paths = build_mission_paths(spec.repo_path, spec.mission_id)
            store = MissionStore(paths.db_path)
            try:
                store.upsert_mission(
                    mission_id=spec.mission_id,
                    status="running",
                    repo_path=spec.repo_path,
                    objective=spec.objective,
                    branch_name=f"codex/arbiter-{spec.mission_id}",
                    outcome=None,
                    spec=spec,
                    summary=MissionSummary(mission_id=spec.mission_id, repo_path=spec.repo_path, objective=spec.objective),
                    created_at=utc_now().isoformat(),
                )
                store.upsert_control_state(spec.mission_id, RunState.RUNNING.value, None, None, utc_now().isoformat())
            finally:
                store.close()
            thread = threading.Thread(target=self._run_start, args=(spec.mission_id, request), daemon=True, name=f"arbiter-mission-{spec.mission_id}")
            self._active = ActiveExecution(spec.mission_id, spec.repo_path, thread)
            self._known_repos[spec.mission_id] = spec.repo_path
            thread.start()
            return MissionControlResponse(mission_id=spec.mission_id, run_state=RunState.RUNNING.value, repo_path=spec.repo_path)

    def _run_start(self, mission_id: str, request: MissionCreateRequest) -> None:
        try:
            start_mission(
                repo=request.repo,
                objective=request.objective,
                constraints=request.constraints,
                preferences=request.preferences,
                max_runtime=request.max_runtime,
                benchmark_requirement=request.benchmark_requirement,
                protected_paths=request.protected_paths,
                public_api_surface=request.public_api_surface,
                strategy_backend=self.strategy_backend_factory(),
                mission_id=mission_id,
            )
        finally:
            with self._lock:
                self._active = None

    def resolve_repo(self, mission_id: str, repo_path: str | None = None) -> str:
        if repo_path:
            return str(resolve_repo_path(repo_path))
        if mission_id in self._known_repos:
            return self._known_repos[mission_id]
        if self._active and self._active.mission_id == mission_id:
            return self._active.repo_path
        raise MissionNotFoundError(mission_id)

    def resume(self, repo_path: str | None, mission_id: str) -> MissionControlResponse:
        resolved_repo = self.resolve_repo(mission_id, repo_path)
        with self._lock:
            if self._active and self._active.thread.is_alive():
                if self._active.mission_id == mission_id:
                    self._active.thread.join(timeout=10.0)
                    if not self._active.thread.is_alive():
                        self._active = None
                if self._active and self._active.thread.is_alive():
                    raise MissionConflictError("Only one active mission is supported per process in V1.")
            self._update_control(resolved_repo, mission_id, RunState.RUNNING.value, None, None)
            thread = threading.Thread(target=self._run_resume, args=(resolved_repo, mission_id), daemon=True, name=f"arbiter-mission-{mission_id}")
            self._active = ActiveExecution(mission_id, resolved_repo, thread)
            self._known_repos[mission_id] = resolved_repo
            thread.start()
            return MissionControlResponse(mission_id=mission_id, run_state=RunState.RUNNING.value, repo_path=resolved_repo)

    def _run_resume(self, repo_path: str, mission_id: str) -> None:
        try:
            resume_mission(mission_id, repo_path, strategy_backend=self.strategy_backend_factory())
        finally:
            with self._lock:
                self._active = None

    def pause(self, repo_path: str | None, mission_id: str) -> MissionControlResponse:
        resolved_repo = self.resolve_repo(mission_id, repo_path)
        self._ensure_mission_exists(resolved_repo, mission_id)
        self._update_control(resolved_repo, mission_id, RunState.RUNNING.value, "pause", "user_paused")
        return MissionControlResponse(mission_id=mission_id, run_state="pause_requested", repo_path=resolved_repo)

    def cancel(self, repo_path: str | None, mission_id: str) -> MissionControlResponse:
        resolved_repo = self.resolve_repo(mission_id, repo_path)
        self._ensure_mission_exists(resolved_repo, mission_id)
        self._update_control(resolved_repo, mission_id, RunState.CANCELLING.value, "cancel", "user_cancelled")
        return MissionControlResponse(mission_id=mission_id, run_state=RunState.CANCELLING.value, repo_path=resolved_repo)

    def list_history(self, repo_path: str | None) -> list[MissionHistoryEntry]:
        if repo_path is None:
            repo_path = self._active.repo_path if self._active else next(iter(self._known_repos.values()), None)
            if repo_path is None:
                return []
        entries: list[MissionHistoryEntry] = []
        for mission_root in _mission_roots(repo_path):
            mission_id = mission_root.name
            try:
                self._normalize_mission_state(str(repo_path), mission_id)
                paths = build_mission_paths(repo_path, mission_id)
                migrate_legacy_mission(paths, mission_id)
                store = MissionStore(paths.db_path)
                try:
                    view = materialize_mission_view(repo_path, mission_id)
                    mission = store.fetch_mission(mission_id)
                    runtime = store.fetch_runtime(mission_id)
                    control = store.fetch_control_state(mission_id)
                finally:
                    store.close()
            except Exception:
                continue
            if mission is None:
                continue
            updated_at = max(
                (
                    timestamp
                    for timestamp in (
                        mission["updated_at"],
                        runtime["updated_at"] if runtime else None,
                        control["updated_at"] if control else None,
                    )
                    if timestamp
                ),
                default=mission["created_at"],
            )
            entries.append(
                MissionHistoryEntry(
                    mission_id=mission_id,
                    repo_path=view.repo_path,
                    objective=view.objective,
                    created_at=mission["created_at"],
                    updated_at=updated_at,
                    run_state=view.run_state,
                    status=view.status or view.active_phase,
                    outcome=view.outcome,
                    branch_name=view.branch_name,
                )
            )
            self._known_repos[mission_id] = view.repo_path
        return sorted(entries, key=lambda item: item.updated_at, reverse=True)

    def snapshot(self, repo_path: str | None, mission_id: str):
        resolved_repo = self.resolve_repo(mission_id, repo_path)
        self._ensure_mission_exists(resolved_repo, mission_id)
        self._normalize_mission_state(resolved_repo, mission_id)
        self._known_repos[mission_id] = resolved_repo
        return materialize_mission_view(resolved_repo, mission_id)

    def _update_control(self, repo_path: str, mission_id: str, run_state: str, requested_action: str | None, reason: str | None) -> None:
        paths = build_mission_paths(repo_path, mission_id)
        migrate_legacy_mission(paths, mission_id)
        store = MissionStore(paths.db_path)
        try:
            store.upsert_control_state(mission_id=mission_id, run_state=run_state, requested_action=requested_action, reason=reason, updated_at=utc_now().isoformat())
        finally:
            store.close()

    def _ensure_mission_exists(self, repo_path: str, mission_id: str) -> None:
        paths = build_mission_paths(repo_path, mission_id)
        migrate_legacy_mission(paths, mission_id)
        if not Path(paths.db_path).exists():
            raise MissionNotFoundError(mission_id)

    def _normalize_mission_state(self, repo_path: str, mission_id: str) -> None:
        with self._lock:
            is_live_in_process = bool(
                self._active
                and self._active.mission_id == mission_id
                and self._active.thread.is_alive()
            )
        if is_live_in_process:
            return
        self._finalize_orphaned_mission(repo_path, mission_id, reason="session_ended")

    def _finalize_orphaned_mission(self, repo_path: str, mission_id: str, reason: str) -> None:
        paths = build_mission_paths(repo_path, mission_id)
        migrate_legacy_mission(paths, mission_id)
        store = MissionStore(paths.db_path)
        try:
            control = store.fetch_control_state(mission_id)
            if not control or control["run_state"] not in {
                RunState.RUNNING.value,
                RunState.PAUSED.value,
                RunState.CANCELLING.value,
            }:
                return
            store.upsert_control_state(
                mission_id=mission_id,
                run_state=RunState.FINALIZED.value,
                requested_action=None,
                reason=reason,
                updated_at=utc_now().isoformat(),
            )
        finally:
            store.close()
