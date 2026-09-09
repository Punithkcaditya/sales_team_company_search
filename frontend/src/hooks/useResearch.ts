import { useCallback, useEffect, useReducer, useRef } from "react";

import { researchStream } from "../api/stream";
import { ApiError } from "../api/client";
import { initialState, reducer } from "../state/research";
import type { Report } from "../types";

interface Options {
  /** Called when a run finishes and the backend has saved the report. */
  onSaved: (report: Report) => void;
}

export function useResearch({ onSaved }: Options) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const controller = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const saved = useRef(onSaved);
  saved.current = onSaved;

  useEffect(() => {
    mounted.current = true;
    return () => {
      // Unmounting mid-stream aborts the fetch, which closes the SSE response
      // and tears the agent run down on the server too.
      mounted.current = false;
      controller.current?.abort();
    };
  }, []);

  const start = useCallback(async (company: string) => {
    // A second search supersedes the first: abort before starting, so two
    // streams never write into the same view.
    controller.current?.abort();
    const run = new AbortController();
    controller.current = run;

    dispatch({ type: "start", company });

    let terminated = false;
    try {
      for await (const event of researchStream(company, run.signal)) {
        if (run.signal.aborted || !mounted.current) return;
        if (event.event === "done" || event.event === "error") terminated = true;
        dispatch({ type: "event", event });
        if (event.event === "done") saved.current(event.data.report);
      }
      if (!terminated && mounted.current && !run.signal.aborted) {
        dispatch({
          type: "failed",
          message: "The connection dropped before the briefing finished. Please try again.",
        });
      }
    } catch (error) {
      if (run.signal.aborted || !mounted.current) return;
      dispatch({ type: "failed", message: describe(error), code: error instanceof ApiError ? error.code : undefined });
    } finally {
      if (controller.current === run) controller.current = null;
    }
  }, []);

  const cancel = useCallback(() => {
    if (!controller.current) return;
    controller.current.abort();
    controller.current = null;
    dispatch({ type: "cancelled" });
  }, []);

  const show = useCallback((report: Report) => {
    controller.current?.abort();
    controller.current = null;
    dispatch({ type: "loaded", report });
  }, []);

  const clear = useCallback(() => {
    controller.current?.abort();
    controller.current = null;
    dispatch({ type: "clear" });
  }, []);

  return { state, start, cancel, show, clear };
}

function describe(error: unknown): string {
  return error instanceof Error && error.message
    ? error.message
    : "Something went wrong. Please try again.";
}
