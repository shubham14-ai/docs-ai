/**
 * The single status model the UI renders from.
 *
 * The backend owns four states -- queued, processing, completed, failed. The
 * two client-side phases in front of them ("upload" in flight, "uploaded"
 * accepted) exist only for the duration of a submission: they describe the
 * POST, not the document. Everything after `uploaded` is read from the
 * document the API returns, never from a timer.
 */
import type { DocumentRecord, DocumentStatus } from "./types";

export type Phase =
  | "upload"
  | "uploaded"
  | "queued"
  | "processing"
  | "completed"
  | "failed";

export interface StepDef {
  phase: Phase;
  label: string;
  description: string;
}

/** The happy path, in order. `failed` is an error state on the reached step. */
export const STEPS: StepDef[] = [
  { phase: "upload", label: "Upload", description: "Submit the document" },
  { phase: "uploaded", label: "Uploaded", description: "Received and persisted" },
  { phase: "queued", label: "Queued", description: "Waiting for a worker" },
  { phase: "processing", label: "Processing", description: "Being summarized" },
  { phase: "completed", label: "Completed", description: "Summary ready" },
];

const ORDER: Phase[] = ["upload", "uploaded", "queued", "processing", "completed"];

/** Index into STEPS. `failed` reports the last step actually reached. */
export function stepIndex(phase: Phase, lastReached: Phase = "processing"): number {
  if (phase === "failed") return Math.max(0, ORDER.indexOf(lastReached));
  return Math.max(0, ORDER.indexOf(phase));
}

export const TERMINAL: ReadonlySet<Phase> = new Set<Phase>(["completed", "failed"]);

export function isTerminal(phase: Phase): boolean {
  return TERMINAL.has(phase);
}

/** A persisted document is always at or past `queued`. */
export function phaseOf(doc: DocumentRecord): Phase {
  return doc.status as Phase;
}

export const STATUS_COLOR: Record<
  DocumentStatus,
  "default" | "info" | "success" | "error"
> = {
  queued: "default",
  processing: "info",
  completed: "success",
  failed: "error",
};

export const STATUS_LABEL: Record<DocumentStatus, string> = {
  queued: "Queued",
  processing: "Processing",
  completed: "Completed",
  failed: "Failed",
};
