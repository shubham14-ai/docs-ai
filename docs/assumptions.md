# Assumptions

> Related: [`api.md`](api.md) · [`data-model.md`](data-model.md) · [`future-plan/`](future-plan/README.md)

Every ambiguity in the brief resolved by assumption and documented. Each one below is a reading that could have gone the other way.

1. **Cache is scoped per user** — from the wording *"if a user submits a document with identical content"*. A global key hits more often but serves one tenant's summary to another — a data-isolation decision that should be explicit. Revisiting it: [`future-plan/12-open-architectural-decisions.md`](future-plan/12-open-architectural-decisions.md) §1.

2. **A cache hit creates a new document record** in `completed` state rather than returning the older `document_id`. Each submission stays independently addressable and appears in the user's list. Its summary is re-attributed and marked as a duplicate of the upload that was processed.

3. **A cache hit does not consume a rate-limit slot** — no processing capacity is used. Neither does a collapsed duplicate.

4. **The 10% simulated failure is transient** and therefore retried. A permanent failure injected at random would be indistinguishable from a bug. See [`failure-handling.md`](failure-handling.md).

5. **Rate limiting counts `queued` + `processing`** exactly as worded. `completed` and `failed` release the slot; a document awaiting a retry is still `queued` and still holds its slot.

6. **`user_id` is client-supplied and unauthenticated** — no auth is specified. A real deployment derives it from a token; the limit is trivially bypassed otherwise. It is pattern- and length-constrained because it becomes a Redis key and a Mongo index value.

7. **An unknown user lists as an empty page, not a 404.** There is no user resource that could be missing.

8. **Duplicate-submission 409 is narrow** — only when the winning request holds the in-flight guard but has not yet written its document (~500ms window). The caller receives `SUBMISSION_SETTLING` and the id to poll, rather than a misleading 404.

9. **Endpoints are versioned** even though the brief writes them unprefixed. An API that will change deserves a version from day one; adding it later is the expensive time. See [`api.md`](api.md#versioning).
