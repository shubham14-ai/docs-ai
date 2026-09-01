# UI Dashboard

`http://localhost:3000` — a Next.js 15 / MUI viewer in [`ui/`](../ui/). Started automatically by `./doc-ai.sh init`.

---

## What it is

A thin client for the API. It exists to make the async behaviour **visible**: submit a document, watch it move through `queued` → `processing` → `completed` (or exhaust retries to `failed`). A 429 and a cache hit are equally observable.

| Component | Does |
|---|---|
| `DocumentsDashboard.tsx` | Paginated, filterable table over `GET /users/{id}/documents`, polling for status changes |
| `UploadDialog.tsx` | `POST /documents`, surfaces the error envelope's `message` verbatim |
| `DocumentDetailModal.tsx` | Single document view — summary text and provenance |
| `lib/api.ts` | Entire API surface in ~4 functions |

---

## What it is not

Not part of the assignment. The brief asks for an API; this is a viewer. It holds no state the API does not own and makes no decision the API does not make.

---

## How it reaches the API

All calls go to a relative `/api/v1` path, rewritten server-side by Next.js to `NEXT_PUBLIC_API_URL` (the `api` container inside Compose, `localhost:8000` outside). The browser never needs to know an internal Docker hostname — no CORS configuration in any environment.

---

## Running standalone

```bash
cd ui && npm install && npm run dev
```

Expects the API on `localhost:8000`. Change the host port with `UI_PORT` — see [`configuration.md`](configuration.md).
