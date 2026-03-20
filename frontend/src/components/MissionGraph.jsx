import { useMemo } from "react";
import ReactFlow, { Background, Controls, MarkerType } from "reactflow";

import StatusBadge from "./StatusBadge";

function depthFor(task, taskMap, seen = new Set()) {
  if (!task.dependencies.length) {
    return 0;
  }
  if (seen.has(task.task_id)) {
    return 0;
  }
  seen.add(task.task_id);
  return (
    Math.max(
      ...task.dependencies.map((dependency) =>
        depthFor(taskMap.get(dependency), taskMap, new Set(seen))
      )
    ) + 1
  );
}

function TaskLabel({ task }) {
  return (
    <div className={`task-node task-node-${task.status}`}>
      <div className="task-node-head">
        <strong>{task.title}</strong>
        <StatusBadge value={task.status} quiet />
      </div>
      <div className="task-node-meta">
        <span>{task.task_id}</span>
        <span>{task.task_type}</span>
      </div>
    </div>
  );
}

export default function MissionGraph({ tasks, compact = false }) {
  const { nodes, edges } = useMemo(() => {
    const taskMap = new Map(tasks.map((task) => [task.task_id, task]));
    const lanes = new Map();
    const nodes = tasks.map((task) => {
      const depth = depthFor(task, taskMap);
      const laneIndex = lanes.get(depth) ?? 0;
      lanes.set(depth, laneIndex + 1);
      return {
        id: task.task_id,
        position: { x: depth * 320, y: laneIndex * 140 },
        data: { label: <TaskLabel task={task} /> },
        draggable: false,
        selectable: false
      };
    });
    const edges = tasks.flatMap((task) =>
      task.dependencies.map((dependency) => ({
        id: `${dependency}-${task.task_id}`,
        source: dependency,
        target: task.task_id,
        markerEnd: { type: MarkerType.ArrowClosed },
        animated: task.status === "running"
      }))
    );
    return { nodes, edges };
  }, [tasks]);

  return (
    <div className={`graph-shell ${compact ? "graph-shell-compact" : ""}`}>
      <ReactFlow fitView nodes={nodes} edges={edges} proOptions={{ hideAttribution: true }}>
        <Background color="#c0cad5" gap={22} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
