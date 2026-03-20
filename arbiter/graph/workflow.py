from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class WorkflowState(TypedDict, total=False):
    status: str


def build_workflow(runtime):
    graph = StateGraph(WorkflowState)
    graph.add_node("collect", lambda state: runtime.node_collect())
    graph.add_node("decompose", lambda state: runtime.node_decompose())
    graph.add_node("select_task", lambda state: runtime.node_select_task())
    graph.add_node("market", lambda state: runtime.node_market())
    graph.add_node("execute", lambda state: runtime.node_execute())
    graph.add_node("validate", lambda state: runtime.node_validate())
    graph.add_node("recover", lambda state: runtime.node_recover())
    graph.add_node("finalize", lambda state: runtime.node_finalize())

    graph.add_edge(START, "collect")
    graph.add_edge("collect", "decompose")
    graph.add_edge("decompose", "select_task")
    graph.add_conditional_edges("select_task", lambda state: state["status"], {"market": "market", "finalize": "finalize"})
    graph.add_conditional_edges("market", lambda state: state["status"], {"execute": "execute", "recover": "recover", "finalize": "finalize"})
    graph.add_edge("execute", "validate")
    graph.add_conditional_edges("validate", lambda state: state["status"], {"select_task": "select_task", "recover": "recover", "finalize": "finalize"})
    graph.add_conditional_edges("recover", lambda state: state["status"], {"execute": "execute", "market": "market", "finalize": "finalize", "select_task": "select_task"})
    graph.add_edge("finalize", END)
    return graph.compile()
