# Failure Handling

> Related: [`concurrency.md`](concurrency.md) · [`architecture.md`](architecture.md) · [`testing.md`](testing.md)

---

## Transient vs terminal

Every error is an `AppError` subclass with a stable `code`, an `http_status`, and a **transient/terminal** flag. That flag is the single input to "retry or give up" — the decision lives with the error, not re-derived at each call site.

The brief's ~10% random failure is treated as **transient**: a permanent failure injected at random would be indistinguishable from a bug, and retrying is what a real service does with a flaky downstream.

---

## Retry flow

A failed attempt:
1. Increments `attempts` on the document.
2. Returns it to `queued`.
3. Schedules a retry in a Redis ZSET scored by "ready at" (`app/queue/delay.py`).

After `MAX_ATTEMPTS` (default 3) → `failed`, typed error recorded, rate-limit slot released.

Retries go into the ZSET, not `asyncio.sleep` in place. A job sleeping its backoff holds a concurrency slot doing nothing and dies with the process. The ZSET survives a restart; the sweeper promotes entries once they are due.

Backoff is exponential **with full jitter** — without jitter, a burst of simultaneous failures retries in lockstep, recreating the herd that caused it.

---

## Nothing is silently swallowed

No `except Exception: pass` anywhere. The catch-all exists in exactly two places:

- `@guarded` decorator ([`app/core/decorators.py`](../app/core/decorators.py)) — converts unexpected exceptions to typed pipeline errors, logs the traceback.
- Unhandled-exception handler ([`app/api/errors.py`](../app/api/errors.py)) — same, for the API layer.

Everywhere else the catch is narrow and intentional: `REDIS_ERRORS` for degradation, `AppError` for domain failures.

---

## Redis degradation — per concern, not globally

| Concern | Without Redis |
|---|---|
| Content cache | Miss and process normally — correct, just slower |
| Rate limiting | **MongoDB count fallback** over `(user_id, status)` — the index exists for this. Slower, still correct. Failing open removes the limit; failing closed takes the API down. MongoDB is the source of truth anyway. |
| Queue (enqueue) | **503** — accepting work that cannot be enqueued would silently lose it |
| Health endpoint | Reports `degraded` with the specific failing dependency |

Verified live:
```
$ docker compose stop redis && curl -i localhost:8000/health
HTTP/1.1 503
{"status":"degraded","dependencies":{
  "mongodb":{"status":"up","latency_ms":1.4},
  "redis":{"status":"down","error":"ping timed out"}}}
```

---

## Rate-limit slot lifecycle

| Outcome | Slot |
|---|---|
| `completed` | Released |
| `failed` (attempts exhausted) | Released |
| Awaiting retry (`queued`) | Held — still active work |
| Worker died mid-job | Held until lease expires; sweeper reconciles from MongoDB |
| Cache hit or collapsed duplicate | Never consumed |

---

## Health check

`GET /health` actively pings both dependencies with a short timeout and reports each separately. Returns **503** when any dependency is down — a health check that always returns 200 is decoration.
