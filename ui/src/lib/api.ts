/**
 * Thin API client. All paths go through Next.js rewrites → backend API,
 * so no CORS issues in any environment.
 */
import type { DocumentRecord, Page, SubmitPayload } from "./types";

const BASE = "/api/v1";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const msg: string =
      body?.error?.message ?? `HTTP ${res.status} ${res.statusText}`;
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

/** POST /api/v1/documents */
export async function submitDocument(payload: SubmitPayload): Promise<DocumentRecord> {
  const res = await fetch(`${BASE}/documents`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return json<DocumentRecord>(res);
}

/** GET /api/v1/documents/:id */
export async function getDocument(id: string): Promise<DocumentRecord> {
  const res = await fetch(`${BASE}/documents/${id}`);
  return json<DocumentRecord>(res);
}

/** GET /api/v1/users/:userId/documents */
export async function listDocuments(
  userId: string,
  page = 1,
  pageSize = 20,
  status?: string
): Promise<Page<DocumentRecord>> {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  });
  if (status) params.set("status", status);
  const res = await fetch(`${BASE}/users/${encodeURIComponent(userId)}/documents?${params}`);
  return json<Page<DocumentRecord>>(res);
}
