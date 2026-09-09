import { describe, expect, it } from "vitest";

import { parseEventStream, researchStream } from "../src/api/stream";

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

const collect = async <T>(source: AsyncGenerator<T>): Promise<T[]> => {
  const items: T[] = [];
  for await (const item of source) items.push(item);
  return items;
};

describe("parseEventStream", () => {
  it("emits one event per frame", async () => {
    const events = await collect(
      parseEventStream(streamOf(['event: status\ndata: {"a":1}\n\nevent: done\ndata: {"b":2}\n\n'])),
    );

    expect(events).toEqual([
      { event: "status", data: '{"a":1}' },
      { event: "done", data: '{"b":2}' },
    ]);
  });

  it("reassembles frames split across network chunks", async () => {
    const events = await collect(
      parseEventStream(streamOf(["event: sec", 'tion_delta\ndata: {"text":"Ac', 'me"}\n\n'])),
    );

    expect(events).toEqual([{ event: "section_delta", data: '{"text":"Acme"}' }]);
  });

  it("handles CRLF line endings and keep-alive comments", async () => {
    const events = await collect(
      parseEventStream(streamOf([': keep-alive\r\n\r\nevent: done\r\ndata: {}\r\n\r\n'])),
    );

    expect(events).toEqual([{ event: "done", data: "{}" }]);
  });

  it("ignores a trailing partial frame rather than emitting half an event", async () => {
    const events = await collect(
      parseEventStream(streamOf(['event: status\ndata: {}\n\nevent: done\ndata: {"tru'])),
    );

    expect(events).toEqual([{ event: "status", data: "{}" }]);
  });
});

describe("researchStream", () => {
  const respondWith = (chunks: string[]) => {
    globalThis.fetch = (() =>
      Promise.resolve({ ok: true, status: 200, body: streamOf(chunks) })) as unknown as typeof fetch;
  };

  it("decodes known events and skips ones it does not recognise", async () => {
    respondWith([
      'event: status\ndata: {"phase":"searching","message":"Researching…"}\n\n',
      "event: telemetry\ndata: {}\n\n", // a future event this client predates
      'event: done\ndata: {"report":{"id":1}}\n\n',
    ]);

    const events = await collect(researchStream("Acme", new AbortController().signal));

    expect(events.map((e) => e.event)).toEqual(["status", "done"]);
  });

  it("drops an unparseable payload instead of failing the run", async () => {
    respondWith(['event: status\ndata: {broken\n\nevent: done\ndata: {"report":{}}\n\n']);

    const events = await collect(researchStream("Acme", new AbortController().signal));

    expect(events.map((e) => e.event)).toEqual(["done"]);
  });

  it("surfaces the backend's message when the request is refused", async () => {
    globalThis.fetch = (() =>
      Promise.resolve({
        ok: false,
        status: 409,
        body: null,
        json: () => Promise.resolve({ message: "Research on Acme is already running." }),
      })) as unknown as typeof fetch;

    await expect(collect(researchStream("Acme", new AbortController().signal))).rejects.toThrow(
      "Research on Acme is already running.",
    );
  });

  it("explains an unreachable backend in plain language", async () => {
    globalThis.fetch = (() => Promise.reject(new TypeError("Failed to fetch"))) as unknown as typeof fetch;

    await expect(collect(researchStream("Acme", new AbortController().signal))).rejects.toThrow(
      /Can't reach the server/,
    );
  });
});
