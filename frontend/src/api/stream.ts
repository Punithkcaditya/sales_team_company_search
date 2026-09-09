import { API_BASE, ApiError } from "./client";
import type { ResearchEvent } from "../types";

/**
 * Reading SSE from a POST.
 *
 * `EventSource` only does GET, and the company name belongs in a request body
 * rather than a URL, so we read the response stream and parse the (small)
 * wire format ourselves. That also gives us AbortController support, which
 * `EventSource` lacks -- cancelling research is just aborting the fetch.
 */

interface RawEvent {
  event: string;
  data: string;
}

export async function* parseEventStream(body: ReadableStream<Uint8Array>): AsyncGenerator<RawEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");

      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const parsed = parseFrame(frame);
        if (parsed) yield parsed;
        boundary = buffer.indexOf("\n\n");
      }
    }
  } finally {
    reader.releaseLock();
  }
}

function parseFrame(frame: string): RawEvent | null {
  let event = "message";
  const data: string[] = [];

  for (const line of frame.split("\n")) {
    if (line.startsWith(":")) continue; // keep-alive comment
    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    const value = separator === -1 ? "" : line.slice(separator + 1).replace(/^ /, "");
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }

  return data.length ? { event, data: data.join("\n") } : null;
}

export async function* researchStream(
  company: string,
  signal: AbortSignal,
): AsyncGenerator<ResearchEvent> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/api/research`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ company }),
      signal,
    });
  } catch (error) {
    if (signal.aborted) throw error;
    throw new ApiError("Can't reach the server. Is the backend running?", 0);
  }

  if (!response.ok || !response.body) {
    let message = "Research could not be started.";
    let code: string | undefined;
    try {
      const body = await response.json();
      if (typeof body?.message === "string") message = body.message;
      if (typeof body?.code === "string") code = body.code;
    } catch {
      /* keep the generic message */
    }
    throw new ApiError(message, response.status, code ?? (response.status === 429 ? "quota_exceeded" : undefined));
  }

  for await (const raw of parseEventStream(response.body)) {
    // Unknown event names are ignored on purpose, so adding one to the backend
    // never breaks a client that has not shipped yet.
    if (!KNOWN_EVENTS.has(raw.event)) continue;
    try {
      yield { event: raw.event, data: JSON.parse(raw.data) } as ResearchEvent;
    } catch {
      /* a truncated frame is not worth failing the whole run over */
    }
  }
}

const KNOWN_EVENTS = new Set([
  "status",
  "search",
  "section_start",
  "section_delta",
  "section_item",
  "section_end",
  "done",
  "error",
]);
