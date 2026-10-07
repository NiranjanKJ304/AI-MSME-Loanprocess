"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Fetch data, optionally re-polling while `shouldPoll(data)` is true (e.g. documents still processing).
 */
export function useApi<T>(
  fetcher: () => Promise<T>,
  deps: unknown[],
  opts: { pollMs?: number; shouldPoll?: (data: T) => boolean } = {},
) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const optsRef = useRef(opts);
  optsRef.current = opts;

  const reload = useCallback(async () => {
    try {
      const d = await fetcherRef.current();
      setData(d);
      setError(null);
      return d;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      const d = await reload();
      const { pollMs = 2000, shouldPoll } = optsRef.current;
      if (!cancelled && d && shouldPoll?.(d)) timer = setTimeout(tick, pollMs);
    };
    setLoading(true);
    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, loading, reload };
}

export const ACTIVE_DOC_STATUSES = new Set(["UPLOADED", "VALIDATING", "VALID", "PROCESSING"]);
