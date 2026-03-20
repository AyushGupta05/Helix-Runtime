from __future__ import annotations

import threading
from dataclasses import dataclass

from arbiter.core.contracts import MissionSummary, RunState, utc_now
from arbiter.mission.runner import build_mission_spec, resume_mission, start_mission
from arbiter.runtime.paths import build_mission_paths
from arbiter.runtime.store import MissionStore
from arbiter.server.materializer import materialize_mission_view
from arbiter.server.registry import MissionRegistry
from arbiter.server.schemas import MissionControlResponse, MissionCreateRequest


class MissionConflictError(RuntimeError):
    pass


class MissionNotFoundError(RuntimeError):
    pass


@dataclass
class ActiveExecution:
    mission_id: str
    repo_path: str
    thread: threading.Thread


class MissionService:
    def __init__(self, strategy_backend_factory=None) -> None:
        self.registry = MissionRegistry()
        self.strategy_backend_factory = strategy_backend_factory or (lambda: None)
        self._lock = threading.Lock()
        self._active: ActiveExecution | None = None

    def close(self) -> None:
        self.registry.close()

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
            now = utc_now().isoformat()
            store = MissionStore(paths.db_path)
            try:
                store.upsert_mission(
                    mission_id=spec.mission_id,
                    status="queued",
                    repo_path=spec.repo_path,
                    branch_name=f"codex/arbiter-{spec.mission_id}",
                    outcome=None,
                    spec=spec,
                    summary=MissionSummary(
                        mission_id=spec.mission_id,
                        repo_path=spec.repo_path,
                        objective=spec.objective,
                    ),
                )
                store.upsert_control_state(
                    mission_id=spec.mission_id,
                    run_state=RunState.RUNNING.value,
                    requested_action=None,
                    reason=None,
                    updated_at=now,
                )
            finally:
                store.close()
            self.registry.upsert(
                mission_id=spec.mission_id,
                repo_path=spec.repo_path,
                objective=spec.objective,
                root_dir=paths.root_dir,
                status="queued",
                run_state=RunState.RUNNING.value,
                created_at=now,
                updated_at=now,
            )
            thread = threading.Thread(
                target=self._run_start,
                args=(spec.mission_id, request),
                daemon=True,
                name=f"arbiter-mission-{spec.mission_id}",
            )
            self._active = ActiveExecution(spec.mission_id, spec.repo_path, thread)
            thread.start()
            return MissionControlResponse(mission_id=spec.mission_id, run_state=RunState.RUNNING.value)

    def _run_start(self, mission_id: str, request: MissionCreateRequest) -> None:
        try:
            state = start_mission(
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
            self._sync_registry(mission_id, request.repo, request.objective, state.control.run_state.value)
        except Exception:
            self._sync_registry(mission_id, request.repo, request.objective, RunState.FINALIZED.value)
            raise
        finally:
            with self._lock:
                self._active = None

    def resume(self, mission_id: str) -> MissionControlResponse:
        with self._lock:
            record = self.registry.get(mission_id)
            if record is None:
                raise MissionNotFoundError(mission_id)
            if self._active and self._active.thread.is_alive():
                if self._active.mission_id == mission_id and record["run_state"] == RunState.PAUSED.value:
                    self._active.thread.join(timeout=2.0)
                if self._active.thread.is_alive():
                    raise MissionConflictError("Only one active mission is supported per process in V1.")
            self._update_control(record["repo_path"], mission_id, run_state=RunState.RUNNING.value, requested_action=None, reason=None)
            thread = threading.Thread(
                target=self._run_resume,
                args=(mission_id, record["repo_path"], record["objective"]),
                daemon=True,
                name=f"arbiter-mission-{mission_id}",
            )
            self._active = ActiveExecution(mission_id, record["repo_path"], thread)
            thread.start()
            return MissionControlResponse(mission_id=mission_id, run_state=RunState.RUNNING.value)

    def _run_resume(self, mission_id: str, repo_path: str, objective: str) -> None:
        try:
            state = resume_mission(mission_id, repo_path, strategy_backend=self.strategy_backend_factory())
            self._sync_registry(mission_id, repo_path, objective, state.control.run_state.value)
        finally:
            with self._lock:
                self._active = None

    def pause(self, mission_id: str) -> MissionControlResponse:
        record = self.registry.get(mission_id)
        if record is None:
            raise MissionNotFoundError(mission_id)
        self._update_control(record["repo_path"], mission_id, run_state=RunState.RUNNING.value, requested_action="pause", reason="user_paused")
        self._sync_registry(mission_id, record["repo_path"], record["objective"], RunState.RUNNING.value)
        return MissionControlResponse(mission_id=mission_id, run_state="pause_requested")

    def cancel(self, mission_id: str) -> MissionControlResponse:
        record = self.registry.get(mission_id)
        if record is None:
            raise MissionNotFoundError(mission_id)
        self._update_control(record["repo_path"], mission_id, run_state=RunState.CANCELLING.value, requested_action="cancel", reason="user_cancelled")
        self._sync_registry(mission_id, record["repo_path"], record["objective"], RunState.CANCELLING.value)
        return MissionControlResponse(mission_id=mission_id, run_state=RunState.CANCELLING.value)

    def list_history(self):
        entries = []
        for entry in self.registry.list():
            try:
                view = materialize_mission_view(entry.repo_path, entry.mission_id)
                entry.run_state = view.run_state
                entry.outcome = view.outcome
                entry.branch_name = view.branch_name
            except Exception:
                pass
            entries.append(entry)
        return entries

    def snapshot(self, mission_id: str):
        record = self.registry.get(mission_id)
        if record is None:
            raise MissionNotFoundError(mission_id)
        return materialize_mission_view(record["repo_path"], mission_id)

    def _update_control(self, repo_path: str, mission_id: str, run_state: str, requested_action: str | None, reason: str | None) -> None:
        paths = build_mission_paths(repo_path, mission_id)
        store = MissionStore(paths.db_path)
        try:
            store.upsert_control_state(
                mission_id=mission_id,
                run_state=run_state,
                requested_action=requested_action,
                reason=reason,
                updated_at=utc_now().isoformat(),
            )
        finally:
            store.close()

    def _sync_registry(self, mission_id: str, repo_path: str, objective: str, run_state: str) -> None:
        view = materialize_mission_view(repo_path, mission_id)
        record = self.registry.get(mission_id)
        created_at = record["created_at"] if record else utc_now().isoformat()
        self.registry.upsert(
            mission_id=mission_id,
            repo_path=repo_path,
            objective=objective,
            root_dir=build_mission_paths(repo_path, mission_id).root_dir,
            status=view.active_phase,
            run_state=run_state,
            created_at=created_at,
            updated_at=utc_now().isoformat(),
            outcome=view.outcome,
            branch_name=view.branch_name,
        )
