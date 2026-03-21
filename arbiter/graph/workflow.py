from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph


class WorkflowState(TypedDict, total=False):
    status: str
    runtime_state: dict[str, Any]


def build_workflow(runtime, *, checkpointer=None):
    graph = StateGraph(WorkflowState)
    graph.add_node("bootstrap", runtime.workflow_bootstrap)
    graph.add_node("collect", runtime.workflow_collect)
    graph.add_node("decompose", runtime.workflow_decompose)
    graph.add_node("select_task", runtime.workflow_select_task)
    graph.add_node("market", runtime.workflow_market)
    graph.add_node("simulate", runtime.workflow_simulate)
    graph.add_node("select", runtime.workflow_select)
    graph.add_node("execute", runtime.workflow_execute)
    graph.add_node("validate", runtime.workflow_validate)
    graph.add_node("recover", runtime.workflow_recover)
    graph.add_node("finalize", runtime.workflow_finalize)

    graph.add_edge(START, "bootstrap")
    graph.add_conditional_edges(
        "bootstrap",
        lambda state: state["status"],
        {
            "collect": "collect",
            "decompose": "decompose",
            "select_task": "select_task",
            "market": "market",
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
        {"decompose": "decompose", "finalize": "finalize"},
    )
    graph.add_edge("decompose", "select_task")
    graph.add_conditional_edges(
        "select_task",
        lambda state: state["status"],
        {"market": "market", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "market",
        lambda state: state["status"],
        {"simulate": "simulate", "recover": "recover", "finalize": "finalize"},
    )
    graph.add_edge("simulate", "select")
    graph.add_conditional_edges(
        "select",
        lambda state: state["status"],
        {"execute": "execute", "finalize": "finalize"},
    )
    graph.add_edge("execute", "validate")
    graph.add_conditional_edges(
        "validate",
        lambda state: state["status"],
        {"select_task": "select_task", "recover": "recover", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "recover",
        lambda state: state["status"],
        {
            "execute": "execute",
            "market": "market",
            "finalize": "finalize",
            "select_task": "select_task",
        },
    )
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer, name="arbiter_mission_runtime")
