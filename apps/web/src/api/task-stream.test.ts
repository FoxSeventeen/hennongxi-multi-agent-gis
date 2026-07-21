import { describe, expect, it, vi } from "vitest";

import { MasterApiError, createMasterClient } from "./client";

const baseUrl = "http://master.test";
const taskId = "4f09fc09-6bd2-49fb-9636-7f4fb93baa44";
const correlationId = "f399c36a-6b76-4db5-a831-ebf6a170edf1";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function streamResponse(chunks: readonly string[]): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) {
          controller.enqueue(encoder.encode(chunk));
        }
        controller.close();
      },
    }),
    { headers: { "content-type": "text/event-stream; charset=utf-8" } },
  );
}

function taskEvent(sequence: number, status = "PLANNING") {
  return {
    schema_version: "1.0",
    sequence,
    task_id: taskId,
    step_id: "planning",
    attempt: 1,
    correlation_id: correlationId,
    agent: "master",
    status,
    progress: status === "COMPLETED" ? 100 : 5,
    message: status === "COMPLETED" ? "任务执行完成" : "正在生成执行计划",
    elapsed_ms: 42,
    occurred_at: "2024-08-12T08:30:01Z",
    error: null,
    artifacts: [],
  };
}

describe("task polling client", () => {
  it("maps the approved task, plan, and step fields to component-safe names", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({
        schema_version: "1.0",
        task_id: taskId,
        query: "分析神农溪植被变化",
        status: "DATA_PREPARING",
        progress: 10,
        current_attempt: 1,
        correlation_id: correlationId,
        created_at: "2024-08-12T08:30:00Z",
        updated_at: "2024-08-12T08:30:02Z",
        plan: {
          schema_version: "1.0",
          plan_id: "354da501-f92e-432d-8367-c845c16d6a07",
          task_id: taskId,
          source: "REAL_LLM",
          created_at: "2024-08-12T08:30:01Z",
          model_call: {
            schema_version: "1.0",
            model: "claude-test",
            started_at: "2024-08-12T08:30:00Z",
            duration_ms: 600,
            status: "SUCCEEDED",
            input_tokens: 128,
            output_tokens: 256,
            response_sha256: "a".repeat(64),
            error_code: null,
          },
          steps: [
            {
              schema_version: "1.0",
              step_id: "prepare_data",
              kind: "prepare_data",
              agent: "data",
              order: 1,
              title: "准备神农溪流域数据",
              depends_on: [],
            },
          ],
        },
        steps: [
          {
            schema_version: "1.0",
            step_id: "prepare_data",
            kind: "prepare_data",
            agent: "data",
            attempt: 1,
            status: "RUNNING",
            progress: 15,
            started_at: "2024-08-12T08:30:02Z",
            completed_at: null,
            elapsed_ms: 300,
            error: null,
            artifacts: [],
          },
        ],
        artifacts: [],
        last_error: null,
      }),
    );
    const client = createMasterClient({ baseUrl, fetcher });

    await expect(client.getTask(taskId)).resolves.toMatchObject({
      taskId,
      query: "分析神农溪植被变化",
      status: "DATA_PREPARING",
      progress: 10,
      currentAttempt: 1,
      correlationId,
      plan: {
        source: "REAL_LLM",
        steps: [
          {
            stepId: "prepare_data",
            kind: "prepare_data",
            agent: "data",
            order: 1,
            title: "准备神农溪流域数据",
            dependsOn: [],
          },
        ],
      },
      steps: [
        {
          stepId: "prepare_data",
          status: "RUNNING",
          progress: 15,
          elapsedMs: 300,
        },
      ],
      lastError: null,
    });
    expect(fetcher).toHaveBeenCalledWith(
      `${baseUrl}/api/v1/tasks/${taskId}`,
      expect.objectContaining({ headers: { accept: "application/json" } }),
    );
  });
});

describe("task SSE client", () => {
  it("parses chunked CRLF frames, ignores heartbeats, and resumes after the durable cursor", async () => {
    const firstEvent = JSON.stringify(taskEvent(8));
    const terminalEvent = JSON.stringify(taskEvent(9, "COMPLETED"));
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      streamResponse([
        ": heartbeat\r\n\r\nid: 8\r\ndata: ",
        `${firstEvent}\r\n\r\nid: 9\ndata: ${terminalEvent.slice(0, 40)}`,
        `${terminalEvent.slice(40)}\n\n`,
      ]),
    );
    const client = createMasterClient({ baseUrl, fetcher });
    const received: unknown[] = [];
    const controller = new AbortController();

    await client.streamTaskEvents(taskId, {
      afterSequence: 7,
      signal: controller.signal,
      onEvent(event) {
        received.push(event);
      },
    });

    expect(received).toMatchObject([
      { sequence: 8, taskId, agent: "master", progress: 5 },
      { sequence: 9, taskId, status: "COMPLETED", progress: 100 },
    ]);
    expect(fetcher).toHaveBeenCalledWith(`${baseUrl}/api/v1/tasks/${taskId}/events`, {
      method: "GET",
      headers: {
        accept: "text/event-stream",
        "Last-Event-ID": "7",
      },
      signal: controller.signal,
    });
  });

  it("rejects an event that belongs to another task or widens the approved contract", async () => {
    const hostileEvent = {
      ...taskEvent(1),
      task_id: "351d2860-817c-4ca3-8f61-606a9e5677d9",
      provider_payload: "不得进入组件",
    };
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(streamResponse([`id: 1\ndata: ${JSON.stringify(hostileEvent)}\n\n`]));
    const client = createMasterClient({ baseUrl, fetcher });

    const error = await client
      .streamTaskEvents(taskId, {
        afterSequence: 0,
        signal: new AbortController().signal,
        onEvent: vi.fn(),
      })
      .catch((reason: unknown) => reason);

    expect(error).toBeInstanceOf(MasterApiError);
    expect(error).toMatchObject({ code: "INTERNAL_ERROR", retryable: true });
  });
});
