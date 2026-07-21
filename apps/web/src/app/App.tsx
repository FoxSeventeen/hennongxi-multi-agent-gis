import { useEffect, useState } from "react";

import type { MasterClient, ReadinessSnapshot } from "../api/client";
import { MapWorkspace } from "../components/MapWorkspace";
import { ReadinessPanel } from "../components/ReadinessPanel";
import "./app.css";

interface AppProps {
  readonly client: MasterClient;
}

type ReadinessState =
  | { readonly phase: "loading" }
  | { readonly phase: "ready"; readonly snapshot: ReadinessSnapshot }
  | { readonly phase: "error" };

export function App({ client }: AppProps) {
  const [refreshIndex, setRefreshIndex] = useState(0);
  const [readinessState, setReadinessState] = useState<ReadinessState>({ phase: "loading" });

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

  const environmentLabel =
    readinessState.phase === "ready" && readinessState.snapshot.ready ? "环境可用" : "检查环境";

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
        <MapWorkspace />
        <aside className="control-rail" aria-label="任务与状态控制区">
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
