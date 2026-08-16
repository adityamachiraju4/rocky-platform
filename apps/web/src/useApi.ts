// Small typed data hook over api.ts. Exposes { data, loading, error, reload }.
// A session-expiry (AuthExpiredError) fires onExpired from an effect.

import { useEffect, useReducer } from "react";
import { AuthExpiredError } from "./api";
import { useAuth } from "./useAuth";

export interface Resource<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

interface State<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  expired: boolean;
  nonce: number;
}

type Action<T> =
  | { kind: "start" }
  | { kind: "success"; data: T }
  | { kind: "error"; error: string }
  | { kind: "expired" }
  | { kind: "reload" };

function reducer<T>(state: State<T>, action: Action<T>): State<T> {
  switch (action.kind) {
    case "start":
      return { ...state, loading: true, error: null };
    case "success":
      return { ...state, loading: false, data: action.data };
    case "error":
      return { ...state, loading: false, error: action.error };
    case "expired":
      return { ...state, loading: false, expired: true };
    case "reload":
      return { ...state, nonce: state.nonce + 1 };
  }
}

export function useResource<T>(fetcher: () => Promise<T>): Resource<T> {
  const { onExpired } = useAuth();
  const [state, dispatch] = useReducer(reducer<T>, {
    data: null,
    loading: true,
    error: null,
    expired: false,
    nonce: 0,
  });

  useEffect(() => {
    let cancelled = false;
    dispatch({ kind: "start" });
    fetcher()
      .then((d) => {
        if (!cancelled) dispatch({ kind: "success", data: d });
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        if (e instanceof AuthExpiredError) dispatch({ kind: "expired" });
        else dispatch({ kind: "error", error: e instanceof Error ? e.message : "Request failed" });
      });
    return () => {
      cancelled = true;
    };
  }, [state.nonce, fetcher]);

  useEffect(() => {
    if (state.expired) onExpired();
  }, [state.expired, onExpired]);

  return {
    data: state.data,
    loading: state.loading,
    error: state.error,
    reload: () => dispatch({ kind: "reload" }),
  };
}
