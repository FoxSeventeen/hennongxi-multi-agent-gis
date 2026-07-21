import type { TimelineStage } from "./timeline-model";

const agentLabels = {
  master: "主控 Agent",
  data: "数据 Agent",
  analysis: "分析 Agent",
  quality: "质量 Agent",
  publisher: "发布 Agent",
} as const;

const statusLabels = {
  PENDING: "等待中",
  RUNNING: "执行中",
  COMPLETED: "已完成",
  FAILED: "失败",
  SKIPPED: "已复用",
} as const;

export function TimelineStepList({ stages }: { readonly stages: readonly TimelineStage[] }) {
  return (
    <ol className="timeline-steps" aria-label="Agent 执行步骤">
      {stages.map((stage, index) => (
        <li
          className={`timeline-stage stage-${stage.status.toLowerCase()}`}
          data-testid="timeline-stage"
          key={stage.key}
          aria-current={stage.status === "RUNNING" ? "step" : undefined}
        >
          <div className="stage-marker" aria-hidden="true">
            {stage.status === "COMPLETED" || stage.status === "SKIPPED" ? "✓" : index + 1}
          </div>
          <div className="stage-content">
            <div className="stage-heading">
              <div>
                <span>{agentLabels[stage.agent]}</span>
                <h3>{stage.title}</h3>
              </div>
              <strong>{statusLabels[stage.status]}</strong>
            </div>
            <p className="stage-message">{stage.message}</p>
            <div className="stage-evidence">
              <span>进度 {stage.progress}%</span>
              <span>{formatElapsed(stage.elapsedMs)}</span>
              {stage.occurredAt === null ? null : (
                <time dateTime={stage.occurredAt}>{formatTime(stage.occurredAt)}</time>
              )}
            </div>
            {stage.status === "RUNNING" ? (
              <progress
                className="stage-progress"
                aria-label={`${stage.title}进度`}
                max={100}
                value={stage.progress}
              />
            ) : null}
            {stage.error === null ? null : (
              <div className="stage-error" role="alert">
                <strong>{stage.error.message}</strong>
                {stage.error.details.length === 0 ? null : (
                  <ul>
                    {stage.error.details.map((detail, detailIndex) => (
                      <li key={`${String(detailIndex)}-${detail.reason}`}>{detail.reason}</li>
                    ))}
                  </ul>
                )}
                <span>{stage.error.retryable ? "可以从安全检查点重试" : "此错误不可重试"}</span>
              </div>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}

function formatElapsed(value: number | null): string {
  if (value === null) {
    return "尚未计时";
  }
  if (value < 1_000) {
    return `${String(value)} 毫秒`;
  }
  if (value < 60_000) {
    return `${(value / 1_000).toFixed(1)} 秒`;
  }
  const minutes = Math.floor(value / 60_000);
  const seconds = Math.floor((value % 60_000) / 1_000);
  return `${String(minutes)} 分 ${String(seconds)} 秒`;
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "时间未知";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}
