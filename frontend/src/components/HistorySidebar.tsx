import type { ReportSummary } from "../types";
import { relativeTime } from "../utils/time";

interface Props {
  reports: ReportSummary[];
  loading: boolean;
  error: string | null;
  activeId: number | null;
  onSelect: (id: number) => void;
  onDelete: (id: number) => void;
}

export function HistorySidebar({ reports, loading, error, activeId, onSelect, onDelete }: Props) {
  return (
    <aside className="history" aria-label="Previous briefings">
      <h2 className="history__title">Recent briefings</h2>

      {error && <p className="history__note history__note--error">{error}</p>}

      {loading ? (
        <p className="history__note">Loading…</p>
      ) : reports.length === 0 ? (
        <p className="history__note">Briefings you run will be saved here.</p>
      ) : (
        <ul className="history__list">
          {reports.map((report) => (
            <li key={report.id}>
              <div className={`history__item${report.id === activeId ? " is-active" : ""}`}>
                <button
                  type="button"
                  className="history__open"
                  onClick={() => onSelect(report.id)}
                  aria-current={report.id === activeId ? "true" : undefined}
                >
                  <span className="history__company">{report.company}</span>
                  <span className="history__time">{relativeTime(report.created_at)}</span>
                </button>
                <button
                  type="button"
                  className="history__delete"
                  onClick={() => onDelete(report.id)}
                  aria-label={`Delete the briefing on ${report.company}`}
                  title="Delete"
                >
                  ×
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}
