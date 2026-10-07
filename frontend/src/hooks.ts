import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ClaimDetail, type TraceEvent } from "./api";

/** Poll a loader. `interval` may change between renders (fast while agents work, slow when idle). */
export function usePoll<T>(load: () => Promise<T>, interval: number, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const loadRef = useRef(load);
  loadRef.current = load;
  const refresh = useCallback(async () => {
    try {
      setData(await loadRef.current());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    setData(null);
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => {
    const t = setInterval(refresh, interval);
    return () => clearInterval(t);
  }, [interval, refresh]);
  return { data, error, refresh };
}

const WORKING = new Set(["RECEIVED", "CLASSIFIED", "UNDER_REVIEW", "RECOMMENDED", "APPROVED",
  "PARTIALLY_APPROVED", "SETTLING", "SETTLED", "REJECTED"]);

export function useClaim(id: string) {
  const [fast, setFast] = useState(true);
  const res = usePoll(() => api.claim(id), fast ? 700 : 3000, [id]);
  const d = res.data as ClaimDetail | null;
  useEffect(() => {
    if (d) setFast(WORKING.has(d.status) && !d.waiting_for && d.flags?.turns != null);
  }, [d]);
  return res;
}

/** Incremental trace: fetches only events after the last seen sequence number. */
export function useTrace(id: string, live: boolean) {
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const last = useRef(0);
  useEffect(() => {
    setEvents([]);
    last.current = 0;
  }, [id]);
  useEffect(() => {
    let stop = false;
    const tick = async () => {
      try {
        const next = await api.trace(id, last.current);
        if (!stop && next.length) {
          last.current = next[next.length - 1].seq;
          setEvents((prev) => [...prev, ...next]);
        }
      } catch { /* transient */ }
    };
    tick();
    const t = setInterval(tick, live ? 500 : 2500);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, [id, live]);
  return events;
}
