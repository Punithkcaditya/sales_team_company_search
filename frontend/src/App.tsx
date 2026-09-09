import { useCallback, useEffect, useRef, useState } from "react";

import { getReport } from "./api/client";
import { HistorySidebar } from "./components/HistorySidebar";
import { ReportView } from "./components/ReportView";
import { SearchBar } from "./components/SearchBar";
import { UsageBanner } from "./components/UsageBanner";
import { EmptyState, ErrorPanel } from "./components/states";
import { useReports } from "./hooks/useReports";
import { useResearch } from "./hooks/useResearch";
import { useUsage } from "./hooks/useUsage";
import { hasContent } from "./state/research";

export default function App() {
  const { reports, loading, error: historyError, remove, record } = useReports();
  const { state, start, cancel, show, clear } = useResearch({ onSaved: record });
  const [historyOpen, setHistoryOpen] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [searchReset, setSearchReset] = useState(0);
  const loadVersion = useRef(0);
  const { usage, unavailable, refresh } = useUsage();

  const researching = state.phase === "researching";
  const blocked = usage?.remaining === 0;

  useEffect(() => {
    // Refresh after starts and finishes, including failed and cancelled attempts.
    if (state.phase !== "idle") void refresh();
  }, [state.phase, refresh]);

  useEffect(() => () => { ++loadVersion.current; }, []);

  const backToSearch = useCallback(() => {
    ++loadVersion.current;
    clear();
    setLoadError(null);
    setHistoryOpen(false);
    setSearchReset((value) => value + 1);
    void refresh();
  }, [clear, refresh]);

  const openReport = useCallback(
    async (id: number) => {
      const current = ++loadVersion.current;
      setHistoryOpen(false);
      setLoadError(null);
      try {
        const report = await getReport(id);
        if (current === loadVersion.current) show(report);
      } catch (error) {
        if (current === loadVersion.current) {
          setLoadError(error instanceof Error ? error.message : "Could not open that briefing.");
        }
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
      if (blocked) return;
      ++loadVersion.current;
      setLoadError(null);
      void start(company);
    },
    [start, blocked],
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
        <SearchBar onSearch={research} onCancel={cancel} busy={researching} resetKey={searchReset} blocked={blocked} />
        <UsageBanner usage={usage} unavailable={unavailable} onRefresh={refresh} />

        {(state.phase !== "idle" || loadError) && (
          <button type="button" className="back-button" onClick={backToSearch}>
            <span aria-hidden="true">←</span> Back to search
          </button>
        )}

        {loadError && <ErrorPanel message={loadError} code={null} />}

        {state.phase === "error" && (
          <ErrorPanel
            message={state.error ?? "Research failed."}
            code={state.errorCode}
            onRetry={state.company && !blocked ? () => research(state.company) : undefined}
          />
        )}

        {state.phase === "idle" ? (
          <EmptyState onPick={research} blocked={blocked} />
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
