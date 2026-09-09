import { useCallback, useEffect, useState } from "react";

import { deleteReport, listReports } from "../api/client";
import type { Report, ReportSummary } from "../types";

export function useReports() {
  const [reports, setReports] = useState<ReportSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setReports(await listReports());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load your history.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  /** Drop it from the list immediately, put it back if the server disagrees. */
  const remove = useCallback(
    async (id: number) => {
      const previous = reports;
      setReports((current) => current.filter((report) => report.id !== id));
      try {
        await deleteReport(id);
      } catch (err) {
        setReports(previous);
        setError(err instanceof Error ? err.message : "Could not delete that report.");
      }
    },
    [reports],
  );

  const record = useCallback((report: Report) => {
    setReports((current) => [
      { id: report.id, company: report.company, created_at: report.created_at },
      ...current.filter((existing) => existing.id !== report.id),
    ]);
  }, []);

  return { reports, loading, error, refresh, remove, record, dismissError: () => setError(null) };
}
