import { useCallback, useEffect, useRef, useState } from "react";

import { getUsage } from "../api/client";
import type { UsageStatus } from "../types";

export function useUsage() {
  const [usage, setUsage] = useState<UsageStatus | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const version = useRef(0);

  const refresh = useCallback(async () => {
    const current = ++version.current;
    try {
      const next = await getUsage();
      if (current !== version.current) return;
      setUsage(next);
      setUnavailable(false);
    } catch {
      if (current !== version.current) return;
      setUsage(null);
      setUnavailable(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const onFocus = () => { void refresh(); };
    const interval = window.setInterval(onFocus, 60_000);
    window.addEventListener("focus", onFocus);
    return () => {
      ++version.current;
      window.clearInterval(interval);
      window.removeEventListener("focus", onFocus);
    };
  }, [refresh]);

  return { usage, unavailable, refresh };
}
