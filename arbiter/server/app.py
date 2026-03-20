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
from arbiter.runtime.paths import build_mission_paths
from arbiter.runtime.store import MissionStore
from arbiter.server.manager import MissionConflictError, MissionNotFoundError, MissionService
from arbiter.server.schemas import MissionCreateRequest


def create_app(strategy_backend_factory=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service = MissionService(strategy_backend_factory=strategy_backend_factory)
        app.state.mission_service = service
        try:
            yield
        finally:
            service.close()

    app = FastAPI(title="Arbiter Mission Control", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8000", "http://localhost:8000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    service: MissionService = app.state.mission_service

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/api/missions")
    def create_mission(payload: MissionCreateRequest):
        try:
            return service.start(payload)
        except MissionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/missions")
    def list_missions():
        return service.list_history()

    @app.get("/api/missions/{mission_id}")
    def get_mission(mission_id: str):
        try:
            return service.snapshot(mission_id)
        except MissionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Mission {mission_id} not found") from exc

    @app.post("/api/missions/{mission_id}/pause")
    def pause_mission(mission_id: str):
        try:
            return service.pause(mission_id)
        except MissionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Mission {mission_id} not found") from exc

    @app.post("/api/missions/{mission_id}/resume")
    def resume_mission(mission_id: str):
        try:
            return service.resume(mission_id)
        except MissionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except MissionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Mission {mission_id} not found") from exc

    @app.post("/api/missions/{mission_id}/cancel")
    def cancel_mission(mission_id: str):
        try:
            return service.cancel(mission_id)
        except MissionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Mission {mission_id} not found") from exc

    @app.get("/api/missions/{mission_id}/events")
    async def mission_events(mission_id: str, request: Request, after_id: int | None = None):
        try:
            snapshot = service.snapshot(mission_id)
        except MissionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Mission {mission_id} not found") from exc
        last_event_id = request.headers.get("last-event-id")
        if after_id is not None:
            last_seen = after_id
        else:
            last_seen = int(last_event_id) if last_event_id and last_event_id.isdigit() else snapshot.latest_event_id

        async def event_generator():
            nonlocal last_seen
            record = service.registry.get(mission_id)
            assert record is not None
            paths = build_mission_paths(record["repo_path"], mission_id)
            store = MissionStore(paths.db_path)
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    rows = store.fetch_events_after(last_seen)
                    for row in rows:
                        payload = json.loads(row["payload_json"])
                        last_seen = row["id"]
                        yield {
                            "id": str(row["id"]),
                            "event": payload["event_type"],
                            "data": json.dumps(payload),
                        }
                    control = store.fetch_control_state(mission_id)
                    if not rows and control and control["run_state"] == RunState.FINALIZED.value:
                        break
                    await asyncio.sleep(0.5)
            finally:
                store.close()

        return EventSourceResponse(event_generator())

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
