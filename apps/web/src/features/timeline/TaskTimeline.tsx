import type { MasterClient } from "../../api/client";
import { TimelineStepList } from "./TimelineStepList";
import { buildTimelineStages } from "./timeline-model";
import { useTaskTimeline, type TaskTimelineState, type TimelineConnection } from "./useTaskTimeline";
import "./timeline.css";

interface TaskTimelineProps {
  readonly client: MasterClient;
  readonly taskId: string;
}

const taskStatusLabels = {
  PENDING: "任务已排队",
  PLANNING: "正在规划",
  DATA_PREPARING: "正在准备数据",
  ANALYZING: "正在分析",
  QUALITY_CHECKING: "正在质量评价",
  PUBLISHING: "正在发布",
  COMPLETED: "任务已完成",
  FAILED: "任务失败",
} as const;

export function TaskTimeline({ client, taskId }: TaskTimelineProps) {
  return <TaskTimelineView state={useTaskTimeline(client, taskId)} />;
}

export function TaskTimelineView({ state }: { readonly state: TaskTimelineState }) {
  const { snapshot } = state;
  return (
    <section className="panel-card timeline-panel" aria-labelledby="timeline-title">
      <div className="panel-heading">
        <p className="section-kicker">Agent 时间线</p>
        <span className="panel-index" aria-hidden="true">
          02
        </span>
      </div>
      <div className="timeline-title-row">
        <div>
          <h2 id="timeline-title">Agent 执行时间线</h2>
          <p>同一任务编号串联规划、数据、分析、质量与发布过程。</p>
        </div>
        <span className={`connection-chip connection-${state.connection}`}>
          {getConnectionLabel(state.connection, snapshot?.status)}
        </span>
      </div>

      {snapshot === null ? (
        state.connection === "error" ? (
          <div className="timeline-load-error" role="alert">
            <strong>{state.problem ?? "暂时无法加载任务。"}</strong>
            <button className="secondary-button" type="button" onClick={state.retry}>
              重新加载任务
            </button>
          </div>
        ) : (
          <div className="timeline-loading" role="status" aria-live="polite">
            <span aria-hidden="true" />
            <p>正在读取任务计划与持久化进度…</p>
          </div>
        )
      ) : (
        <>
          <div className="task-identity">
            <span>任务编号</span>
            <code>{snapshot.taskId}</code>
          </div>
          <div className="timeline-summary">
            <div>
              <strong>{taskStatusLabels[snapshot.status]}</strong>
              <span>第 {snapshot.currentAttempt} 次执行</span>
            </div>
            <div>
              <span>{snapshot.plan?.source === "REAL_LLM" ? "真实大模型计划" : "内置恢复计划"}</span>
              <strong>{snapshot.progress}%</strong>
            </div>
          </div>
          <progress
            className="timeline-progress"
            aria-label="任务总进度"
            max={100}
            value={snapshot.progress}
          />
          {state.problem === null ? null : (
            <div className="timeline-connection-note" role="status">
              {state.problem}
            </div>
          )}
          {state.connection === "error" ? (
            <button className="secondary-button" type="button" onClick={state.retry}>
              重新加载任务
            </button>
          ) : null}
          <TimelineStepList stages={buildTimelineStages(snapshot, state.events)} />
        </>
      )}
    </section>
  );
}

function getConnectionLabel(
  connection: TimelineConnection,
  taskStatus: keyof typeof taskStatusLabels | undefined,
): string {
  if (connection === "loading") {
    return "正在加载";
  }
  if (connection === "streaming") {
    return "实时连接";
  }
  if (connection === "polling") {
    return "轮询恢复";
  }
  if (connection === "reconnecting") {
    return "正在重连";
  }
  if (connection === "error") {
    return "连接异常";
  }
  return taskStatus === "COMPLETED" ? "已完成" : "已结束";
}
