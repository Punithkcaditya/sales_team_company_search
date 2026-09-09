import { SECTION_LABELS, SECTION_ORDER, type Financials, type NewsItem, type Person, type SectionKey } from "../types";
import type { ResearchState, SectionStatus } from "../state/research";

interface Props {
  state: ResearchState;
}

export function ReportView({ state }: Props) {
  const streaming = state.phase === "researching";

  return (
    <article className="report" aria-busy={streaming}>
      <header className="report__header">
        <h1 className="report__company">{state.company}</h1>
        <StatusLine state={state} />
      </header>

      {SECTION_ORDER.map((section) => (
        <SectionCard key={section} section={section} status={state.sectionStatus[section]} state={state} />
      ))}

      {state.sources.length > 0 && (
        <footer className="report__sources">
          <h3>Sources</h3>
          <ul>
            {state.sources.map((source) => (
              <li key={source.url}>
                <a href={source.url} target="_blank" rel="noreferrer noopener">
                  {source.title}
                </a>
              </li>
            ))}
          </ul>
        </footer>
      )}
    </article>
  );
}

function StatusLine({ state }: Props) {
  if (state.phase === "researching") {
    return (
      <p className="report__status" role="status">
        <span className="spinner" aria-hidden="true" />
        {state.activity || "Working…"}
      </p>
    );
  }
  if (state.phase === "cancelled") {
    return (
      <p className="report__status report__status--muted" role="status">
        Research cancelled. This briefing was not saved.
      </p>
    );
  }
  if (state.phase === "complete") {
    return (
      <p className="report__status report__status--muted">
        {state.queries.length > 0
          ? `Briefing ready — built from ${state.queries.length} searches.`
          : "Briefing ready."}
      </p>
    );
  }
  return null;
}

interface SectionProps {
  section: SectionKey;
  status: SectionStatus;
  state: ResearchState;
}

function SectionCard({ section, status, state }: SectionProps) {
  return (
    <section className={`card card--${status}`} aria-labelledby={`h-${section}`}>
      <div className="card__head">
        <h2 id={`h-${section}`} className="card__title">
          {SECTION_LABELS[section]}
        </h2>
        {status === "streaming" && state.phase === "researching" && (
          <span className="card__badge">
            <span className="spinner spinner--small" aria-hidden="true" />
            researching
          </span>
        )}
      </div>
      <SectionBody section={section} status={status} state={state} />
    </section>
  );
}

function SectionBody({ section, status, state }: SectionProps) {
  if (state.phase !== "researching" && status !== "done") {
    const value = state.sections[section];
    const hasPartial = typeof value === "string" || Array.isArray(value)
      ? value.length > 0
      : Object.values(value).some(Boolean);
    if (!hasPartial) return <p className="placeholder">Research stopped before this section finished.</p>;
    status = "done";
  }
  if (status === "pending") return <Skeleton />;

  const { sections } = state;
  switch (section) {
    case "overview":
      return sections.overview ? (
        <p className="prose">
          {sections.overview}
          {status === "streaming" && <span className="caret" aria-hidden="true" />}
        </p>
      ) : (
        <Placeholder status={status} what="a description of this company" />
      );

    case "key_people":
      return sections.key_people.length ? (
        <ul className="people">
          {sections.key_people.map((person: Person) => (
            <li key={`${person.name}-${person.title}`}>
              <span className="people__name">{person.name}</span>
              <span className="people__title">{person.title}</span>
            </li>
          ))}
        </ul>
      ) : (
        <Placeholder status={status} what="named executives" />
      );

    case "news":
      return sections.news.length ? (
        <ul className="news">
          {sections.news.map((item: NewsItem, index) => (
            <li key={`${item.headline}-${index}`}>
              <span className="news__headline">{item.headline}</span>
              <span className="news__meta">
                {item.published && <span>{item.published}</span>}
                {item.source_url && (
                  <a href={item.source_url} target="_blank" rel="noreferrer noopener">
                    source
                  </a>
                )}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <Placeholder status={status} what="recent news" />
      );

    case "financials":
      return <FinancialsGrid financials={sections.financials} status={status} />;

    case "risks":
      return sections.risks.length ? (
        <ul className="risks">
          {sections.risks.map((risk, index) => (
            <li key={`${risk}-${index}`}>{risk}</li>
          ))}
        </ul>
      ) : (
        <Placeholder status={status} what="notable risks" />
      );
  }
}

const FINANCIAL_LABELS: Array<[keyof Financials, string]> = [
  ["revenue", "Revenue"],
  ["employee_count", "Employees"],
  ["market_cap", "Market cap"],
  ["yoy_growth", "YoY growth"],
];

function FinancialsGrid({ financials, status }: { financials: Financials; status: SectionStatus }) {
  const known = FINANCIAL_LABELS.filter(([key]) => financials[key]);
  if (known.length === 0) return <Placeholder status={status} what="financial figures" />;

  return (
    <dl className="figures">
      {FINANCIAL_LABELS.map(([key, label]) => (
        <div key={key} className="figures__cell">
          <dt>{label}</dt>
          {/* An explicit "Not disclosed" beats a blank: the rep learns the
              number is unavailable rather than wondering if we failed. */}
          <dd className={financials[key] ? "" : "figures__unknown"}>
            {financials[key] ?? "Not disclosed"}
          </dd>
        </div>
      ))}
    </dl>
  );
}

const Placeholder = ({ status, what }: { status: SectionStatus; what: string }) =>
  status === "streaming" ? (
    <Skeleton />
  ) : (
    <p className="placeholder">Nothing found on {what}.</p>
  );

const Skeleton = () => (
  <div className="skeleton" aria-hidden="true">
    <span />
    <span />
    <span />
  </div>
);
