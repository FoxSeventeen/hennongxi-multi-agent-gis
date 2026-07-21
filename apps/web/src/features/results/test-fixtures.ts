import type {
  PlanSource,
  TaskPublication,
  TaskSnapshot,
  TaskStatus,
  TaskTileMetadata,
} from "../../api/task-contract";

export const resultTaskId = "4f09fc09-6bd2-49fb-9636-7f4fb93baa44";
const correlationId = "f399c36a-6b76-4db5-a831-ebf6a170edf1";
const reportArtifactId = "55555555-5555-4555-8555-555555555555";

function tileMetadata(
  artifactType: TaskTileMetadata["artifactType"],
  startDate: string,
  endDate: string,
): TaskTileMetadata {
  return {
    artifactType,
    boundsWgs84: [110.1, 31, 110.6, 31.5],
    startDate,
    endDate,
    units: artifactType === "CHANGE_CLASSIFICATION" ? "变化类别" : "NDVI",
    attribution: "Copernicus Sentinel-2，经批准的离线数据",
    legend: [
      { value: -1, label: "降低", color: "#A23F35" },
      { value: 0, label: "稳定", color: "#F2E8C9" },
      { value: 1, label: "增加", color: "#3F7652" },
    ],
  };
}

function publication(): TaskPublication {
  const tileDefinitions = [
    ["NDVI_BEFORE", "2019-08-19", "2019-08-19"],
    ["NDVI_AFTER", "2024-08-12", "2024-08-12"],
    ["NDVI_DIFFERENCE", "2019-08-19", "2024-08-12"],
    ["CHANGE_CLASSIFICATION", "2019-08-19", "2024-08-12"],
  ] as const;
  return {
    taskId: resultTaskId,
    attempt: 1,
    correlationId,
    report: {
      artifactId: reportArtifactId,
      createdAt: "2024-08-12T08:30:06Z",
      checksumSha256: "b".repeat(64),
      byteSize: 24_576,
    },
    resources: [
      ...tileDefinitions.map(([artifactType, startDate, endDate], index) => ({
        artifactId: `00000000-0000-4000-8000-00000000000${String(index)}`,
        tileTemplate: `/api/v1/tiles/${resultTaskId}/${artifactType}/{z}/{x}/{y}.png`,
        downloadPath: null,
        tileMetadata: tileMetadata(artifactType, startDate, endDate),
      })),
      {
        artifactId: reportArtifactId,
        tileTemplate: null,
        downloadPath: `/api/v1/tasks/${resultTaskId}/artifacts/${reportArtifactId}/download`,
        tileMetadata: null,
      },
    ],
  };
}

export function completedResultSnapshot(options: {
  readonly status?: TaskStatus;
  readonly source?: PlanSource;
} = {}): TaskSnapshot {
  const source = options.source ?? "REAL_LLM";
  const failure = {
    code: "PUBLISHING_FAILED" as const,
    message: "发布服务暂时失败",
    retryable: true,
    details: [{ field: "publish_results", reason: "Publisher 暂时不可用" }],
  };
  return {
    taskId: resultTaskId,
    query: "分析神农溪流域 2019 至 2024 年植被变化",
    status: options.status ?? "COMPLETED",
    progress: options.status === "FAILED" ? 80 : 100,
    currentAttempt: 1,
    correlationId,
    createdAt: "2024-08-12T08:30:00Z",
    updatedAt: "2024-08-12T08:30:06Z",
    plan: {
      planId: "354da501-f92e-432d-8367-c845c16d6a07",
      source,
      createdAt: "2024-08-12T08:30:01Z",
      modelCall: {
        model: "claude-3-7-sonnet",
        startedAt: "2024-08-12T08:30:00Z",
        durationMs: 640,
        status: source === "REAL_LLM" ? "SUCCEEDED" : "FAILED",
        inputTokens: 128,
        outputTokens: 256,
        responseSha256: source === "REAL_LLM" ? "a".repeat(64) : null,
        errorCode: source === "REAL_LLM" ? null : "DEPENDENCY_UNAVAILABLE",
      },
      steps:
        options.status === "FAILED"
          ? [
              {
                stepId: "publish_results",
                kind: "publish_results",
                agent: "publisher",
                order: 4,
                title: "发布地图与监测报告",
                dependsOn: ["evaluate_quality"],
              },
            ]
          : [],
    },
    steps:
      options.status === "FAILED"
        ? [
            {
              stepId: "publish_results",
              kind: "publish_results",
              agent: "publisher",
              attempt: 1,
              status: "FAILED",
              progress: 0,
              startedAt: "2024-08-12T08:30:05Z",
              completedAt: "2024-08-12T08:30:06Z",
              elapsedMs: 480,
              error: failure,
            },
          ]
        : [],
    lastError: options.status === "FAILED" ? failure : null,
    analysis: {
      taskId: resultTaskId,
      attempt: 1,
      correlationId,
      statistics: {
        increaseHectares: 128.4,
        stableHectares: 702.6,
        decreaseHectares: 54.2,
        validHectares: 885.2,
      },
      elapsedMs: 1320,
    },
    quality: {
      taskId: resultTaskId,
      attempt: 1,
      correlationId,
      coverageRatio: 0.982,
      validPixelRatio: 0.961,
      outputComplete: true,
      elapsedMs: 1320,
      thresholds: {
        minimumWatershedCoverageRatio: 0.95,
        minimumValidPixelRatio: 0.9,
      },
      conclusion: "PASS",
      passed: true,
      evidence: ["范围通过", "像元通过", "成果完整", "耗时已记录"],
    },
    publication: publication(),
  };
}
