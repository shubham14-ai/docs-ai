# Architecture

> Related: [`flow.md`](flow.md) · [`concurrency.md`](concurrency.md) · [`api.md`](api.md)

---

## System diagram

```
Client
  │  HTTP
  ▼
┌──────────────────────────────────────┐
│  API  (FastAPI · uvicorn)            │
│  validate → hash → guards → enqueue  │
└───────────┬──────────────┬───────────┘
            │              │
        MongoDB           Redis
     (source of truth)  (accelerator)
            │              │
            │        XREADGROUP
            │              │
       ┌────▼──────────────▼────┐
       │  Worker  (N replicas)  │
       │  claim → process → ack │
       │  Sweeper  (timer)      │
       └────────────────────────┘
```

Three processes, one Compose file. The API never does processing work — it validates, guards, and enqueues. Workers consume the stream and write results back to MongoDB. The Sweeper runs inside each worker on a timer and repairs state left inconsistent by crashes.

**Redis is an accelerator, never the record.** Every value Redis holds is recomputable from MongoDB. Per-concern degradation means Redis going down does not take the service down — see [`failure-handling.md`](failure-handling.md).

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12 | `asyncio.TaskGroup`, `contextvars` — used genuinely, not decoratively |
| API framework | FastAPI | Pydantic-native, async-first, OpenAPI out of the box |
| ASGI server | uvicorn | Standard FastAPI pairing; `--reload` in dev via bind mount |
| ODM | Beanie (Motor + Pydantic) | Typed async MongoDB access; declarative indexes; models shared with API layer |
| Validation | Pydantic v2 | Request/response schemas, settings, domain model — one layer throughout |
| Settings | pydantic-settings | Environment-driven; no hardcoded connection strings |
| Logging | structlog | Structured JSON; `contextvars` binds `request_id` across the call stack |
| Primary store | MongoDB 7 | Source of truth; atomic `findOneAndUpdate` is the CAS primitive |
| Cache / queue | Redis 7 | Rate-limit counter · content cache · Streams queue · ZSET retry scheduler |
| Queue primitive | Redis Streams + consumer group | Push delivery, per-consumer ownership, `XAUTOCLAIM` for dead-worker recovery |
| Containers | Docker multi-stage + Compose | `base → deps → dev-deps → dev / prod`; source never baked in dev mode |
| UI | Next.js 15 + MUI | Viewer dashboard on `:3000`; not part of the brief |
| Tests | pytest + httpx.AsyncClient | Integration tests against the real app; real MongoDB + Redis for concurrency proofs |

---

## Process roles

**API** (`app/api/`) — handles every HTTP request, does no processing work. Validates, hashes content, runs three Redis guards, writes to MongoDB, publishes to the stream. Returns in single-digit milliseconds.

**Worker** (`app/workers/`) — separate process, N replicas. Reads the stream via `XREADGROUP`, claims documents with a MongoDB compare-and-set, processes up to `WORKER_CONCURRENCY` (default 10) concurrently via `asyncio.TaskGroup` + semaphore backpressure. On success: writes summary, caches it, decrements slot, ACKs. On failure: schedules retry in ZSET with exponential + jitter backoff.

**Sweeper** (`app/workers/sweeper.py`) — timer inside each worker (default 15s). Reclaims expired leases, promotes due retries, re-enqueues orphaned `queued` documents, reconciles rate-limit counters against MongoDB.

---

## Repository layout

```
app/
├── main.py                  # app factory + lifespan (DI wiring, index ensure)
├── config.py                # pydantic-settings — all env vars
├── db.py                    # MongoDB + Redis init, REDIS_ERRORS tuple
├── api/
│   ├── router.py            # only place a version prefix is applied
│   ├── deps.py              # FastAPI DI providers
│   ├── errors.py            # exception handlers → error envelope
│   ├── meta.py              # GET /api/versions
│   └── v1/
│       ├── documents.py     # POST /documents · GET /documents/{id}
│       ├── users.py         # GET /users/{id}/documents
│       └── health.py        # GET /health (unversioned)
├── core/
│   ├── decorators.py        # @stage · @timed · @retryable · @guarded
│   ├── exceptions.py        # typed AppError hierarchy
│   └── logging.py           # structlog config + request-id middleware
├── models/document.py       # Beanie document model + index declarations
├── schemas/document.py      # Pydantic request/response shapes
├── services/
│   ├── ingestion.py         # full submit flow
│   ├── cache.py             # content cache, degradation-aware
│   ├── ratelimit.py         # Lua check-and-increment + MongoDB fallback
│   └── summarizer.py        # mock — single seam for a real LLM
├── queue/
│   ├── stream.py            # XADD · XREADGROUP · XACK · XAUTOCLAIM
│   └── delay.py             # ZSET retry scheduler
├── workers/
│   ├── consumer.py          # async generator + TaskGroup loop
│   ├── pipeline.py          # claim → process → complete
│   └── sweeper.py           # lease reclaim + counter reconciliation
└── repositories/document.py # all MongoDB access; atomic claim lives here
tests/
├── unit/                    # hashing · rate-limit logic · decorators · validation
├── integration/             # full HTTP surface, concurrency races, failure paths
└── conftest.py              # fixtures, fakes, config overrides
```

`summarizer.py` is isolated as the single seam where a real LLM replaces the mock — a one-file change.

---

## Key design decisions

| Decision | Rationale |
|---|---|
| Separate worker, not `BackgroundTasks` | `BackgroundTasks` ties job lifetime to the API process — a deploy silently kills in-flight work. Separate process scales independently and survives API restarts. |
| Redis Streams + consumer group | Push delivery, per-consumer ownership, `XAUTOCLAIM` for dead-worker recovery. Redis was already a required dependency. |
| Per-capability versioning | A global version couples every endpoint to the slowest-moving one. Version prefix applied in exactly one place: `app/api/router.py`. |
| Rate limit checked last | Content cache and duplicate guard answer "not new work" first. Capacity only charged for work that will actually run. |
| Atomic Lua rate-limit check | `GET` then `INCR` has a TOCTOU race. Lua makes check-and-increment one atomic step. |
| Per-concern Redis degradation | Rate limit → MongoDB count. Cache → miss. Queue → 503. Each concern fails independently. |
| Two double-processing guards | Stream delivers to one consumer per group AND MongoDB CAS filters on `status:queued`. Either alone can be defeated by redelivery after a timeout. |
| MongoDB as source of truth | Redis holds nothing that cannot be recomputed. Sweeper reconciles any drift. A Redis wipe loses no data. |
