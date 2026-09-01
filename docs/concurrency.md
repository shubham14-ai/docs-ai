# Concurrency

> Related: [`flow.md`](flow.md) · [`failure-handling.md`](failure-handling.md) · [`testing.md`](testing.md)

---

## Every race and its guard

| Race | Guard |
|---|---|
| Two workers pick up the same job | **Two independent guards:** stream delivers each entry to one consumer per group; separately, `findOneAndUpdate` filters on `status:queued` — only one CAS matches. Either alone can be defeated by redelivery after a timeout. |
| Worker dies mid-job | Lease expires → sweeper resets to `queued` and re-enqueues. `XAUTOCLAIM` recovers the stream entry. Both needed: document state and stream entry can strand independently. |
| Slow worker finishes after being reclaimed | Its `complete` CAS filters on `worker_id`, no longer matches — stale result dropped, not written. |
| Two concurrent submissions at the rate limit | Atomic Lua check-and-increment. `GET` then `INCR` lets both read 2, both proceed, user ends with 4 active documents against a limit of 3. |
| Identical content submitted simultaneously | `SET NX inflight:{user}:{hash}`. One enqueues; the rest receive that document id. Cache cannot cover this — nothing is cached until processing ends. |
| Rate-limit counter drifts after a crash | Counter is an accelerator. Sweeper recomputes every counter from MongoDB — a crash cannot permanently consume a user's capacity. |
| Job succeeds but ACK is lost | Redelivery is harmless: CAS fails because status is no longer `queued`. Worker ACKs and drops. Idempotent by construction. |

These are **tested, not just asserted** — [`tests/integration/test_worker_races.py`](../tests/integration/test_worker_races.py) races ten simultaneous claims at one document and expects exactly one winner. The guarantee comes from MongoDB's atomicity; a fake would test the fake.

---

## The rule underneath all of them

**State transitions are compare-and-set.** The filter always names the status the caller believes the document is in. A transition that lost a race returns `False` and never overwrites.

---

## Why the worker is a separate process

`BackgroundTasks` runs inside the API process — an API restart silently kills a 10–30s job mid-flight and leaves the client polling a document that will never move. A separate process scales independently (`./doc-ai.sh scale 3`), survives API deploys, and drains gracefully on SIGTERM.

---

## Bounded concurrency inside one worker

An async generator yields only documents the worker has successfully claimed. A semaphore acquired **before** `create_task` turns the concurrency limit into backpressure: a saturated worker stops reading the stream, leaving unclaimed work in Redis for another worker rather than piling it up in memory.

`TaskGroup` over fire-and-forget `create_task`: an exception in a detached task is silently discarded — the "worker swallows exceptions" pitfall exactly.

| Axis | Knob |
|---|---|
| Documents in flight per process | `WORKER_CONCURRENCY` (default 10) |
| Worker processes | `./doc-ai.sh scale N` |

---

## Decorators — cross-cutting concerns

| Decorator | Does |
|---|---|
| `@log_stage` | structlog entry/exit; binds `request_id`, `document_id`, `user_id` via `contextvars` |
| `@timed` | Duration as a log field |
| `@retryable` | Exponential backoff with jitter, only for transient-classified errors |
| `@guarded` | Converts unexpected exceptions to typed pipeline errors — the structural reason no `except Exception: pass` exists |
