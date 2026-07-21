import { useEffect, useRef, useState } from "react";

import { MasterApiError, type AcceptedRetry, type MasterClient } from "../../api/client";
import type { AgentName, TaskSnapshot } from "../../api/task-contract";

interface RetryControlProps {
  readonly snapshot: TaskSnapshot;
  readonly client: MasterClient;
  readonly onAccepted: (retry: AcceptedRetry) => void;
}

type RetryState =
  | { readonly phase: "idle" }
  | { readonly phase: "submitting" }
  | { readonly phase: "accepted"; readonly retry: AcceptedRetry }
  | { readonly phase: "error"; readonly message: string };

const agentLabels: Record<AgentName, string> = {
  master: "Master Agent",
  data: "数据 Agent",
  analysis: "分析 Agent",
  quality: "质量 Agent",
  publisher: "发布 Agent",
};

export function RetryControl({ snapshot, client, onAccepted }: RetryControlProps) {
  const [state, setState] = useState<RetryState>({ phase: "idle" });
  const requestLocked = useRef(false);
  const failedStep = snapshot.steps.find(
    (step) => step.attempt === snapshot.currentAttempt && step.status === "FAILED",
  );
  const failure = failedStep?.error ?? snapshot.lastError;
  const planStep = snapshot.plan?.steps.find((step) => step.stepId === failedStep?.stepId);
  const canRetry =
    snapshot.status === "FAILED" &&
    snapshot.lastError?.retryable === true &&
    failure?.retryable === true;

  useEffect(() => {
    requestLocked.current = false;
    setState({ phase: "idle" });
  }, [snapshot.currentAttempt, snapshot.status, snapshot.taskId]);

  async function retry(): Promise<void> {
    if (!canRetry || requestLocked.current) {
      return;
    }
    requestLocked.current = true;
    setState({ phase: "submitting" });
    try {
      const accepted = await client.retryTask(snapshot.taskId);
      setState({ phase: "accepted", retry: accepted });
      onAccepted(accepted);
    } catch (reason: unknown) {
      requestLocked.current = false;
      setState({ phase: "error", message: retryErrorMessage(reason) });
    }
  }

  return (
    <div className="retry-control">
      <div className="failure-evidence" role="alert">
        <div className="failure-heading">
          <span>责任步骤</span>
          <strong>
            {failedStep === undefined ? "Master Agent" : agentLabels[failedStep.agent]}
          </strong>
        </div>
        <h3>{planStep?.title ?? "任务编排与计划生成"}</h3>
        <p>{failure?.message ?? "本次执行失败，尚未提供结构化错误信息。"}</p>
        {failure === null || failure.details.length === 0 ? null : (
          <ul>
            {failure.details.map((detail) => (
              <li key={`${detail.field ?? "task"}-${detail.reason}`}>
                {detail.field === null ? detail.reason : `${detail.field}：${detail.reason}`}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="retry-status" role="status" aria-live="polite">
        {retryStatusMessage(state)}
      </div>
      {state.phase === "error" ? <p className="retry-error">{state.message}</p> : null}
      {canRetry ? (
        <button
          className="retry-button"
          type="button"
          disabled={state.phase === "submitting" || state.phase === "accepted"}
          onClick={() => {
            void retry();
          }}
        >
          {state.phase === "submitting" ? "正在提交重试…" : "重试失败任务"}
        </button>
      ) : (
        <p className="retry-locked">此错误不能从界面安全重试。</p>
      )}
    </div>
  );
}

function retryStatusMessage(state: RetryState): string {
  if (state.phase === "submitting") {
    return "正在创建安全的后续执行尝试…";
  }
  if (state.phase === "accepted") {
    return `已接受第 ${String(state.retry.attempt)} 次执行，正在重新连接任务时间线。`;
  }
  if (state.phase === "error") {
    return "重试尚未被接受，原失败记录保持不变。";
  }
  return "本次执行未完成，不展示完整成果。";
}

function retryErrorMessage(reason: unknown): string {
  return reason instanceof MasterApiError ? reason.message : "暂时无法提交重试，请稍后再试。";
}
