from __future__ import annotations

import json
import time
from pathlib import Path

import typer

from arbiter.mission.runner import mission_status, resume_mission, start_mission
from arbiter.runtime.paths import build_mission_paths

app = typer.Typer(add_completion=False)
mission_app = typer.Typer(add_completion=False)
app.add_typer(mission_app, name="mission")


@mission_app.command("start")
def start(
    repo: str = typer.Option(..., "--repo"),
    objective: str = typer.Option(..., "--objective"),
    constraints: list[str] = typer.Option(None, "--constraint"),
    preferences: list[str] = typer.Option(None, "--preference"),
    max_runtime: int = typer.Option(10, "--max-runtime"),
    benchmark_requirement: str | None = typer.Option(None, "--benchmark-requirement"),
    protected_paths: list[str] = typer.Option(None, "--protected-path"),
    public_api_surface: list[str] = typer.Option(None, "--public-api-surface"),
) -> None:
    state = start_mission(
        repo=repo,
        objective=objective,
        constraints=constraints or [],
        preferences=preferences or [],
        max_runtime=max_runtime,
        benchmark_requirement=benchmark_requirement,
        protected_paths=protected_paths or [],
        public_api_surface=public_api_surface or [],
    )
    print(json.dumps({"mission_id": state.mission.mission_id, "outcome": state.outcome.value if state.outcome else None, "branch": state.summary.branch_name}, indent=2))


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


@mission_app.command("events")
def events(
    mission_id: str,
    repo: str = typer.Option(..., "--repo"),
    follow: bool = typer.Option(False, "--follow"),
) -> None:
    paths = build_mission_paths(repo, mission_id)
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


def main() -> None:
    app()


if __name__ == "__main__":
    main()

