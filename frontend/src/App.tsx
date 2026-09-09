import { useCallback, useState } from "react";

import { getReport } from "./api/client";
import { HistorySidebar } from "./components/HistorySidebar";
import { ReportView } from "./components/ReportView";
import { SearchBar } from "./components/SearchBar";
import { EmptyState, ErrorPanel } from "./components/states";
import { useReports } from "./hooks/useReports";
import { useResearch } from "./hooks/useResearch";
import { hasContent } from "./state/research";

export default function App() {
  const { reports, loading, error: historyError, remove, record } = useReports();
  const { state, start, cancel, show, clear } = useResearch({ onSaved: record });
  const [historyOpen, setHistoryOpen] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const researching = state.phase === "researching";

  const openReport = useCallback(
    async (id: number) => {
      setHistoryOpen(false);
      setLoadError(null);
      try {
        show(await getReport(id));
      } catch (error) {
        setLoadError(error instanceof Error ? error.message : "Could not open that briefing.");
      }
    },
    [show],
  );

  const deleteReport = useCallback(
    async (id: number) => {
      await remove(id);
      if (state.reportId === id) clear();
    },
    [remove, clear, state.reportId],
  );

  const research = useCallback(
    (company: string) => {
      setLoadError(null);
      void start(company);
    },
    [start],
  );

  return (
    <div className={`app${historyOpen ? " app--history-open" : ""}`}>
      <button
        type="button"
        className="app__history-toggle"
        onClick={() => setHistoryOpen((open) => !open)}
        aria-expanded={historyOpen}
      >
        {historyOpen ? "Close" : "History"}
      </button>

      <HistorySidebar
        reports={reports}
        loading={loading}
        error={historyError}
        activeId={state.reportId}
        onSelect={openReport}
        onDelete={deleteReport}
      />

      <main className="main">
        <SearchBar onSearch={research} onCancel={cancel} busy={researching} />

        {loadError && <ErrorPanel message={loadError} code={null} />}

        {state.phase === "error" && (
          <ErrorPanel
            message={state.error ?? "Research failed."}
            code={state.errorCode}
            onRetry={state.company ? () => research(state.company) : undefined}
          />
        )}

        {state.phase === "idle" ? (
          <EmptyState onPick={research} />
        ) : state.phase === "error" && !hasContent(state) ? null : (
          <ReportView state={state} />
        )}
      </main>

      {historyOpen && (
        <div className="app__scrim" onClick={() => setHistoryOpen(false)} aria-hidden="true" />
      )}
    </div>
  );
}
