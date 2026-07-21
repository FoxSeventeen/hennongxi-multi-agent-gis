import { useEffect, useState } from "react";

import type { AcceptedTask, MasterClient, ReadinessSnapshot } from "../api/client";
import { MapWorkspace } from "../components/MapWorkspace";
import { ReadinessPanel } from "../components/ReadinessPanel";
import { TaskComposer } from "../components/TaskComposer";
import { ResultPanel } from "../features/results/ResultPanel";
import { TaskTimelineView } from "../features/timeline/TaskTimeline";
import { useTaskTimeline } from "../features/timeline/useTaskTimeline";
import "./app.css";
import {
  clearTaskLocation,
  pushTaskLocation,
  readTaskLocation,
  type TaskLocationState,
} from "./task-location";

interface AppProps {
  readonly client: MasterClient;
  readonly publisherBaseUrl?: string;
}

type ReadinessState =
  | { readonly phase: "loading" }
  | { readonly phase: "ready"; readonly snapshot: ReadinessSnapshot }
  | { readonly phase: "error" };

export function App({ client, publisherBaseUrl = "http://localhost:8004" }: AppProps) {
  const [refreshIndex, setRefreshIndex] = useState(0);
  const [readinessState, setReadinessState] = useState<ReadinessState>({ phase: "loading" });
  const [taskLocation, setTaskLocation] = useState<TaskLocationState>(() =>
    readTaskLocation(window.location.search),
  );
  const timelineState = useTaskTimeline(client, taskLocation.taskId);

  useEffect(() => {
    let active = true;
    setReadinessState({ phase: "loading" });
    void client
      .getReadiness()
      .then((snapshot) => {
        if (active) {
          setReadinessState({ phase: "ready", snapshot });
        }
      })
      .catch(() => {
        if (active) {
          setReadinessState({ phase: "error" });
        }
      });

    return () => {
      active = false;
    };
  }, [client, refreshIndex]);

  useEffect(() => {
    function handlePopState(): void {
      setTaskLocation(readTaskLocation(window.location.search));
    }
    window.addEventListener("popstate", handlePopState);
    return () => {
      window.removeEventListener("popstate", handlePopState);
    };
  }, []);

  const environmentLabel =
    readinessState.phase === "ready" && readinessState.snapshot.ready ? "环境可用" : "检查环境";
  const canSubmit = readinessState.phase === "ready" && readinessState.snapshot.ready;
  const disabledReason = getSubmissionDisabledReason(readinessState);

  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <p className="app-kicker">多 Agent · 遥感变化分析</p>
          <h1>神农溪生态监测指挥台</h1>
        </div>
        <div className="environment-chip" aria-label={`系统环境：${environmentLabel}`}>
          <span aria-hidden="true" />
          {environmentLabel}
        </div>
      </header>

      <main className="workspace-layout">
        <MapWorkspace
          activeTaskId={taskLocation.taskId}
          publication={timelineState.snapshot?.publication ?? null}
          publisherBaseUrl={publisherBaseUrl}
        />
        <aside className="control-rail" aria-label="任务与状态控制区">
          {taskLocation.invalid ? (
            <TaskLocationError
              onClear={() => {
                clearTaskLocation();
                setTaskLocation({ taskId: null, invalid: false });
              }}
            />
          ) : taskLocation.taskId === null ? null : (
            <>
              <TaskTimelineView state={timelineState} />
              <ResultPanel
                snapshot={timelineState.snapshot}
                publisherBaseUrl={publisherBaseUrl}
                client={client}
                onRetryAccepted={() => {
                  timelineState.retry();
                }}
              />
            </>
          )}
          <TaskComposer
            client={client}
            canSubmit={canSubmit}
            disabledReason={disabledReason}
            onAccepted={(task) => {
              selectAcceptedTask(task, setTaskLocation);
            }}
          />
          <ReadinessPanel
            state={readinessState}
            onRetry={() => {
              setRefreshIndex((current) => current + 1);
            }}
          />
        </aside>
      </main>
    </div>
  );
}

function getSubmissionDisabledReason(state: ReadinessState): string | undefined {
  if (state.phase === "loading") {
    return "完成系统状态检查后即可创建任务";
  }
  if (state.phase === "error") {
    return "恢复 Master 连接后才可创建任务";
  }
  if (!state.snapshot.ready) {
    return "系统就绪后才可创建任务";
  }
  return undefined;
}

function selectAcceptedTask(
  task: AcceptedTask,
  setTaskLocation: (state: TaskLocationState) => void,
): void {
  pushTaskLocation(task.taskId);
  setTaskLocation({ taskId: task.taskId, invalid: false });
}

function TaskLocationError({ onClear }: { readonly onClear: () => void }) {
  return (
    <section className="panel-card task-location-error" role="alert">
      <p className="section-kicker">任务地址</p>
      <h2>任务编号格式无效</h2>
      <p>当前地址无法对应到受支持的任务，请返回新建任务。</p>
      <button className="secondary-button" type="button" onClick={onClear}>
        返回新建任务
      </button>
    </section>
  );
}
