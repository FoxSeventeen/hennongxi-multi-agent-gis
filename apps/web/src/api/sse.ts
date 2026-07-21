const MAX_EVENT_BUFFER_CHARACTERS = 262_144;
const frameBoundary = /\r\n\r\n|\n\n|\r\r/;

export async function consumeEventStream(
  response: Response,
  onMessage: (id: number, payload: unknown) => void,
): Promise<void> {
  if (response.body === null) {
    throw new Error("SSE response body is unavailable");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  let result = await reader.read();
  while (!result.done) {
    buffer += decoder.decode(result.value, { stream: true });
    buffer = consumeCompleteFrames(buffer, onMessage);
    if (buffer.length > MAX_EVENT_BUFFER_CHARACTERS) {
      await reader.cancel();
      throw new Error("SSE event exceeds the safe buffer limit");
    }
    result = await reader.read();
  }
  buffer += decoder.decode();
  buffer = consumeCompleteFrames(buffer, onMessage);

  if (buffer.trim().length > 0) {
    throw new Error("SSE stream ended with an incomplete event");
  }
}

function consumeCompleteFrames(
  initialBuffer: string,
  onMessage: (id: number, payload: unknown) => void,
): string {
  let buffer = initialBuffer;
  let boundary = frameBoundary.exec(buffer);
  while (boundary !== null) {
    const frame = buffer.slice(0, boundary.index);
    buffer = buffer.slice(boundary.index + boundary[0].length);
    consumeFrame(frame, onMessage);
    boundary = frameBoundary.exec(buffer);
  }
  return buffer;
}

function consumeFrame(frame: string, onMessage: (id: number, payload: unknown) => void): void {
  let id: string | null = null;
  const data: string[] = [];

  for (const line of frame.split(/\r\n|\r|\n/)) {
    if (line.length === 0 || line.startsWith(":")) {
      continue;
    }
    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    let value = separator === -1 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) {
      value = value.slice(1);
    }
    if (field === "id") {
      id = value;
    } else if (field === "data") {
      data.push(value);
    }
  }

  if (data.length === 0) {
    return;
  }
  if (id === null || !/^[1-9][0-9]*$/.test(id)) {
    throw new Error("SSE event id is not a canonical positive integer");
  }
  const sequence = Number(id);
  if (!Number.isSafeInteger(sequence)) {
    throw new Error("SSE event id exceeds the safe integer range");
  }

  let payload: unknown;
  try {
    payload = JSON.parse(data.join("\n"));
  } catch {
    throw new Error("SSE event data is not valid JSON");
  }
  onMessage(sequence, payload);
}
