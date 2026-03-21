from __future__ import annotations

from datetime import datetime, timedelta
import json
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path

from arbiter.core.contracts import MissionOutcome, MissionSpec, MissionSummary, RunState, utc_now
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


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


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
                requested_skills=request.requested_skills,
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
                    branch_name=f"codex/helix-{spec.mission_id}",
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
                requested_skills=request.requested_skills,
                max_runtime=request.max_runtime,
                benchmark_requirement=request.benchmark_requirement,
                protected_paths=request.protected_paths,
                public_api_surface=request.public_api_surface,
                strategy_backend=self.strategy_backend_factory(),
                mission_id=mission_id,
            )
        except Exception as exc:
            self._record_thread_failure(str(resolve_repo_path(request.repo)), mission_id, exc)
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
                    elif self._current_run_state(resolved_repo, mission_id) == RunState.PAUSED.value:
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
        except Exception as exc:
            self._record_thread_failure(repo_path, mission_id, exc)
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
                    runtime_seconds=view.runtime_seconds,
                    run_state=view.run_state,
                    status=view.status or view.active_phase,
                    outcome=view.outcome,
                    branch_name=view.branch_name,
                    total_tokens=view.usage_summary.get("mission", {}).get("total_tokens", 0),
                    total_cost=view.usage_summary.get("mission", {}).get("total_cost", 0.0),
                    checkpoint_count=view.history_metrics.get("checkpoint_count", 0),
                    failure_count=view.history_metrics.get("failure_count", 0),
                    changed_file_count=view.history_metrics.get("changed_file_count", 0),
                    recovery_count=view.history_metrics.get("recovery_count", 0),
                    validator_status=view.history_metrics.get("validation", {}).get("status"),
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

    def _current_run_state(self, repo_path: str, mission_id: str) -> str | None:
        paths = build_mission_paths(repo_path, mission_id)
        migrate_legacy_mission(paths, mission_id)
        store = MissionStore(paths.db_path)
        try:
            control = store.fetch_control_state(mission_id)
            return control["run_state"] if control else None
        finally:
            store.close()

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
                RunState.CANCELLING.value,
            }:
                return
            mission = store.fetch_mission(mission_id)
            runtime = store.fetch_runtime(mission_id)
            if reason == "session_ended":
                activity = [
                    _parse_timestamp(mission["updated_at"]) if mission is not None else None,
                    _parse_timestamp(runtime["updated_at"]) if runtime is not None else None,
                    _parse_timestamp(control["updated_at"]),
                ]
                latest_event = store.fetch_ordered("events", "created_at DESC, id DESC", mission_id)
                latest_trace = store.fetch_ordered("trace_entries", "created_at DESC, id DESC", mission_id)
                latest_invocation = store.fetch_ordered("model_invocations", "COALESCE(completed_at, started_at) DESC, id DESC", mission_id)
                activity.extend(
                    [
                        _parse_timestamp(latest_event[0]["created_at"]) if latest_event else None,
                        _parse_timestamp(latest_trace[0]["created_at"]) if latest_trace else None,
                        _parse_timestamp(latest_invocation[0]["completed_at"] or latest_invocation[0]["started_at"])
                        if latest_invocation
                        else None,
                    ]
                )
                in_flight_invocation = next(
                    (
                        row
                        for row in latest_invocation
                        if row["status"] == "started" and not row["completed_at"]
                    ),
                    None,
                )
                in_flight_started_at = (
                    _parse_timestamp(in_flight_invocation["started_at"])
                    if in_flight_invocation is not None
                    else None
                )
                if in_flight_started_at is not None and utc_now() - in_flight_started_at <= timedelta(minutes=2):
                    return
                latest_activity = max((value for value in activity if value is not None), default=None)
                if latest_activity is not None and utc_now() - latest_activity <= timedelta(seconds=20):
                    return
            if mission is not None:
                summary = MissionSummary.model_validate(json.loads(mission["summary_json"]))
                if summary.outcome is None:
                    summary.outcome = MissionOutcome.FAILED_SAFE_STOP
                summary.stop_reason = reason
                spec = MissionSpec.model_validate(json.loads(mission["spec_json"]))
                store.upsert_mission(
                    mission_id=mission_id,
                    status=RunState.FINALIZED.value,
                    repo_path=mission["repo_path"],
                    objective=mission["objective"],
                    branch_name=mission["branch_name"],
                    outcome=summary.outcome.value if summary.outcome else None,
                    spec=spec,
                    summary=summary,
                    created_at=mission["created_at"],
                )
            store.upsert_control_state(
                mission_id=mission_id,
                run_state=RunState.FINALIZED.value,
                requested_action=None,
                reason=reason,
                updated_at=utc_now().isoformat(),
            )
        finally:
            store.close()

    def _record_thread_failure(self, repo_path: str, mission_id: str, exc: Exception) -> None:
        paths = build_mission_paths(repo_path, mission_id)
        error_text = traceback.format_exc()
        Path(paths.root_dir, "thread-error.log").write_text(error_text, encoding="utf-8")
        store = MissionStore(paths.db_path)
        stop_reason = f"mission_thread_crashed: {exc}"
        now = utc_now().isoformat()
        try:
            mission = store.fetch_mission(mission_id)
            if mission is not None:
                summary = MissionSummary.model_validate(json.loads(mission["summary_json"]))
                summary.outcome = MissionOutcome.FAILED_EXECUTION
                summary.stop_reason = stop_reason
                spec = MissionSpec.model_validate(json.loads(mission["spec_json"]))
                store.upsert_mission(
                    mission_id=mission_id,
                    status=RunState.FINALIZED.value,
                    repo_path=mission["repo_path"],
                    objective=mission["objective"],
                    branch_name=mission["branch_name"],
                    outcome=MissionOutcome.FAILED_EXECUTION.value,
                    spec=spec,
                    summary=summary,
                    created_at=mission["created_at"],
                )
            store.connection.execute(
                "UPDATE mission_runtime SET active_phase = ?, stop_reason = ?, updated_at = ? WHERE mission_id = ?",
                ("finalize", stop_reason, now, mission_id),
            )
            store.connection.commit()
            store.upsert_control_state(
                mission_id=mission_id,
                run_state=RunState.FINALIZED.value,
                requested_action=None,
                reason=stop_reason,
                updated_at=now,
            )
            store.refresh_mission_view(mission_id)
        finally:
            store.close()
