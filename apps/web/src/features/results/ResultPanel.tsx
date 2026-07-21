import type { TaskSnapshot } from "../../api/task-contract";
import { buildResultPresentation, type ResultPlanningEvidence } from "./result-model";
import "./results.css";

interface ResultPanelProps {
  readonly snapshot: TaskSnapshot | null;
  readonly publisherBaseUrl: string;
}

export function ResultPanel({ snapshot, publisherBaseUrl }: ResultPanelProps) {
  const result =
    snapshot === null ? null : buildResultPresentation(snapshot, publisherBaseUrl);

  return (
    <section className="panel-card results-panel" aria-labelledby="results-title">
      <div className="panel-heading">
        <p className="section-kicker">成果证据</p>
        <span className="panel-index" aria-hidden="true">
          03
        </span>
      </div>
      <h2 id="results-title">监测成果与质量</h2>
      {result === null ? (
        <div className="results-state" role="status">
          正在读取本次任务的成果证据…
        </div>
      ) : result.status === "incomplete" ? (
        <div className="results-state" role="status">
          {result.message}
        </div>
      ) : result.status === "unavailable" ? (
        <div className="results-state results-unavailable" role="alert">
          {result.message}
        </div>
      ) : (
        <ResultDetails presentation={result.presentation} />
      )}
    </section>
  );
}

function ResultDetails({
  presentation,
}: {
  readonly presentation: Extract<
    ReturnType<typeof buildResultPresentation>,
    { readonly status: "ready" }
  >["presentation"];
}) {
  return (
    <>
      <div className="observation-period" aria-label="监测观测日期">
        <div>
          <span>前期观测</span>
          <strong>{presentation.beforeDate}</strong>
        </div>
        <span aria-hidden="true">→</span>
        <div>
          <span>后期观测</span>
          <strong>{presentation.afterDate}</strong>
        </div>
      </div>

      <section className="result-section" aria-labelledby="statistics-title">
        <div className="result-section-heading">
          <h3 id="statistics-title">NDVI 变化面积</h3>
          <span>单位：{presentation.units}</span>
        </div>
        <dl className="statistics-grid" role="group" aria-label="NDVI 变化面积统计">
          <Statistic label="增加" value={presentation.statistics.increaseHectares} tone="up" />
          <Statistic label="稳定" value={presentation.statistics.stableHectares} tone="stable" />
          <Statistic label="减少" value={presentation.statistics.decreaseHectares} tone="down" />
          <Statistic label="有效面积" value={presentation.statistics.validHectares} tone="valid" />
        </dl>
      </section>

      <section className="result-section" aria-labelledby="quality-title">
        <div className="result-section-heading">
          <h3 id="quality-title">独立质量评价</h3>
          <strong className="quality-pass">质量结论：通过</strong>
        </div>
        <dl className="quality-grid" role="group" aria-label="四项质量指标">
          <QualityMetric
            label="流域覆盖率"
            value={formatPercent(presentation.quality.coverageRatio)}
          />
          <QualityMetric
            label="有效像元率"
            value={formatPercent(presentation.quality.validPixelRatio)}
          />
          <QualityMetric
            label="输出完整性"
            value={presentation.quality.outputComplete ? "完整" : "不完整"}
          />
          <QualityMetric
            label="分析耗时"
            value={formatElapsed(presentation.quality.elapsedMs)}
          />
        </dl>
      </section>

      <PlanningEvidence evidence={presentation.planning} />

      <div className="report-row">
        <div>
          <strong>中文监测摘要报告</strong>
          <span>
            {formatBytes(presentation.report.byteSize)} · 校验和 {presentation.report.checksumSha256.slice(0, 12)}…
          </span>
        </div>
        <a href={presentation.report.url}>下载中文 PDF 报告</a>
      </div>
    </>
  );
}

function Statistic({
  label,
  value,
  tone,
}: {
  readonly label: string;
  readonly value: number;
  readonly tone: "up" | "stable" | "down" | "valid";
}) {
  return (
    <div className={`statistic statistic-${tone}`}>
      <dt>{label}</dt>
      <dd>{value.toFixed(2)} 公顷</dd>
    </div>
  );
}

function QualityMetric({ label, value }: { readonly label: string; readonly value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function PlanningEvidence({ evidence }: { readonly evidence: ResultPlanningEvidence }) {
  const tokenEvidence =
    evidence.inputTokens === null || evidence.outputTokens === null
      ? "Token 用量未提供"
      : `输入 ${String(evidence.inputTokens)} / 输出 ${String(evidence.outputTokens)} tokens`;
  return (
    <section
      className={`planning-evidence ${evidence.isAcceptanceEvidence ? "planning-real" : "planning-recovery"}`}
      aria-labelledby="planning-evidence-title"
    >
      <div>
        <span>规划来源</span>
        <h3 id="planning-evidence-title">{evidence.label}</h3>
      </div>
      {evidence.model === null ? null : <strong>{evidence.model}</strong>}
      <p>
        {evidence.durationMs === null ? "调用耗时未记录" : `${String(evidence.durationMs)} 毫秒`} · {tokenEvidence}
      </p>
      {evidence.isAcceptanceEvidence ? (
        <small>已记录成功模型调用与响应摘要，可作为真实规划证据。</small>
      ) : (
        <small>这是显式恢复方案，不作为真实大模型验收证据。</small>
      )}
    </section>
  );
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(2)}%`;
}

function formatElapsed(elapsedMs: number): string {
  return elapsedMs < 1_000 ? `${String(elapsedMs)} 毫秒` : `${(elapsedMs / 1_000).toFixed(2)} 秒`;
}

function formatBytes(byteSize: number): string {
  return byteSize < 1_024 ? `${String(byteSize)} B` : `${(byteSize / 1_024).toFixed(1)} KB`;
}
