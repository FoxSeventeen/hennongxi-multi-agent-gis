import type { ReadinessSnapshot } from "../api/client";

type ReadinessPanelState =
  | { readonly phase: "loading" }
  | { readonly phase: "ready"; readonly snapshot: ReadinessSnapshot }
  | { readonly phase: "error" };

interface ReadinessPanelProps {
  readonly state: ReadinessPanelState;
  readonly onRetry: () => void;
}

const serviceLabels = {
  master: "主控 Agent",
  data: "数据 Agent",
  analysis: "分析 Agent",
  quality: "质检 Agent",
  publisher: "发布服务",
  postgis: "空间数据库",
  redis: "任务队列",
} as const;

const healthLabels = {
  HEALTHY: "正常",
  DEGRADED: "降级",
  UNAVAILABLE: "不可用",
} as const;

const blockerLabels = {
  LLM_NOT_CONFIGURED: "尚未配置大模型访问凭据",
  DATA_NOT_CONFIGURED: "尚未登记完整的分析数据",
  DEPENDENCY_UNAVAILABLE: "至少一项系统依赖当前不可用",
} as const;

export function ReadinessPanel({ state, onRetry }: ReadinessPanelProps) {
  if (state.phase === "loading") {
    return (
      <section className="panel-card readiness-panel" aria-labelledby="readiness-title">
        <PanelHeading />
        <div className="readiness-loading" role="status">
          <span className="loading-mark" aria-hidden="true" />
          <div>
            <strong id="readiness-title">正在检查系统就绪状态</strong>
            <p>正在连接 Master，并核对各 Agent 与基础依赖。</p>
          </div>
        </div>
      </section>
    );
  }

  if (state.phase === "error") {
    return (
      <section className="panel-card readiness-panel" aria-labelledby="readiness-title">
        <PanelHeading />
        <div className="readiness-error" role="alert">
          <strong id="readiness-title">暂时无法读取系统状态</strong>
          <p>Master 连接失败。请确认服务已启动，然后重新检查。</p>
        </div>
        <button className="secondary-button" type="button" onClick={onRetry}>
          重新检查系统状态
        </button>
      </section>
    );
  }

  return <ResolvedReadiness snapshot={state.snapshot} />;
}

function PanelHeading() {
  return (
    <div className="panel-heading" aria-hidden="true">
      <p className="section-kicker">运行环境</p>
      <span className="panel-index">04</span>
    </div>
  );
}

function ResolvedReadiness({ snapshot }: { readonly snapshot: ReadinessSnapshot }) {
  return (
    <section className="panel-card readiness-panel" aria-labelledby="readiness-title">
      <PanelHeading />
      <div className={`readiness-summary ${snapshot.ready ? "is-ready" : "is-blocked"}`}>
        <span className="summary-mark" aria-hidden="true" />
        <div>
          <h2 id="readiness-title">{snapshot.ready ? "系统已就绪" : "需要完成配置"}</h2>
          <p>
            整体健康：{healthLabels[snapshot.health.state]} · 最近检查：
            {formatCheckedTime(snapshot.health.checkedAt)}
          </p>
        </div>
      </div>

      <dl className="configuration-grid" aria-label="配置状态">
        <div>
          <dt>大模型配置</dt>
          <dd className={snapshot.llmConfigured ? "positive" : "negative"}>
            {snapshot.llmConfigured ? "已配置" : "待配置"}
          </dd>
        </div>
        <div>
          <dt>分析数据</dt>
          <dd className={snapshot.dataConfigured ? "positive" : "negative"}>
            {snapshot.dataConfigured ? "已登记" : "待登记"}
          </dd>
        </div>
      </dl>

      {snapshot.blockers.length > 0 ? (
        <div className="blocker-list" aria-label="当前阻塞项">
          <h3>开始任务前需处理</h3>
          <ul>
            {snapshot.blockers.map((blocker) => (
              <li key={blocker}>{blockerLabels[blocker]}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {snapshot.messages.length > 0 ? (
        <ul className="readiness-messages" aria-label="系统就绪说明">
          {snapshot.messages.map((message, index) => (
            <li key={`${String(index)}-${message}`}>{message}</li>
          ))}
        </ul>
      ) : null}

      <div className="service-section">
        <div className="service-heading">
          <h3>Agent 与依赖</h3>
          <span>{snapshot.health.services.length} 项</span>
        </div>
        <ul className="service-list">
          {snapshot.health.services.map((service, index) => (
            <li key={`${String(index)}-${service.service}`}>
              <span className={`service-dot state-${service.state.toLowerCase()}`} aria-hidden="true" />
              <span>{serviceLabels[service.service]}</span>
              <strong>{healthLabels[service.state]}</strong>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function formatCheckedTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "刚刚";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}
