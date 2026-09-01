# Flow

> Related: [`architecture.md`](architecture.md) · [`concurrency.md`](concurrency.md) · [`failure-handling.md`](failure-handling.md)

---

## Submit — `POST /api/v1/documents`

Does no processing work. Returns in single-digit milliseconds.

```
validate → normalize → sha256(content)
    │
    ├─ cache hit?        → re-attribute summary, insert completed → 200
    ├─ in-flight dupe?   → return existing document_id to poll   → 200
    ├─ at rate limit?    → release guard, Retry-After            → 429
    └─ new work          → insert queued, XADD stream            → 201
```

Three Redis guards run in order:

1. **Content cache** `GET cache:summary:{user}:{hash}` — identical content already summarized. The stored summary is re-attributed to the new upload (`source=cache`, `origin=` the upload that was processed) and a new `completed` record is inserted. No slot consumed.
2. **In-flight duplicate** `SET NX inflight:{user}:{hash} EX 300` — identical content already being processed. Returns the existing `document_id` to poll. No slot consumed.
3. **Rate limit** atomic Lua check-and-increment on `ratelimit:active:{user}`. 429 if the user already has 3 documents in `queued` or `processing`. Checked last — the first two guards answer "not new work"; capacity is only charged for work that will actually run.

The `document_id` is minted before the document is written so the guard key can reference it. A duplicate arriving in the ~500ms window before the write completes receives **409 SUBMISSION_SETTLING** rather than a misleading 404.

---

## Worker consume loop

```
XREADGROUP (block 5s)
    │
    └─ for each entry:
           findOneAndUpdate {_id, status:queued} → processing + lease + worker_id
               │
               ├─ null (another worker won CAS) → XACK, drop
               └─ claimed → process concurrently (asyncio.TaskGroup + semaphore)
                       │
                       ├─ success  → write summary, cache it, DECR slot, XACK
                       └─ failure  → attempts++, schedule retry in ZSET, XACK
                                         └─ attempts == MAX → status=failed, release slot
```

The async generator yields only documents the worker has successfully claimed. A semaphore acquired **before** `create_task` propagates backpressure into the generator — a saturated worker stops reading the stream, leaving unclaimed work in Redis for another worker.

`TaskGroup` over bare `create_task`: an unhandled exception propagates rather than being silently discarded.

---

## Document state machine

```
[new] → queued → processing → completed
                     │
                     ├─ transient failure, attempts remain → queued (retry delay)
                     ├─ attempts exhausted                 → failed
                     └─ lease expired / worker died        → queued (sweeper)
```

---

## Sweeper (inside each worker, every 15s)

Repairs state left inconsistent by crashes:

- Expired leases → reset to `queued`, re-enqueue in stream
- Due retries in ZSET → promote back to stream
- `queued` documents with no stream entry → re-enqueue (lost entry recovery)
- Every rate-limit counter → reconcile against live MongoDB count
