import MissionGraph from "./MissionGraph";
import StatusBadge from "./StatusBadge";
import { summarizeProvider } from "../lib/format";

function summarizeTask(task, bids, executionSteps) {
  const taskBids = bids.filter((bid) => bid.task_id === task.task_id);
  const latestBid = [...taskBids].sort((left, right) => (right.score ?? -1) - (left.score ?? -1))[0];
  const touched = executionSteps.some((step) => step.task_id === task.task_id);
  return {
    latestBid,
    touched
  };
}

export default function TaskRail({
  tasks,
  activeTaskId,
  bids,
  executionSteps,
  validationReport,
  winnerBidId,
  standbyBidId
}) {
  const completedCount = tasks.filter((task) => ["complete", "completed"].includes(task.status)).length;
  const runningCount = tasks.filter((task) => task.status === "running").length;
  const failedCount = tasks.filter((task) => task.status === "failed").length;

  return (
    <div className="task-rail">
      <div className="task-rail-head">
        <div>
          <p className="eyebrow">Task Rail</p>
          <h2>Execution ladder</h2>
        </div>
        <span className="panel-meta">{tasks.length} tasks</span>
      </div>
      <div className="task-rail-summary">
        <div className="task-rail-summary-item">
          <strong>{completedCount}</strong>
          <span>done</span>
        </div>
        <div className="task-rail-summary-item">
          <strong>{runningCount}</strong>
          <span>live</span>
        </div>
        <div className="task-rail-summary-item">
          <strong>{failedCount}</strong>
          <span>failed</span>
        </div>
      </div>
      <div className="task-rail-list">
        {tasks.map((task) => {
          const summary = summarizeTask(task, bids, executionSteps);
          const winner = bids.find(
            (bid) => bid.bid_id === winnerBidId && bid.task_id === task.task_id
          );
          const standby = bids.find(
            (bid) => bid.bid_id === standbyBidId && bid.task_id === task.task_id
          );
          const isActive = task.task_id === activeTaskId;
          return (
            <article
              key={task.task_id}
              className={`task-rail-item ${isActive ? "task-rail-item-active" : ""}`}
            >
              <div className="task-rail-item-head">
                <div>
                  <strong>{task.title}</strong>
                  <p>{task.task_id} - {task.task_type}</p>
                </div>
                <StatusBadge value={task.status} quiet />
              </div>
              <div className="task-rail-item-meta">
                <span>{winner ? `Winner ${summarizeProvider(winner.provider || winner.role)}` : "Winner pending"}</span>
                <span>{standby ? `Standby ${summarizeProvider(standby.provider || standby.role)}` : "No standby"}</span>
              </div>
              <div className="task-rail-item-meta">
                <span>
                  {summary.latestBid
                    ? `Top score ${summary.latestBid.score?.toFixed(2) ?? "n/a"}`
                    : "Market pending"}
                </span>
                <span>{summary.touched ? "Repo touched" : "No repo edits yet"}</span>
              </div>
              {validationReport?.task_id === task.task_id ? (
                <p
                  className={`task-rail-validation ${
                    validationReport.passed ? "is-pass" : "is-fail"
                  }`}
                >
                  Latest validation: {validationReport.passed ? "passed" : "failed"}
                </p>
              ) : null}
            </article>
          );
        })}
      </div>
      <details className="task-rail-graph">
        <summary>Dependency mini-map</summary>
        <MissionGraph tasks={tasks} compact />
      </details>
    </div>
  );
}
