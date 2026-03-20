from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from arbiter.core.contracts import Bid, ReplayRecord, TaskNode
from arbiter.runtime.config import RuntimeConfig
from arbiter.runtime.replay import ReplayManager


class FileUpdate(BaseModel):
    path: str
    content: str


class EditProposal(BaseModel):
    summary: str
    files: list[FileUpdate] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ModelInvocationResult(BaseModel):
    content: str
    token_usage: dict[str, int] = Field(default_factory=dict)
    cost_usage: dict[str, float] = Field(default_factory=dict)


class BedrockModelRouter:
    def __init__(self, config: RuntimeConfig, replay: ReplayManager) -> None:
        self.config = config
        self.replay = replay
        self._models: dict[str, Any] = {}

    def _get_model(self, lane: str):
        lane_config = self.config.model_lanes[lane]
        if lane not in self._models:
            if lane_config.provider != "bedrock":
                raise ValueError(f"Unsupported active provider for V1: {lane_config.provider}")
            if self.config.bedrock_profile:
                os.environ["AWS_PROFILE"] = self.config.bedrock_profile
            from langchain_aws import ChatBedrockConverse

            kwargs = {"model_id": lane_config.model_id, "region_name": self.config.bedrock_region, "temperature": lane_config.temperature}
            if self.config.bedrock_access_key_id:
                kwargs["aws_access_key_id"] = self.config.bedrock_access_key_id
            if self.config.bedrock_secret_access_key:
                kwargs["aws_secret_access_key"] = self.config.bedrock_secret_access_key
            if self.config.bedrock_session_token:
                kwargs["aws_session_token"] = self.config.bedrock_session_token
            self._models[lane] = ChatBedrockConverse(**kwargs)
        return self._models[lane]

    def invoke(self, lane: str, prompt: dict[str, Any]) -> ModelInvocationResult:
        if self.replay.mode == "replay":
            recorded = self.replay.load(prompt)
            if recorded:
                return ModelInvocationResult.model_validate(recorded)
        model = self._get_model(lane)
        messages = [
            ("system", prompt["system"]),
            ("human", prompt["user"]),
        ]
        response = model.invoke(messages)
        content = response.content if isinstance(response.content, str) else json.dumps(response.content)
        result = ModelInvocationResult(
            content=content,
            token_usage=getattr(response, "usage_metadata", {}) or {},
            cost_usage={},
        )
        if self.replay.mode in {"record", "off"}:
            self.replay.record(lane=lane, prompt=prompt, response=result.model_dump())
        return result


class DefaultStrategyBackend:
    def __init__(self, router: BedrockModelRouter) -> None:
        self.router = router

    def generate_edit_proposal(
        self,
        task: TaskNode,
        bid: Bid,
        mission_objective: str,
        candidate_files: dict[str, str],
        failure_context: str | None = None,
    ) -> tuple[EditProposal, ModelInvocationResult]:
        if not candidate_files:
            return EditProposal(summary="No candidate files available.", files=[], notes=["no_candidate_files"]), ModelInvocationResult(content="{}", token_usage={}, cost_usage={})
        lane = "test_gen" if task.task_type.value == "test" else "perf_reason" if "perf" in task.task_type.value else "bid_deep"
        system = (
            "You are Arbiter's execution planner. Return only valid JSON with fields: summary, files, notes. "
            "Each file entry must contain path and full replacement content. Keep edits minimal and respect the bid strategy."
        )
        file_blob = "\n\n".join(
            f"FILE: {path}\n```\n{content[:16000]}\n```"
            for path, content in candidate_files.items()
        )
        user = (
            f"Objective: {mission_objective}\n"
            f"Task: {task.title}\n"
            f"Strategy: {bid.strategy_summary}\n"
            f"Exact action: {bid.exact_action}\n"
            f"Failure context: {failure_context or 'none'}\n"
            f"Candidate files:\n{file_blob}\n"
            "Return JSON only."
        )
        result = self.router.invoke(lane=lane, prompt={"system": system, "user": user})
        proposal = self._parse_edit_proposal(result.content)
        return proposal, result

    @staticmethod
    def _parse_edit_proposal(text: str) -> EditProposal:
        cleaned = text.strip()
        if "```" in cleaned:
            cleaned = cleaned.split("```")[-2].replace("json", "").strip()
        data = json.loads(cleaned)
        return EditProposal.model_validate(data)


class ScriptedStrategyBackend(DefaultStrategyBackend):
    def __init__(self, scripted: list[EditProposal]) -> None:
        self.scripted = scripted
        self.index = 0

    def generate_edit_proposal(
        self,
        task: TaskNode,
        bid: Bid,
        mission_objective: str,
        candidate_files: dict[str, str],
        failure_context: str | None = None,
    ) -> tuple[EditProposal, ModelInvocationResult]:
        proposal = self.scripted[self.index]
        self.index = min(self.index + 1, len(self.scripted) - 1)
        return proposal, ModelInvocationResult(content=proposal.model_dump_json(), token_usage={"input_tokens": 0, "output_tokens": 0}, cost_usage={"usd": 0.0})


def load_candidate_files(repo_path: str, files: list[str]) -> dict[str, str]:
    repo = Path(repo_path)
    loaded: dict[str, str] = {}
    for relative in files:
        path = repo / relative
        if path.exists() and path.is_file():
            loaded[relative] = path.read_text(encoding="utf-8", errors="ignore")
    return loaded

