export const SECTION_ORDER = ["overview", "key_people", "news", "financials", "risks"] as const;

export type SectionKey = (typeof SECTION_ORDER)[number];

export const SECTION_LABELS: Record<SectionKey, string> = {
  overview: "Company Overview",
  key_people: "Key People",
  news: "Recent News",
  financials: "Financial Highlights",
  risks: "Risk Factors",
};

export interface Person {
  name: string;
  title: string;
}

export interface NewsItem {
  headline: string;
  source_url: string | null;
  published: string | null;
}

export interface Financials {
  revenue: string | null;
  employee_count: string | null;
  market_cap: string | null;
  yoy_growth: string | null;
}

export interface Source {
  title: string;
  url: string;
}

export interface ReportSections {
  overview: string;
  key_people: Person[];
  news: NewsItem[];
  financials: Financials;
  risks: string[];
}

export interface ReportSummary {
  id: number;
  company: string;
  created_at: string;
}

export interface Report extends ReportSummary {
  sections: ReportSections;
  sources: Source[];
}

/** The SSE vocabulary, mirroring `backend/app/events.py`. */
export type ResearchEvent =
  | { event: "status"; data: { phase: string; message: string } }
  | { event: "search"; data: { query: string } }
  | { event: "section_start"; data: { section: SectionKey } }
  | { event: "section_delta"; data: { section: SectionKey; text: string } }
  | { event: "section_item"; data: { section: SectionKey; item: unknown } }
  | { event: "section_end"; data: { section: SectionKey; value: unknown } }
  | { event: "done"; data: { report: Report } }
  | { event: "error"; data: { code: string; message: string } };

export const emptySections = (): ReportSections => ({
  overview: "",
  key_people: [],
  news: [],
  financials: { revenue: null, employee_count: null, market_cap: null, yoy_growth: null },
  risks: [],
});
