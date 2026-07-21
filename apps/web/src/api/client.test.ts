import { describe, expect, it, vi } from "vitest";

import { MasterApiError, createMasterClient } from "./client";

const baseUrl = "http://master.test";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("readiness Master client", () => {
  it("maps readiness and Agent health responses to camelCase view models", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({
          schema_version: "1.0",
          ready: true,
          llm_configured: true,
          data_configured: true,
          blockers: [],
          messages: ["系统已就绪"],
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          schema_version: "1.0",
          state: "HEALTHY",
          checked_at: "2024-08-12T08:00:00Z",
          services: [
            {
              schema_version: "1.0",
              service: "analysis",
              state: "HEALTHY",
              checked_at: "2024-08-12T08:00:00Z",
              message: null,
            },
          ],
        }),
      );
    const client = createMasterClient({ baseUrl, fetcher });

    await expect(client.getReadiness()).resolves.toEqual({
      ready: true,
      llmConfigured: true,
      dataConfigured: true,
      blockers: [],
      messages: ["系统已就绪"],
      health: {
        state: "HEALTHY",
        checkedAt: "2024-08-12T08:00:00Z",
        services: [
          {
            service: "analysis",
            state: "HEALTHY",
            checkedAt: "2024-08-12T08:00:00Z",
            message: null,
          },
        ],
      },
    });
    expect(fetcher).toHaveBeenNthCalledWith(
      1,
      `${baseUrl}/api/v1/config/readiness`,
      expect.objectContaining({ headers: { accept: "application/json" } }),
    );
    expect(fetcher).toHaveBeenNthCalledWith(
      2,
      `${baseUrl}/api/v1/health`,
      expect.objectContaining({ headers: { accept: "application/json" } }),
    );
  });
});

describe("task submission Master client", () => {
  it("submits the approved wire contract exactly once and returns camelCase data", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse(
        {
          schema_version: "1.0",
          task_id: "4f09fc09-6bd2-49fb-9636-7f4fb93baa44",
          status: "PENDING",
          created_at: "2024-08-12T08:30:00Z",
        },
        202,
      ),
    );
    const client = createMasterClient({ baseUrl, fetcher });

    await expect(client.createTask("  分析神农溪植被变化  ")).resolves.toEqual({
      taskId: "4f09fc09-6bd2-49fb-9636-7f4fb93baa44",
      status: "PENDING",
      createdAt: "2024-08-12T08:30:00Z",
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher).toHaveBeenCalledWith(`${baseUrl}/api/v1/tasks`, {
      method: "POST",
      headers: {
        accept: "application/json",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        schema_version: "1.0",
        query: "分析神农溪植被变化",
      }),
    });
  });

  it("preserves safe structured errors without exposing unknown provider fields", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse(
        {
          schema_version: "1.0",
          error: {
            schema_version: "1.0",
            code: "DEPENDENCY_UNAVAILABLE",
            message: "分析服务暂不可用",
            retryable: true,
            details: [
              {
                schema_version: "1.0",
                field: "analysis",
                reason: "健康检查失败",
              },
            ],
            provider_payload: "不得透传",
          },
        },
        503,
      ),
    );
    const client = createMasterClient({ baseUrl, fetcher });

    const error = await client.createTask("分析神农溪植被变化").catch((reason: unknown) => reason);

    expect(error).toBeInstanceOf(MasterApiError);
    expect(error).toMatchObject({
      code: "DEPENDENCY_UNAVAILABLE",
      message: "分析服务暂不可用",
      retryable: true,
      details: [{ field: "analysis", reason: "健康检查失败" }],
    });
    expect(error).not.toHaveProperty("provider_payload");
  });

  it.each(["", "   ", "河".repeat(2001)])("rejects an invalid query before sending it", async (query) => {
    const fetcher = vi.fn<typeof fetch>();
    const client = createMasterClient({ baseUrl, fetcher });

    await expect(client.createTask(query)).rejects.toMatchObject({
      code: "VALIDATION_ERROR",
      retryable: false,
    });
    expect(fetcher).not.toHaveBeenCalled();
  });
});

describe("task retry Master client", () => {
  it("retries one failed task and maps the accepted attempt", async () => {
    const taskId = "4f09fc09-6bd2-49fb-9636-7f4fb93baa44";
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse(
        {
          schema_version: "1.0",
          task_id: taskId,
          attempt: 2,
          status: "PENDING",
          accepted_at: "2024-08-12T08:31:00Z",
        },
        202,
      ),
    );
    const client = createMasterClient({ baseUrl, fetcher });

    await expect(client.retryTask(taskId)).resolves.toEqual({
      taskId,
      attempt: 2,
      status: "PENDING",
      acceptedAt: "2024-08-12T08:31:00Z",
    });
    expect(fetcher).toHaveBeenCalledWith(`${baseUrl}/api/v1/tasks/${taskId}/retry`, {
      method: "POST",
      headers: { accept: "application/json" },
    });
  });

  it("rejects an invalid task ID before retrying", async () => {
    const fetcher = vi.fn<typeof fetch>();
    const client = createMasterClient({ baseUrl, fetcher });

    await expect(client.retryTask("not-a-task")).rejects.toMatchObject({
      code: "VALIDATION_ERROR",
      retryable: false,
    });
    expect(fetcher).not.toHaveBeenCalled();
  });
});
