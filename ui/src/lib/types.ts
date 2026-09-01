export type DocumentStatus = "queued" | "processing" | "completed" | "failed";

export interface DocumentRef {
  document_id: string;
  user_id: string;
  title: string;
  content_hash: string;
  submitted_at: string;
}

export interface Summary {
  text: string;
  lead: string;
  word_count: number;
  char_count: number;
  sentence_count: number;
  keywords: string[];
  reading_time_seconds: number;
  model: string;
  source: "generated" | "cache";
  document: DocumentRef | null;
  origin: DocumentRef | null;
}

export interface ProcessingError {
  code: string;
  message: string;
  attempts: number;
  failed_at: string;
}

export interface DocumentRecord {
  document_id: string;
  user_id: string;
  title: string;
  status: DocumentStatus;
  content_hash: string;
  summary: Summary | null;
  error: ProcessingError | null;
  attempts: number;
  from_cache: boolean;
  created_at: string;
  updated_at: string;
}

export interface PageMeta {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
  has_next: boolean;
}

export interface Page<T> {
  items: T[];
  meta: PageMeta;
}

export interface SubmitPayload {
  user_id: string;
  title: string;
  content: string;
}
