from __future__ import annotations

from arbiter.graph.workflow import build_workflow


class _WorkflowStub:
    def __init__(self, execute_status: str) -> None:
        self.execute_status = execute_status
        self.calls: list[str] = []

    def workflow_bootstrap(self, _state):
        self.calls.append("bootstrap")
        return {"status": "collect"}

    def workflow_collect(self, _state):
        self.calls.append("collect")
        return {"status": "strategize"}

    def workflow_strategize(self, _state):
        self.calls.append("strategize")
        return {"status": "simulate"}

    def workflow_simulate(self, _state):
        self.calls.append("simulate")
        return {"status": "select"}

    def workflow_select(self, _state):
        self.calls.append("select")
        return {"status": "execute"}

    def workflow_execute(self, _state):
        self.calls.append("execute")
        return {"status": self.execute_status}

    def workflow_validate(self, _state):
        self.calls.append("validate")
        return {"status": "finalize"}

    def workflow_recover(self, _state):
        self.calls.append("recover")
        return {"status": "finalize"}

    def workflow_finalize(self, _state):
        self.calls.append("finalize")
        return {"status": "done"}


def test_execute_can_route_to_recover() -> None:
    runtime = _WorkflowStub(execute_status="recover")
    workflow = build_workflow(runtime)

    result = workflow.invoke({"status": "collect"})

    assert result["status"] == "done"
    assert runtime.calls == [
        "bootstrap",
        "collect",
        "strategize",
        "simulate",
        "select",
        "execute",
        "recover",
        "finalize",
    ]
