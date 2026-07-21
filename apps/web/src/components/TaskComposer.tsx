import { useRef, useState } from "react";

import { MasterApiError, type AcceptedTask, type ErrorDetail, type MasterClient } from "../api/client";
import "./task-composer.css";

interface TaskComposerProps {
  readonly client: MasterClient;
  readonly canSubmit: boolean;
  readonly disabledReason?: string | undefined;
  readonly onAccepted: (task: AcceptedTask) => void;
}

interface DisplayError {
  readonly message: string;
  readonly retryable: boolean;
  readonly details: readonly ErrorDetail[];
}

type SubmissionState =
  | { readonly phase: "idle" }
  | { readonly phase: "submitting" }
  | { readonly phase: "accepted"; readonly task: AcceptedTask }
  | { readonly phase: "error"; readonly error: DisplayError };

export function TaskComposer({ client, canSubmit, disabledReason, onAccepted }: TaskComposerProps) {
  const [query, setQuery] = useState("");
  const [submission, setSubmission] = useState<SubmissionState>({ phase: "idle" });
  const submissionLock = useRef(false);
  const characterCount = Array.from(query).length;
  const isAccepted = submission.phase === "accepted";
  const isSubmitting = submission.phase === "submitting";
  const isInvalid = submission.phase === "error" && !submission.error.retryable;

  async function handleSubmit() {
    if (!canSubmit || submissionLock.current || isAccepted) {
      return;
    }

    const normalizedQuery = query.trim();
    if (normalizedQuery.length === 0) {
      setSubmission({
        phase: "error",
        error: {
          message: "请输入需要分析的生态监测任务。",
          retryable: false,
          details: [],
        },
      });
      return;
    }

    if (Array.from(normalizedQuery).length > 2000) {
      setSubmission({
        phase: "error",
        error: {
          message: "任务描述不能超过 2000 个字符。",
          retryable: false,
          details: [],
        },
      });
      return;
    }

    submissionLock.current = true;
    setSubmission({ phase: "submitting" });
    let task: AcceptedTask;
    try {
      task = await client.createTask(normalizedQuery);
    } catch (reason: unknown) {
      submissionLock.current = false;
      setSubmission({ phase: "error", error: toDisplayError(reason) });
      return;
    }
    setSubmission({ phase: "accepted", task });
    onAccepted(task);
  }

  function handleQueryChange(value: string) {
    setQuery(value);
    if (submission.phase === "error") {
      setSubmission({ phase: "idle" });
    }
  }

  return (
    <section className="panel-card task-composer" aria-labelledby="task-composer-title">
      <div className="panel-heading">
        <p className="section-kicker">任务调度</p>
        <span className="panel-index" aria-hidden="true">
          01
        </span>
      </div>

      <div className="task-introduction">
        <h2 id="task-composer-title">发起生态监测任务</h2>
        <p>用中文描述分析目标，Master 将生成受约束的多 Agent 执行计划。</p>
      </div>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          void handleSubmit();
        }}
        noValidate
      >
        <label htmlFor="task-query">生态监测任务描述</label>
        <textarea
          id="task-query"
          name="query"
          value={query}
          rows={5}
          maxLength={2000}
          placeholder="例如：分析 2023 年与 2024 年神农溪流域的植被变化，并生成质量报告。"
          aria-describedby="task-query-hint task-query-count"
          aria-invalid={isInvalid}
          disabled={isSubmitting || isAccepted}
          onChange={(event) => {
            handleQueryChange(event.currentTarget.value);
          }}
        />
        <div className="task-field-meta">
          <span id="task-query-hint">包含区域、时相与期望输出会更准确</span>
          <span id="task-query-count">{characterCount} / 2000</span>
        </div>

        {!canSubmit && disabledReason ? <p className="task-gate-note">{disabledReason}</p> : null}

        {submission.phase === "error" ? <SubmissionError error={submission.error} /> : null}
        {submission.phase === "accepted" ? <AcceptedTaskNotice task={submission.task} /> : null}

        <button
          className="primary-button"
          type="submit"
          disabled={!canSubmit || isSubmitting || isAccepted}
        >
          {getSubmitLabel(submission)}
        </button>
      </form>
    </section>
  );
}

function SubmissionError({ error }: { readonly error: DisplayError }) {
  return (
    <div className="submission-error" role="alert">
      <strong>{error.message}</strong>
      {error.details.length > 0 ? (
        <ul>
          {error.details.map((detail, index) => (
            <li key={`${String(index)}-${detail.reason}`}>{detail.reason}</li>
          ))}
        </ul>
      ) : null}
      <p>{error.retryable ? "当前问题可以重试。" : "请检查任务描述后再次提交。"}</p>
    </div>
  );
}

function AcceptedTaskNotice({ task }: { readonly task: AcceptedTask }) {
  return (
    <div className="accepted-task" role="status">
      <span aria-hidden="true">✓</span>
      <div>
        <strong>任务已创建</strong>
        <p>任务编号</p>
        <code>{task.taskId}</code>
      </div>
    </div>
  );
}

function getSubmitLabel(state: SubmissionState): string {
  if (state.phase === "submitting") {
    return "正在创建任务";
  }
  if (state.phase === "accepted") {
    return "任务已创建";
  }
  if (state.phase === "error" && state.error.retryable) {
    return "重试创建任务";
  }
  return "创建监测任务";
}

function toDisplayError(reason: unknown): DisplayError {
  if (reason instanceof MasterApiError) {
    return {
      message: reason.message,
      retryable: reason.retryable,
      details: reason.details,
    };
  }
  return {
    message: "创建任务时发生未知错误，请稍后重试。",
    retryable: true,
    details: [],
  };
}
