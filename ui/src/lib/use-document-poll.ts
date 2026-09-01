"use client";

/**
 * Polls one document until it reaches a terminal state.
 *
 * Polling stops on `completed`/`failed`, on an unmounted component, and when
 * the id changes -- a stale response can never overwrite a newer one because
 * every response is checked against the id it was requested for.
 */
import * as React from "react";
import { getDocument } from "./api";
import { isTerminal, phaseOf } from "./status";
import type { DocumentRecord } from "./types";

const DEFAULT_INTERVAL_MS = 1500;

interface Options {
  intervalMs?: number;
  /** Called once when the document first reaches a terminal state. */
  onSettled?: (doc: DocumentRecord) => void;
}

export function useDocumentPoll(documentId: string | null, options: Options = {}) {
  const { intervalMs = DEFAULT_INTERVAL_MS, onSettled } = options;

  const [doc, setDoc] = React.useState<DocumentRecord | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [refreshKey, setRefreshKey] = React.useState(0);

  const settledRef = React.useRef(false);
  const onSettledRef = React.useRef(onSettled);
  onSettledRef.current = onSettled;

  const refresh = React.useCallback(() => setRefreshKey((k) => k + 1), []);

  React.useEffect(() => {
    if (!documentId) {
      setDoc(null);
      setError(null);
      return;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    settledRef.current = false;
    setLoading(true);

    async function tick() {
      try {
        const next = await getDocument(documentId!);
        if (cancelled) return;
        setDoc(next);
        setError(null);
        if (isTerminal(phaseOf(next))) {
          if (!settledRef.current) {
            settledRef.current = true;
            onSettledRef.current?.(next);
          }
          return; // terminal: no further polls
        }
        timer = setTimeout(tick, intervalMs);
      } catch (e: unknown) {
        if (cancelled) return;
        // A transient poll failure is reported but does not stop the poll: the
        // worker is still running whether or not this request reached the API.
        setError(e instanceof Error ? e.message : "Failed to load document");
        timer = setTimeout(tick, intervalMs);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [documentId, intervalMs, refreshKey]);

  return { doc, error, loading, refresh };
}
