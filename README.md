# Document Insights API

A FastAPI service that accepts text documents, queues them for background processing, and returns AI-generated summaries. Built to demonstrate API design, async processing, caching, and production readiness.

---

## Architecture

Three processes, one Compose file.

```
Client → API (FastAPI · uvicorn)
              ↓ XADD
           Redis ←→ Worker (separate process, N replicas)
              ↓           ↓
           MongoDB  ←─────┘  (source of truth)
```

- The **API** validates, hashes content, applies three Redis guards (content cache → in-flight duplicate → rate limit), writes to MongoDB, and publishes to a Redis Stream. It returns in single-digit milliseconds.
- The **Worker** reads the stream via a consumer group, claims documents with a compare-and-set, processes them concurrently with `asyncio`, and writes results back to MongoDB.
- The **Sweeper** (inside the worker) repairs state after crashes: reclaims expired leases, promotes delayed retries, and reconciles rate-limit counters against MongoDB.
- **Redis is an accelerator, never the record.** Every Redis value is recomputable from MongoDB.

### Key design decisions

| Decision | Rationale |
|---|---|
| Separate worker process | Survives API deploys; scales independently; `BackgroundTasks` would silently kill in-flight jobs on restart |
| Redis Streams + consumer group | Push delivery, per-consumer ownership, `XAUTOCLAIM` for dead-worker recovery |
| Per-capability API versioning | `documents` can move to v2 without touching `users`; version prefix applied once in `app/api/router.py` |
| Rate limit checked last | Content cache and duplicate guard answer "not new work" first; capacity is only charged for work that will actually run |
| Atomic Lua rate-limit check | `GET` then `INCR` has a race; the Lua script makes check-and-increment one atomic step |
| Per-concern Redis degradation | Rate limit falls back to a MongoDB count; cache degrades to a miss; queue returns 503 rather than silently losing work |

---

## Setup

**Prerequisites:** Docker, Docker Compose.

```bash
cp .env.example .env        # optional — defaults work out of the box
./doc-ai.sh init            # build + start (first run)
./doc-ai.sh up              # start after the first run
```

| URL | What |
|---|---|
| `http://localhost:8000/api/v1/documents` | The API (versioned — see Assumptions) |
| `http://localhost:8000/docs` | Interactive OpenAPI |
| `http://localhost:8000/health` | Health check (503 if a dependency is down) |
| `http://localhost:3000` | Next.js dashboard |

```bash
./doc-ai.sh test            # full test suite (109 tests, ~20s)
./doc-ai.sh logs worker     # tail worker logs
./doc-ai.sh scale 3         # run 3 worker processes
```

See [DOCKER.md](DOCKER.md) for the full CLI reference and image details.

```bash
curl -i -X POST http://localhost:8000/api/v1/documents -H 'Content-Type: application/json' -d '{"user_id":"alice","title":"Notes","content":"The quarterly review covers revenue, churn and hiring."}'
```

Then poll `GET /api/v1/documents/{id}` and list with
`GET /api/v1/users/alice/documents?page=1&page_size=20&status=completed`.

---

## Assumptions

The brief leaves several things open; each was resolved deliberately. Full list with
reasoning in [`docs/assumptions.md`](docs/assumptions.md).

- **Routes are versioned** (`/api/v1/...`) even though the brief writes them unprefixed —
  an API that will change deserves a version on day one. `/health` stays unversioned so a
  probe never breaks on a release.
- **The content cache is scoped per user.** A global key hits more often but would serve
  one tenant's summary to another.
- **A cache hit still creates a new document record** (already `completed`), so every
  submission stays addressable and appears in the user's list; its summary text states that
  it is a duplicate and names the upload that was actually processed.
- **Neither a cache hit nor a collapsed duplicate consumes a rate-limit slot** — capacity is
  charged only for work that will actually run, which is why the limit is checked last.
- **The 10% simulated failure is transient**, so it is retried with backoff; a random
  permanent failure would be indistinguishable from a bug.
- **`user_id` is client-supplied and unauthenticated** (no auth in the brief), but is
  pattern- and length-constrained because it becomes a Redis key and a Mongo index value.
- **An unknown user lists as an empty page, not a 404** — there is no user resource to be
  missing.

## With more time

- **Authentication.** `user_id` in the body is trivially spoofable; the rate limit means
  little without a token the id is derived from.
- **Idempotency keys on submit**, so a client retrying through a timeout cannot create a
  second document for the same intent.
- **Metrics and tracing** — queue depth, time-to-completion, retry and failure rates as
  Prometheus counters; today the evidence is structured logs only.
- **A dead-letter path** for documents that exhaust `MAX_ATTEMPTS`, with an operator
  endpoint to inspect and requeue them.
- **Content stored outside the document record** (object storage + a pointer), so the
  collection stays small as documents grow.
- **Load testing the worker pool** to size `WORKER_CONCURRENCY` against real I/O rather
  than a defensible guess.
- **The full RAG evolution** — parsing, chunking, embeddings, retrieval, reranking,
  generation and evaluation — is planned in depth but deliberately unimplemented, since it
  sits beyond the brief's ceiling: [`docs/future-plan/`](docs/future-plan/README.md).

---

## Repository layout

```
app/
├── api/v1/          # route handlers (documents, users, health)
├── core/            # logging, decorators (@stage, @retryable, @guarded), exceptions
├── models/          # Beanie document model + indexes
├── schemas/         # Pydantic request/response models
├── services/        # ingestion flow, content cache, rate limiter, summarizer
├── queue/           # Redis Stream wrapper, delay ZSET
├── workers/         # consumer loop, pipeline, sweeper
└── repositories/    # all MongoDB access
tests/
├── unit/            # hashing, rate-limit logic, decorators, validation
└── integration/     # full HTTP surface, concurrency races, failure handling
docs/                # design notes (see docs/README.md)
ui/                  # Next.js dashboard (viewer only, not part of the brief)
```

`app/services/summarizer.py` is the single seam where a real LLM replaces the mock — a one-file change.

---

## Docs

| Note | Covers |
|---|---|
| [`docs/flow.md`](docs/flow.md) | Submit sequence, worker loop, state machine, sweeper |
| [`docs/architecture.md`](docs/architecture.md) | System diagram, tech stack, process roles, repo layout |
| [`docs/api.md`](docs/api.md) | Endpoints, versioning, status codes, error envelope, curl examples |
| [`docs/data-model.md`](docs/data-model.md) | `documents` collection, indexes, ObjectId handling |
| [`docs/concurrency.md`](docs/concurrency.md) | Every race condition and the guard that closes it |
| [`docs/failure-handling.md`](docs/failure-handling.md) | Transient vs terminal, retries, backoff, Redis degradation |
| [`docs/testing.md`](docs/testing.md) | What each suite proves; why concurrency tests use real MongoDB |
| [`docs/assumptions.md`](docs/assumptions.md) | Every ambiguity in the brief and the reading taken |
| [`docs/future-plan/`](docs/future-plan/README.md) | Work queued beyond the assignment's scope |
| [`docs/configuration.md`](docs/configuration.md) | All environment variables *(optional reference)* |
