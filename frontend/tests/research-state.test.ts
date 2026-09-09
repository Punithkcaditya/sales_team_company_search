import { describe, expect, it } from "vitest";

import { initialState, reducer, hasContent, type ResearchState } from "../src/state/research";
import type { Report, ResearchEvent } from "../src/types";

const play = (events: ResearchEvent[], from: ResearchState = initialState): ResearchState =>
  events.reduce((state, event) => reducer(state, { type: "event", event }), from);

const started = reducer(initialState, { type: "start", company: "Acme" });

const report: Report = {
  id: 7,
  company: "Acme Corporation",
  created_at: "2026-09-09T10:00:00+00:00",
  sections: {
    overview: "Acme makes widgets.",
    key_people: [{ name: "Ada", title: "CEO" }],
    news: [],
    financials: { revenue: "$4M", employee_count: null, market_cap: null, yoy_growth: null },
    risks: ["Antitrust review"],
  },
  sources: [{ title: "Profile", url: "https://x.test" }],
};

describe("research reducer", () => {
  it("starts clean so a second search never shows the first company's data", () => {
    const stale = reducer(started, { type: "event", event: { event: "done", data: { report } } });
    const fresh = reducer(stale, { type: "start", company: "Globex" });

    expect(fresh.company).toBe("Globex");
    expect(fresh.sections.overview).toBe("");
    expect(fresh.reportId).toBeNull();
    expect(Object.values(fresh.sectionStatus)).toEqual(Array(5).fill("pending"));
  });

  it("narrates each search the agent runs", () => {
    const state = play([{ event: "search", data: { query: "Acme leadership" } }], started);

    expect(state.activity).toBe("Searching “Acme leadership”");
    expect(state.queries).toEqual(["Acme leadership"]);
  });

  it("accumulates prose deltas into the overview", () => {
    const state = play(
      [
        { event: "section_start", data: { section: "overview" } },
        { event: "section_delta", data: { section: "overview", text: "Acme " } },
        { event: "section_delta", data: { section: "overview", text: "makes widgets." } },
      ],
      started,
    );

    expect(state.sections.overview).toBe("Acme makes widgets.");
    expect(state.sectionStatus.overview).toBe("streaming");
    expect(state.activity).toBe("Writing Company Overview…");
  });

  it("appends list items as they arrive and unwraps risks to plain strings", () => {
    const state = play(
      [
        { event: "section_item", data: { section: "key_people", item: { name: "Ada", title: "CEO" } } },
        { event: "section_item", data: { section: "risks", item: { risk: "Antitrust review" } } },
      ],
      started,
    );

    expect(state.sections.key_people).toEqual([{ name: "Ada", title: "CEO" }]);
    expect(state.sections.risks).toEqual(["Antitrust review"]);
  });

  it("marks a section done and takes the server's final value as authoritative", () => {
    const state = play(
      [
        { event: "section_item", data: { section: "news", item: { headline: "draft", source_url: null, published: null } } },
        { event: "section_end", data: { section: "news", value: [] } },
      ],
      started,
    );

    expect(state.sections.news).toEqual([]);
    expect(state.sectionStatus.news).toBe("done");
  });

  it("adopts the saved report on done, including the agent's canonical name", () => {
    const state = play([{ event: "done", data: { report } }], started);

    expect(state.phase).toBe("complete");
    expect(state.company).toBe("Acme Corporation");
    expect(state.reportId).toBe(7);
    expect(state.sources).toHaveLength(1);
    expect(Object.values(state.sectionStatus)).toEqual(Array(5).fill("done"));
  });

  it("keeps the error code so the UI can soften a not-found result", () => {
    const state = play(
      [{ event: "error", data: { code: "not_found", message: "No such company." } }],
      started,
    );

    expect(state).toMatchObject({ phase: "error", errorCode: "not_found", activity: "" });
  });

  it("keeps whatever was already on screen when a run is cancelled", () => {
    const partial = play([{ event: "section_delta", data: { section: "overview", text: "Acme" } }], started);
    const state = reducer(partial, { type: "cancelled" });

    expect(state.phase).toBe("cancelled");
    expect(state.sections.overview).toBe("Acme");
  });

  it("loads a saved report into the same shape a live run produces", () => {
    const state = reducer(initialState, { type: "loaded", report });

    expect(state.phase).toBe("complete");
    expect(state.sections.risks).toEqual(["Antitrust review"]);
    expect(hasContent(state)).toBe(true);
  });

  it("reports having no content before anything streams in", () => {
    expect(hasContent(started)).toBe(false);
  });
});
