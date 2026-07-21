import { isTaskId } from "../api/task-contract";

export interface TaskLocationState {
  readonly taskId: string | null;
  readonly invalid: boolean;
}

export function readTaskLocation(search: string): TaskLocationState {
  const values = new URLSearchParams(search).getAll("task_id");
  if (values.length === 0) {
    return { taskId: null, invalid: false };
  }
  if (values.length !== 1) {
    return { taskId: null, invalid: true };
  }
  const taskId = values[0];
  return taskId !== undefined && isTaskId(taskId)
    ? { taskId, invalid: false }
    : { taskId: null, invalid: true };
}

export function pushTaskLocation(taskId: string): void {
  if (!isTaskId(taskId)) {
    throw new Error("cannot write an invalid task id to the URL");
  }
  const url = new URL(window.location.href);
  url.searchParams.set("task_id", taskId);
  window.history.pushState(null, "", `${url.pathname}${url.search}${url.hash}`);
}

export function clearTaskLocation(): void {
  const url = new URL(window.location.href);
  url.searchParams.delete("task_id");
  window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
}
