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

function publisherPublication() {
  const tileTypes = [
    "NDVI_BEFORE",
    "NDVI_AFTER",
    "NDVI_DIFFERENCE",
    "CHANGE_CLASSIFICATION",
  ] as const;
  const artifactIds = [
    "11111111-1111-4111-8111-111111111111",
    "22222222-2222-4222-8222-222222222222",
    "33333333-3333-4333-8333-333333333333",
    "44444444-4444-4444-8444-444444444444",
  ] as const;
  const reportArtifactId = "55555555-5555-4555-8555-555555555555";
  return {
    schema_version: "1.0",
    task_id: taskId,
    step_id: "publish_results",
    attempt: 1,
    correlation_id: correlationId,
    resources: [
      ...tileTypes.map((artifactType, index) => ({
        schema_version: "1.0",
        artifact_id: artifactIds[index],
        tile_template: `/api/v1/tiles/${taskId}/${artifactType}/{z}/{x}/{y}.png`,
        download_path: null,
        tile_metadata: {
          schema_version: "1.0",
          artifact_type: artifactType,
          bounds_wgs84: [110.1, 31.0, 110.6, 31.5],
          start_date: artifactType === "NDVI_AFTER" ? "2024-08-12" : "2019-08-19",
          end_date: artifactType === "NDVI_BEFORE" ? "2019-08-19" : "2024-08-12",
          units: artifactType === "CHANGE_CLASSIFICATION" ? "变化类别" : "NDVI",
          attribution: "Copernicus Sentinel-2，经批准的离线数据",
          legend: [
            { schema_version: "1.0", value: -1, label: "降低", color: "#A23F35" },
            { schema_version: "1.0", value: 0, label: "稳定", color: "#F2E8C9" },
            { schema_version: "1.0", value: 1, label: "增加", color: "#3F7652" },
          ],
        },
      })),
      {
        schema_version: "1.0",
        artifact_id: reportArtifactId,
        tile_template: null,
        download_path: `/api/v1/tasks/${taskId}/artifacts/${reportArtifactId}/download`,
        tile_metadata: null,
      },
    ],
    report: {
      schema_version: "1.0",
      artifact_id: reportArtifactId,
      task_id: taskId,
      attempt: 1,
      artifact_type: "PDF_REPORT",
      status: "COMPLETE",
      media_type: "application/pdf",
      created_at: "2024-08-12T08:30:03Z",
      checksum_sha256: "b".repeat(64),
      byte_size: 1024,
    },
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
        publication: publisherPublication(),
      }),
    );
    const client = createMasterClient({ baseUrl, fetcher });
    const result = await client.getTask(taskId);

    expect(result).toMatchObject({
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
      publication: {
        taskId,
        attempt: 1,
      },
    });
    const beforeResource = result.publication?.resources.find(
      (resource) => resource.tileMetadata?.artifactType === "NDVI_BEFORE",
    );
    expect(beforeResource).toMatchObject({
      artifactId: "11111111-1111-4111-8111-111111111111",
      tileTemplate: `/api/v1/tiles/${taskId}/NDVI_BEFORE/{z}/{x}/{y}.png`,
      tileMetadata: {
        artifactType: "NDVI_BEFORE",
        boundsWgs84: [110.1, 31.0, 110.6, 31.5],
        attribution: "Copernicus Sentinel-2，经批准的离线数据",
      },
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

  it("rejects a complete SSE frame that exceeds the safe buffer limit", async () => {
    const oversizedEvent = {
      ...taskEvent(1),
      message: "超".repeat(262_144),
    };
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(streamResponse([`id: 1\ndata: ${JSON.stringify(oversizedEvent)}\n\n`]));
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
