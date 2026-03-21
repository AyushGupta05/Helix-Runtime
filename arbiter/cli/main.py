from __future__ import annotations

import json
import time
from pathlib import Path

import typer
import uvicorn

from arbiter.civic.runtime import CivicRuntime
from arbiter.mission.runner import mission_status, resume_mission, start_mission
from arbiter.repo.collector import RepoStateCollector
from arbiter.runtime.config import load_runtime_config
from arbiter.runtime.migrate import migrate_legacy_mission
from arbiter.runtime.paths import build_mission_paths, resolve_repo_path
from arbiter.runtime.store import MissionStore
from arbiter.server.app import create_app

app = typer.Typer(add_completion=False)
mission_app = typer.Typer(add_completion=False)
civic_app = typer.Typer(add_completion=False)
app.add_typer(mission_app, name="mission")
app.add_typer(civic_app, name="civic")


@mission_app.command("start")
def start(
    repo: str = typer.Option(..., "--repo"),
    objective: str = typer.Option(..., "--objective"),
    constraints: list[str] = typer.Option(None, "--constraint"),
    preferences: list[str] = typer.Option(None, "--preference"),
    requested_skills: list[str] = typer.Option(None, "--requested-skill"),
    max_runtime: int | None = typer.Option(None, "--max-runtime"),
    benchmark_requirement: str | None = typer.Option(None, "--benchmark-requirement"),
    protected_paths: list[str] = typer.Option(None, "--protected-path"),
    public_api_surface: list[str] = typer.Option(None, "--public-api-surface"),
) -> None:
    state = start_mission(
        repo=repo,
        objective=objective,
        constraints=constraints or [],
        preferences=preferences or [],
        requested_skills=requested_skills or [],
        max_runtime=max_runtime,
        benchmark_requirement=benchmark_requirement,
        protected_paths=protected_paths or [],
        public_api_surface=public_api_surface or [],
    )
    print(json.dumps({"mission_id": state.mission.mission_id, "outcome": state.outcome.value if state.outcome else None, "branch": state.summary.branch_name}, indent=2))


@civic_app.command("check")
def civic_check(
    repo: str | None = typer.Option(None, "--repo"),
    objective: str | None = typer.Option(None, "--objective"),
) -> None:
    config = load_runtime_config()
    runtime = CivicRuntime(config)
    repo_snapshot = None
    if repo:
        repo_path = resolve_repo_path(repo)
        repo_snapshot = RepoStateCollector(str(repo_path)).collect(run_commands=False, objective=objective)
    refreshed = runtime.refresh_capability_state(repo_snapshot, force=True)
    payload = {
        "civic_connection": refreshed["connection"].model_dump(mode="json"),
        "civic_capabilities": [capability.model_dump(mode="json") for capability in refreshed["capabilities"]],
        "available_skills": refreshed["available_skills"],
        "skill_health": {
            key: value.model_dump(mode="json")
            for key, value in refreshed["skill_health"].items()
        },
    }
    if repo_snapshot is not None:
        payload["repo_insights"] = {
            "remote_provider": repo_snapshot.remote_provider,
            "remote_slug": repo_snapshot.remote_slug,
            "branch": repo_snapshot.branch,
            "tracking_branch": repo_snapshot.tracking_branch,
            "objective_hints": repo_snapshot.objective_hints,
        }
        if repo_snapshot.remote_provider == "github" and "github_context" in refreshed["available_skills"]:
            preflight_payload = {
                "repo": repo_snapshot.remote_slug,
                "branch": repo_snapshot.branch,
            }
            pr_numbers = repo_snapshot.objective_hints.get("pr_numbers", [])
            if pr_numbers:
                preflight_payload["pr_number"] = pr_numbers[0]
            preflight = runtime.preflight_action(
                mission_id="civic-check",
                task_id="health",
                bid_id=None,
                action_type="fetch_ci_status",
                payload=preflight_payload,
                skill_id="github_context",
            )
            payload["preflight"] = preflight.model_dump(mode="json")
    print(json.dumps(payload, indent=2))


@mission_app.command("resume")
def resume(
    mission_id: str,
    repo: str = typer.Option(..., "--repo"),
) -> None:
    state = resume_mission(mission_id, repo)
    print(json.dumps({"mission_id": state.mission.mission_id, "outcome": state.outcome.value if state.outcome else None, "branch": state.summary.branch_name}, indent=2))


@mission_app.command("status")
def status(
    mission_id: str,
    repo: str = typer.Option(..., "--repo"),
) -> None:
    print(json.dumps(mission_status(mission_id, repo), indent=2))


@mission_app.command("list")
def list_missions(
    repo: str = typer.Option(..., "--repo"),
) -> None:
    repo_path = resolve_repo_path(repo)
    missions_root = repo_path / ".arbiter" / "missions"
    if not missions_root.exists():
        print("[]")
        return
    entries: list[dict[str, object]] = []
    for mission_root in sorted(missions_root.iterdir()):
        if not mission_root.is_dir():
            continue
        mission_id = mission_root.name
        paths = build_mission_paths(str(repo_path), mission_id)
        migrate_legacy_mission(paths, mission_id)
        store = MissionStore(paths.db_path)
        try:
            entries.append(store.get_mission_view(mission_id))
        finally:
            store.close()
    print(json.dumps(entries, indent=2))


@mission_app.command("events")
def events(
    mission_id: str,
    repo: str = typer.Option(..., "--repo"),
    follow: bool = typer.Option(False, "--follow"),
) -> None:
    paths = build_mission_paths(repo, mission_id)
    migrate_legacy_mission(paths, mission_id)
    target = Path(paths.events_path)
    if not target.exists():
        raise typer.BadParameter(f"No events found for mission {mission_id}")
    if not follow:
        print(target.read_text(encoding="utf-8"))
        return
    with target.open("r", encoding="utf-8") as handle:
        handle.seek(0, 2)
        try:
            while True:
                line = handle.readline()
                if line:
                    print(line.rstrip("\n"))
                else:
                    time.sleep(0.25)
        except KeyboardInterrupt:
            return


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
) -> None:
    uvicorn.run(create_app(), host=host, port=port)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
