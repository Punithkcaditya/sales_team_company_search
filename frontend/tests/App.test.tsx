import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../src/App";
import type { Report, ReportSummary, UsageStatus } from "../src/types";

/** A hand-driven SSE body, so tests can assert on half-finished research. */
class LiveStream {
  private controller!: ReadableStreamDefaultController<Uint8Array>;
  readonly body: ReadableStream<Uint8Array>;
  readonly signal: AbortSignal;

  constructor(signal: AbortSignal) {
    this.signal = signal;
    this.body = new ReadableStream({
      start: (controller) => {
        this.controller = controller;
      },
    });
  }

  async push(event: string, data: unknown) {
    await act(async () => {
      this.controller.enqueue(
        new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`),
      );
      await Promise.resolve();
    });
  }

  async close() {
    await act(async () => {
      this.controller.close();
      await Promise.resolve();
    });
  }
}

const report = (over: Partial<Report> = {}): Report => ({
  id: 1,
  company: "Stripe",
  created_at: new Date().toISOString(),
  sections: {
    overview: "Stripe builds payments infrastructure.",
    key_people: [{ name: "Patrick", title: "CEO" }],
    news: [{ headline: "Launched Issuing in the EU", published: "July 2026", source_url: "https://x.test" }],
    financials: { revenue: "$16B", employee_count: "8,000", market_cap: null, yoy_growth: "25%" },
    risks: ["Increased regulatory scrutiny in the EU"],
  },
  sources: [{ title: "Profile", url: "https://x.test" }],
  ...over,
});

class Backend {
  reports: ReportSummary[] = [];
  reportsById = new Map<number, Report>();
  stream: LiveStream | null = null;
  researchStatus = 200;
  researchError = "";
  listFails = false;
  deleted: number[] = [];
  usage: UsageStatus = { mode: "demo", provider: "demo", daily_limit: null, used_today: 0, remaining: null, resets_at: null, provider_tokens_remaining: null };

  seed(full: Report) {
    this.reports = [{ id: full.id, company: full.company, created_at: full.created_at }, ...this.reports];
    this.reportsById.set(full.id, full);
  }

  install() {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";

      if (url.endsWith("/api/usage")) return json({ ...this.usage });

      if (url.endsWith("/api/research")) {
        if (this.researchStatus !== 200) {
          return json({ message: this.researchError }, this.researchStatus);
        }
        if (this.usage.remaining !== null) {
          this.usage.remaining -= 1;
          this.usage.used_today += 1;
        }
        this.stream = new LiveStream(init!.signal!);
        return { ok: true, status: 200, body: this.stream.body } as unknown as Response;
      }
      if (url.endsWith("/api/reports")) {
        if (this.listFails) throw new TypeError("Failed to fetch");
        return json(this.reports);
      }
      const id = Number(url.split("/").pop());
      if (method === "DELETE") {
        this.deleted.push(id);
        this.reports = this.reports.filter((r) => r.id !== id);
        return { ok: true, status: 204 } as Response;
      }
      const found = this.reportsById.get(id);
      return found ? json(found) : json({ message: "That report no longer exists." }, 404);
    }) as unknown as typeof fetch;
  }
}

const json = (body: unknown, status = 200) =>
  ({ ok: status < 400, status, json: async () => body }) as Response;

let backend: Backend;

beforeEach(() => {
  backend = new Backend();
  backend.install();
});

afterEach(() => {
  vi.restoreAllMocks();
});

const startResearch = async (company: string) => {
  const user = userEvent.setup();
  const input = screen.getByRole("textbox", { name: /company name/i });
  await user.clear(input);
  await user.type(input, company);
  await user.click(screen.getByRole("button", { name: "Research" }));
  await waitFor(() => expect(backend.stream).not.toBeNull());
  return backend.stream!;
};

describe("first visit", () => {
  it("labels demo data and does not show a made-up token allowance", async () => {
    render(<App />);
    expect(await screen.findByText("Demo mode")).toBeInTheDocument();
    expect(screen.getByText(/No AI tokens or live search calls are used/)).toBeInTheDocument();
    expect(screen.queryByText(/app searches left/)).not.toBeInTheDocument();
  });
  it("tells the rep what to do instead of showing a blank screen", async () => {
    render(<App />);

    expect(await screen.findByText(/Walk in knowing the room/i)).toBeInTheDocument();
    expect(screen.getByText(/Briefings you run will be saved here/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stripe" })).toBeInTheDocument();
  });

  it("an example chip starts research without any typing", async () => {
    render(<App />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Stripe" }));

    await waitFor(() => expect(backend.stream).not.toBeNull());
    expect(screen.getByRole("heading", { level: 1, name: "Stripe" })).toBeInTheDocument();
  });
});

describe("a research run", () => {
  it("back to search aborts the run, clears the input and ignores late content", async () => {
    render(<App />);
    const stream = await startResearch("Stripe");
    await stream.push("section_delta", { section: "overview", text: "Partial research." });
    await userEvent.setup().click(screen.getByRole("button", { name: /Back to search/ }));
    expect(stream.signal.aborted).toBe(true);
    expect(screen.getByText(/Walk in knowing the room/)).toBeInTheDocument();
    const input = screen.getByRole("textbox", { name: /company name/i });
    expect(input).toHaveValue("");
    expect(input).toHaveFocus();
    await stream.push("done", { report: report() });
    expect(screen.queryByText(/Briefing ready/)).not.toBeInTheDocument();
  });

  it("updates the shared allowance after a live attempt", async () => {
    backend.usage = { ...backend.usage, mode: "live", provider: "gemini", daily_limit: 20, remaining: 20, resets_at: "2026-09-10T00:00:00Z" };
    render(<App />);
    expect(await screen.findByText("20 of 20 briefings left today")).toBeInTheDocument();
    const stream = await startResearch("Stripe");
    await stream.push("done", { report: report() });
    await stream.close();
    expect(await screen.findByText("19 of 20 briefings left today")).toBeInTheDocument();
    expect(screen.getByText(/Gemini's free tier is also rate limited per minute/)).toBeInTheDocument();
  });
  it("shows progress, renders sections as they stream, then saves to history", async () => {
    render(<App />);
    const stream = await startResearch("Stripe");

    // Streaming state: the rep can see what the agent is doing.
    await stream.push("search", { query: "Stripe executive team" });
    expect(screen.getByText(/Searching “Stripe executive team”/)).toBeInTheDocument();

    // Prose renders progressively rather than waiting for the section to finish.
    await stream.push("section_start", { section: "overview" });
    expect(screen.getAllByText("researching").length).toBe(1);
    await stream.push("section_delta", { section: "overview", text: "Stripe builds " });
    await stream.push("section_delta", { section: "overview", text: "payments infrastructure." });
    expect(screen.getByText(/Stripe builds payments infrastructure\./)).toBeInTheDocument();
    await stream.push("section_end", { section: "overview", value: "Stripe builds payments infrastructure." });

    // List sections land one item at a time.
    await stream.push("section_start", { section: "key_people" });
    await stream.push("section_item", { section: "key_people", item: { name: "Patrick", title: "CEO" } });
    expect(screen.getByText("Patrick")).toBeInTheDocument();

    await stream.push("done", { report: report() });
    await stream.close();

    expect(screen.getByText(/Briefing ready/)).toBeInTheDocument();
    expect(screen.getByText("$16B")).toBeInTheDocument();
    // A metric the agent could not source is called out, not silently blank.
    expect(screen.getByText("Not disclosed")).toBeInTheDocument();

    const history = screen.getByRole("complementary", { name: /previous briefings/i });
    expect(within(history).getByText("Stripe")).toBeInTheDocument();
    expect(within(history).getByText("just now")).toBeInTheDocument();
  });

  it("lets the rep cancel and keeps what had already rendered", async () => {
    render(<App />);
    const stream = await startResearch("Stripe");
    await stream.push("section_delta", { section: "overview", text: "Stripe builds payments." });

    await userEvent.setup().click(screen.getByRole("button", { name: "Cancel" }));

    expect(stream.signal.aborted).toBe(true);
    expect(screen.getByText(/Research cancelled/)).toBeInTheDocument();
    expect(screen.getByText(/Stripe builds payments\./)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Research" })).toBeInTheDocument();
  });

  it("aborts the stream when the view goes away mid-run", async () => {
    const { unmount } = render(<App />);
    const stream = await startResearch("Stripe");
    await stream.push("section_delta", { section: "overview", text: "Stripe" });

    unmount();

    expect(stream.signal.aborted).toBe(true);
  });

  it("supersedes an in-flight run when a second search starts", async () => {
    render(<App />);
    const first = await startResearch("Stripe");
    await first.push("section_delta", { section: "overview", text: "Stripe builds payments." });

    await userEvent.setup().click(screen.getByRole("button", { name: "Cancel" }));
    const second = await startResearch("Datadog");

    expect(first.signal.aborted).toBe(true);
    expect(second).not.toBe(first);
    expect(screen.getByRole("heading", { level: 1, name: "Datadog" })).toBeInTheDocument();
    expect(screen.queryByText(/Stripe builds payments\./)).not.toBeInTheDocument();
  });
});

describe("things going wrong", () => {
  it("shows provider quota exhaustion during a report and stops loading indicators", async () => {
    render(<App />);
    const stream = await startResearch("Stripe");
    await stream.push("section_delta", { section: "overview", text: "Partial research." });
    await stream.push("section_start", { section: "key_people" });
    await stream.push("error", { code: "quota_exceeded", message: "Gemini's request or token limit has been reached." });
    await stream.close();
    expect(screen.getByRole("alert")).toHaveTextContent("Research limit reached");
    expect(screen.getByRole("alert")).toHaveTextContent(/saved briefings/);
    expect(screen.getByText("Partial research.")).toBeInTheDocument();
    expect(screen.queryByText("researching")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Back to search/ })).toBeEnabled();
  });

  it("handles a quota rejection before streaming starts", async () => {
    backend.researchStatus = 429;
    backend.researchError = "Research quota reached.";
    render(<App />);
    const user = userEvent.setup();
    await user.type(screen.getByRole("textbox", { name: /company name/i }), "Stripe");
    await user.click(screen.getByRole("button", { name: "Research" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Research limit reached");
  });

  it("blocks new searches at zero allowance while preserving history and allows refresh", async () => {
    backend.usage = { ...backend.usage, mode: "live", provider: "gemini", daily_limit: 20, used_today: 20, remaining: 0, resets_at: "2026-09-10T00:00:00Z" };
    backend.seed(report({ company: "Saved company" }));
    render(<App />);
    expect(await screen.findByText("0 of 20 briefings left today")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stripe" })).toBeDisabled();
    const user = userEvent.setup();
    await user.type(screen.getByRole("textbox", { name: /company name/i }), "New company");
    expect(screen.getByRole("button", { name: "Research" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /^Saved company/ }));
    expect(await screen.findByText(/Briefing ready/)).toBeInTheDocument();
    backend.usage.remaining = 20;
    backend.usage.used_today = 0;
    await user.click(screen.getByRole("button", { name: "Refresh availability" }));
    expect(await screen.findByText("20 of 20 briefings left today")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Research" })).toBeEnabled();
  });
  it("explains an unresearchable company gently and saves nothing", async () => {
    render(<App />);
    const stream = await startResearch("asdkjhasd");
    await stream.push("error", { code: "not_found", message: "We could not find a company called “asdkjhasd”." });
    await stream.close();

    expect(screen.getByRole("alert")).toHaveTextContent(/could not find a company/i);
    expect(screen.getByText(/Check the spelling/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 1 })).not.toBeInTheDocument();
  });

  it("refuses a duplicate run with the backend's own wording", async () => {
    backend.researchStatus = 409;
    backend.researchError = "Research on Stripe is already running.";
    render(<App />);

    const user = userEvent.setup();
    await user.type(screen.getByRole("textbox", { name: /company name/i }), "Stripe");
    await user.click(screen.getByRole("button", { name: "Research" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Research on Stripe is already running.");
  });

  it("shows a human message, not a stack trace, when the stream dies early", async () => {
    render(<App />);
    const stream = await startResearch("Stripe");
    await stream.push("section_delta", { section: "overview", text: "Stripe" });
    await stream.close();

    expect(await screen.findByRole("alert")).toHaveTextContent(/connection dropped/i);
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("survives a history endpoint that is down", async () => {
    backend.listFails = true;
    render(<App />);

    expect(await screen.findByText(/Can't reach the server/i)).toBeInTheDocument();
    expect(screen.getByText(/Walk in knowing the room/i)).toBeInTheDocument();
  });
});

describe("history", () => {
  it("returns from a saved briefing without deleting it", async () => {
    backend.seed(report());
    render(<App />);
    const user = userEvent.setup();
    const history = screen.getByRole("complementary", { name: /previous briefings/i });
    await user.click(await within(history).findByRole("button", { name: /^Stripe/ }));
    await user.click(await screen.findByRole("button", { name: /Back to search/ }));
    expect(screen.getByText(/Walk in knowing the room/)).toBeInTheDocument();
    expect(within(history).getByText("Stripe")).toBeInTheDocument();
    expect(backend.deleted).toEqual([]);
  });
  it("opens a saved briefing and deletes it", async () => {
    backend.seed(report({ id: 42, company: "Datadog" }));
    render(<App />);
    const user = userEvent.setup();
    const history = screen.getByRole("complementary", { name: /previous briefings/i });

    await user.click(await within(history).findByRole("button", { name: /^Datadog/ }));
    expect(await screen.findByRole("heading", { level: 1, name: "Datadog" })).toBeInTheDocument();
    expect(screen.getByText("Increased regulatory scrutiny in the EU")).toBeInTheDocument();

    await user.click(within(history).getByRole("button", { name: /Delete the briefing on Datadog/ }));

    await waitFor(() => expect(backend.deleted).toEqual([42]));
    // Deleting the open briefing returns the rep to the starting screen.
    expect(await screen.findByText(/Walk in knowing the room/i)).toBeInTheDocument();
  });
});

describe("keyboard", () => {
  it("Ctrl/Cmd+K puts the cursor in the search box", async () => {
    render(<App />);
    const input = await screen.findByRole("textbox", { name: /company name/i });
    input.blur();

    await userEvent.setup().keyboard("{Control>}k{/Control}");

    expect(input).toHaveFocus();
  });
});
