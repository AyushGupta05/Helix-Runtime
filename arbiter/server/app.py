from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from arbiter.core.contracts import RunState
from arbiter.runtime.migrate import migrate_legacy_mission
from arbiter.runtime.paths import build_mission_paths
from arbiter.runtime.store import MissionStore
from arbiter.server.manager import MissionConflictError, MissionNotFoundError, MissionService
from arbiter.server.schemas import MissionCreateRequest


def create_app(strategy_backend_factory=None) -> FastAPI:
    service = MissionService(strategy_backend_factory=strategy_backend_factory)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.mission_service = service
        try:
            yield
        finally:
            service.close()

    app = FastAPI(title="Arbiter Mission Control", version="0.2.0", lifespan=lifespan)
    app.state.mission_service = service
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8000", "http://localhost:8000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/api/missions")
    def create_mission(payload: MissionCreateRequest, request: Request):
        try:
            return request.app.state.mission_service.start(payload)
        except MissionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/missions")
    def list_missions(request: Request, repo: str | None = None):
        return request.app.state.mission_service.list_history(repo)

    @app.get("/api/missions/{mission_id}")
    def get_mission(mission_id: str, request: Request, repo: str | None = None):
        try:
            return request.app.state.mission_service.snapshot(repo, mission_id)
        except MissionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Mission {mission_id} not found") from exc

    @app.post("/api/missions/{mission_id}/pause")
    def pause_mission(mission_id: str, request: Request, repo: str | None = None):
        try:
            return request.app.state.mission_service.pause(repo, mission_id)
        except MissionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Mission {mission_id} not found") from exc

    @app.post("/api/missions/{mission_id}/resume")
    def resume_mission_route(mission_id: str, request: Request, repo: str | None = None):
        try:
            return request.app.state.mission_service.resume(repo, mission_id)
        except MissionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except MissionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Mission {mission_id} not found") from exc

    @app.post("/api/missions/{mission_id}/cancel")
    def cancel_mission(mission_id: str, request: Request, repo: str | None = None):
        try:
            return request.app.state.mission_service.cancel(repo, mission_id)
        except MissionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Mission {mission_id} not found") from exc

    @app.get("/api/missions/{mission_id}/events")
    async def mission_events(mission_id: str, request: Request, repo: str | None = None, after_id: int | None = None):
        try:
            snapshot = request.app.state.mission_service.snapshot(repo, mission_id)
        except MissionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Mission {mission_id} not found") from exc
        last_event_id = request.headers.get("last-event-id")
        last_seen = after_id if after_id is not None else int(last_event_id) if last_event_id and last_event_id.isdigit() else snapshot.latest_event_id

        async def event_generator():
            nonlocal last_seen
            resolved_repo = request.app.state.mission_service.resolve_repo(mission_id, repo)
            paths = build_mission_paths(resolved_repo, mission_id)
            migrate_legacy_mission(paths, mission_id)
            store = MissionStore(paths.db_path)
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    rows = store.fetch_events_after(mission_id, last_seen)
                    for row in rows:
                        payload = json.loads(row["payload_json"])
                        last_seen = row["id"]
                        yield {"id": str(row["id"]), "event": row["event_type"], "data": json.dumps(payload)}
                    control = store.fetch_control_state(mission_id)
                    if not rows and control and control["run_state"] == RunState.FINALIZED.value:
                        break
                    await asyncio.sleep(0.5)
            finally:
                store.close()

        return EventSourceResponse(event_generator())

    @app.get("/api/missions/{mission_id}/trace")
    def mission_trace(mission_id: str, request: Request, repo: str | None = None, after_id: int = 0, limit: int = 200):
        try:
            resolved_repo = request.app.state.mission_service.resolve_repo(mission_id, repo)
        except MissionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Mission {mission_id} not found") from exc
        paths = build_mission_paths(resolved_repo, mission_id)
        migrate_legacy_mission(paths, mission_id)
        store = MissionStore(paths.db_path)
        try:
            rows = store.fetch_trace_entries(mission_id, limit=min(limit, 500), after_id=after_id)
            return [
                {
                    "id": row["id"],
                    "trace_type": row["trace_type"],
                    "title": row["title"],
                    "message": row["message"],
                    "status": row["status"],
                    "task_id": row["task_id"],
                    "bid_id": row["bid_id"],
                    "provider": row["provider"],
                    "lane": row["lane"],
                    "payload": json.loads(row["payload_json"]).get("payload", {}),
                    "created_at": row["created_at"],
                }
                for row in rows
            ]
        finally:
            store.close()

    @app.get("/api/missions/{mission_id}/diff")
    def mission_diff(mission_id: str, request: Request, repo: str | None = None):
        try:
            resolved_repo = request.app.state.mission_service.resolve_repo(mission_id, repo)
        except MissionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Mission {mission_id} not found") from exc
        paths = build_mission_paths(resolved_repo, mission_id)
        migrate_legacy_mission(paths, mission_id)
        store = MissionStore(paths.db_path)
        try:
            view = store.get_mission_view(mission_id)
            return {
                "mission_id": mission_id,
                "repo_path": resolved_repo,
                "branch_name": view.get("branch_name"),
                "head_commit": view.get("head_commit"),
                "worktree_state": view.get("worktree_state", {}),
                "accepted_checkpoint": view.get("accepted_checkpoints", [])[-1] if view.get("accepted_checkpoints") else None,
            }
        finally:
            store.close()

    @app.get("/api/missions/{mission_id}/usage")
    def mission_usage(mission_id: str, request: Request, repo: str | None = None):
        try:
            resolved_repo = request.app.state.mission_service.resolve_repo(mission_id, repo)
        except MissionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Mission {mission_id} not found") from exc
        paths = build_mission_paths(resolved_repo, mission_id)
        migrate_legacy_mission(paths, mission_id)
        store = MissionStore(paths.db_path)
        try:
            return store.get_mission_view(mission_id).get("usage_summary", {})
        finally:
            store.close()

    dist_dir = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    assets_dir = dist_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/")
    def root():
        if (dist_dir / "index.html").exists():
            return FileResponse(dist_dir / "index.html")
        return JSONResponse({"message": "Frontend not built yet. Run the Vite dev server or build the frontend."})

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        if (dist_dir / "index.html").exists():
            return FileResponse(dist_dir / "index.html")
        return JSONResponse({"message": "Frontend not built yet. Run the Vite dev server or build the frontend."})

    return app
