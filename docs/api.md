# API Reference

> Interactive OpenAPI at [`/docs`](http://localhost:8000/docs) once the stack is up.
> Related: [`data-model.md`](data-model.md) · [`flow.md`](flow.md)

---

## Versioning

Routes live under `/api/v1`. The version prefix is applied **once**, in [`app/api/router.py`](../app/api/router.py):

```python
CAPABILITIES = (
    Capability("documents", {"v1": documents_v1.router}),
    Capability("users",     {"v1": users_v1.router}),
)
```

Versioning is **per capability, not global** — `documents` can move to v2 while `users` stays on v1. No handler, schema, test, or log line contains the string `v1`. Moving a capability to v2 means adding one module and one dict entry.

`/health` and `/api/versions` are unversioned: a liveness probe must not break on an API release; a discovery document that needed discovering would be circular.

---

## Endpoints

| Method | Path | Returns |
|---|---|---|
| `POST` | `/api/v1/documents` | 201 · 200 · 409 · 422 · 429 · 503 |
| `GET` | `/api/v1/documents/{document_id}` | 200 · 404 |
| `GET` | `/api/v1/users/{user_id}/documents` | 200 paginated |
| `GET` | `/health` | 200 · 503 |
| `GET` | `/api/versions` | current version per capability |

### Status codes

| Code | When |
|---|---|
| **201** | Document accepted and queued |
| **200** | Cache hit (already summarized) or collapsed duplicate (already in flight) |
| **409** | `SUBMISSION_SETTLING` — guard taken, document not yet written; poll the returned id |
| **422** | Validation failure — bad request body or query params |
| **429** | Rate limit — user already has 3 documents in `queued` or `processing` |
| **503** | MongoDB or Redis is down |
| **404** | Unknown id or malformed id — never a 500 from a failed `ObjectId()` construction |

---

## Examples

```bash
# Submit
curl -X POST localhost:8000/api/v1/documents \
  -H 'content-type: application/json' \
  -d '{"user_id":"user-1","title":"Q3 report","content":"Revenue grew. Costs fell."}'

# Poll
curl localhost:8000/api/v1/documents/<document_id>

# List with filter
curl "localhost:8000/api/v1/users/user-1/documents?page=1&page_size=20&status=completed"
```

---

## Error envelope

Every error uses one shape — never a bare string:

```json
{
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "User already has 3 documents in progress (limit 3).",
    "details": {"limit": 3, "active": 3, "retry_after_seconds": 15},
    "request_id": "606cdac7dee34d16828ccc60c52840dd"
  }
}
```

- `code` — stable, machine-readable
- `message` — human-readable
- `details` — whatever the client needs to act (retry timing, limit values, etc.)
- **429** also carries a `Retry-After` header — a client without it will hot-loop

Every error is an `AppError` subclass with a `code`, `http_status`, and transient/terminal flag. No ad-hoc `HTTPException`s in handlers.

---

## Validation

Beyond Pydantic defaults: `content` non-empty after strip + max length, `title` length-bounded, `user_id` pattern-constrained (becomes a Redis key and Mongo index value), `page` ≥ 1, `page_size` 1–100, `status` filter constrained to the enum. Every constraint is on the model — enforced and self-documenting in OpenAPI.

---

## Cache hits

A cache hit inserts a new `completed` record — each submission stays independently addressable. The summary is re-rendered with a provenance line:

```
Provenance: DUPLICATE UPLOAD - identical content (e9228d8cf2e2081f) was already
summarized and served from cache. Original: 6a969550... "Q3 report" submitted
2026-09-01T09:05:20+00:00 by user-1.
```

A copy of a copy cites the upload that did the work, not the copy in between (`find_completed_by_hash` returns the oldest `from_cache: false` record).
