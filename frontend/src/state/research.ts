import {
  SECTION_LABELS,
  SECTION_ORDER,
  emptySections,
  type Financials,
  type NewsItem,
  type Person,
  type Report,
  type ReportSections,
  type ResearchEvent,
  type SectionKey,
  type Source,
} from "../types";

export type Phase = "idle" | "researching" | "complete" | "error" | "cancelled";
export type SectionStatus = "pending" | "streaming" | "done";

export interface ResearchState {
  phase: Phase;
  company: string;
  /** What the agent is doing right now, in words a rep can read. */
  activity: string;
  queries: string[];
  sections: ReportSections;
  sectionStatus: Record<SectionKey, SectionStatus>;
  sources: Source[];
  error: string | null;
  errorCode: string | null;
  reportId: number | null;
}

export type Action =
  | { type: "start"; company: string }
  | { type: "event"; event: ResearchEvent }
  | { type: "failed"; message: string }
  | { type: "cancelled" }
  | { type: "loaded"; report: Report }
  | { type: "clear" };

const allSections = (status: SectionStatus): Record<SectionKey, SectionStatus> =>
  Object.fromEntries(SECTION_ORDER.map((s) => [s, status])) as Record<SectionKey, SectionStatus>;

export const initialState: ResearchState = {
  phase: "idle",
  company: "",
  activity: "",
  queries: [],
  sections: emptySections(),
  sectionStatus: allSections("pending"),
  sources: [],
  error: null,
  errorCode: null,
  reportId: null,
};

export function reducer(state: ResearchState, action: Action): ResearchState {
  switch (action.type) {
    case "clear":
      return initialState;

    case "start":
      return {
        ...initialState,
        phase: "researching",
        company: action.company,
        activity: `Researching ${action.company}…`,
      };

    case "failed":
      return { ...state, phase: "error", error: action.message, errorCode: "network" };

    case "cancelled":
      return { ...state, phase: "cancelled", activity: "" };

    case "loaded":
      return {
        ...initialState,
        phase: "complete",
        company: action.report.company,
        sections: action.report.sections,
        sectionStatus: allSections("done"),
        sources: action.report.sources,
        reportId: action.report.id,
      };

    case "event":
      return applyEvent(state, action.event);
  }
}

function applyEvent(state: ResearchState, { event, data }: ResearchEvent): ResearchState {
  switch (event) {
    case "status":
      return { ...state, activity: data.message };

    case "search":
      return {
        ...state,
        activity: `Searching “${data.query}”`,
        queries: [...state.queries, data.query],
      };

    case "section_start":
      return {
        ...state,
        activity: `Writing ${SECTION_LABELS[data.section]}…`,
        sectionStatus: { ...state.sectionStatus, [data.section]: "streaming" },
      };

    // Content implies the section is live, whether or not its `section_start`
    // arrived -- a dropped frame should not leave a filled section looking empty.
    case "section_delta":
      return data.section === "overview"
        ? {
            ...state,
            sections: { ...state.sections, overview: state.sections.overview + data.text },
            sectionStatus: markStreaming(state, data.section),
          }
        : state;

    case "section_item":
      return {
        ...state,
        sections: appendItem(state.sections, data.section, data.item),
        sectionStatus: markStreaming(state, data.section),
      };

    case "section_end":
      return {
        ...state,
        sections: setSection(state.sections, data.section, data.value),
        sectionStatus: { ...state.sectionStatus, [data.section]: "done" },
      };

    case "done":
      return {
        ...state,
        phase: "complete",
        activity: "",
        company: data.report.company,
        // The saved report is authoritative -- it is what history will show.
        sections: data.report.sections,
        sectionStatus: allSections("done"),
        sources: data.report.sources,
        reportId: data.report.id,
      };

    case "error":
      return { ...state, phase: "error", activity: "", error: data.message, errorCode: data.code };
  }
}

const markStreaming = (state: ResearchState, section: SectionKey) =>
  state.sectionStatus[section] === "pending"
    ? { ...state.sectionStatus, [section]: "streaming" as SectionStatus }
    : state.sectionStatus;

function appendItem(sections: ReportSections, section: SectionKey, item: unknown): ReportSections {
  switch (section) {
    case "key_people":
      return { ...sections, key_people: [...sections.key_people, item as Person] };
    case "news":
      return { ...sections, news: [...sections.news, item as NewsItem] };
    case "risks":
      return { ...sections, risks: [...sections.risks, (item as { risk: string }).risk] };
    default:
      return sections;
  }
}

function setSection(sections: ReportSections, section: SectionKey, value: unknown): ReportSections {
  switch (section) {
    case "overview":
      return { ...sections, overview: (value as string) ?? "" };
    case "key_people":
      return { ...sections, key_people: (value as Person[]) ?? [] };
    case "news":
      return { ...sections, news: (value as NewsItem[]) ?? [] };
    case "risks":
      return { ...sections, risks: (value as string[]) ?? [] };
    case "financials":
      return { ...sections, financials: value as Financials };
  }
}

/** True once there is something on screen worth looking at. */
export const hasContent = (state: ResearchState): boolean =>
  state.sections.overview.length > 0 ||
  state.sections.key_people.length > 0 ||
  state.sections.news.length > 0 ||
  state.sections.risks.length > 0;
