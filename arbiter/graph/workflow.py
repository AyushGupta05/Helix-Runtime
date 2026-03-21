from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph


class WorkflowState(TypedDict, total=False):
    status: str
    runtime_state: dict[str, Any]


_LEGACY_PHASE_MAP = {
    "decompose": "strategize",
    "select_task": "strategize",
    "market": "strategize",
}


def build_workflow(runtime, *, checkpointer=None):
    graph = StateGraph(WorkflowState)
    graph.add_node("bootstrap", runtime.workflow_bootstrap)
    graph.add_node("collect", runtime.workflow_collect)
    graph.add_node("strategize", runtime.workflow_strategize)
    graph.add_node("simulate", runtime.workflow_simulate)
    graph.add_node("select", runtime.workflow_select)
    graph.add_node("execute", runtime.workflow_execute)
    graph.add_node("validate", runtime.workflow_validate)
    graph.add_node("recover", runtime.workflow_recover)
    graph.add_node("finalize", runtime.workflow_finalize)

    graph.add_edge(START, "bootstrap")
    graph.add_conditional_edges(
        "bootstrap",
        lambda state: _LEGACY_PHASE_MAP.get(state["status"], state["status"]),
        {
            "collect": "collect",
            "strategize": "strategize",
            "simulate": "simulate",
            "select": "select",
            "execute": "execute",
            "validate": "validate",
            "recover": "recover",
            "finalize": "finalize",
        },
    )
    graph.add_conditional_edges(
        "collect",
        lambda state: state["status"],
        {"strategize": "strategize", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "strategize",
        lambda state: state["status"],
        {"simulate": "simulate", "recover": "recover", "finalize": "finalize"},
    )
    graph.add_edge("simulate", "select")
    graph.add_conditional_edges(
        "select",
        lambda state: state["status"],
        {"execute": "execute", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "execute",
        lambda state: state["status"],
        {"validate": "validate", "recover": "recover", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "validate",
        lambda state: state["status"],
        {"strategize": "strategize", "recover": "recover", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "recover",
        lambda state: state["status"],
        {
            "execute": "execute",
            "strategize": "strategize",
            "finalize": "finalize",
        },
    )
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer, name="arbiter_mission_runtime")
