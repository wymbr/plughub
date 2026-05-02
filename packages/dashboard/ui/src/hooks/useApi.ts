/**
 * useApi — generic fetch hook with loading/error state.
 * Re-fetches whenever the URL changes.
 */

import { useCallback, useEffect, useState } from "react";

export type ApiState<T> =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; error: string }
  | { status: "ok"; data: T };

export function useApi<T>(url: string | null): [ApiState<T>, () => void] {
  const [state, setState] = useState<ApiState<T>>({ status: "idle" });

  const fetchData = useCallback(async () => {
    if (!url) {
      setState({ status: "idle" });
      return;
    }
    setState({ status: "loading" });
    try {
      const res = await fetch(url);
      if (!res.ok) {
        setState({ status: "error", error: `HTTP ${res.status}` });
        return;
      }
      const data = (await res.json()) as T;
      setState({ status: "ok", data });
    } catch (err) {
      setState({
        status: "error",
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }, [url]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  return [state, fetchData];
}
