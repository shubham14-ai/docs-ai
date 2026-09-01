# CLAUDE.md

Project context for agents working in this repository.

## What this is

A **Document Insights API**: submit document text, it is summarized
asynchronously by a background worker, poll for the result. Built for a
take-home assignment (`docs/BE_Take_Home_Document_Insights_Api.pdf`), scoped to
3-5 hours.

The functional surface is small on purpose. The assignment states the
requirements are "intentionally straightforward" and that *how* it is built is
what is assessed: API design, data modeling, async processing, caching,
production readiness.

**No AI/LLM integration.** `app/services/summarizer.py` is a deterministic mock
and the only seam where a real model would go.

## Source of truth

Read these before changing anything structural:

| Document | What it settles |
|---|---|
| `docs/BE_Take_Home_Document_Insights_Api.pdf` | The requirements. The final word on scope. |
| `docs/README.md` | **The index.** Every note in the vault, one line each. Start here. |
| `docs/00-flow.md` | Architecture, data model, submit/worker flows, race table, rubric mapping |
| `docs/01-api-versioning.md` | Why routes live under `/api/v1` and how versions are composed |
| `README.md` | Reviewer-facing summary: quickstart, architecture, the decisions that matter |

`docs/` is a linked note vault — one note per concern, cross-linked with
relative markdown links so it renders identically on GitHub and in Obsidian. A
fact lives in exactly one note; everything else points at it. When adding
documentation, add a note and a row in `docs/README.md` rather than growing the
root README. Anything past the brief's ceiling goes in `docs/future-plan/`.

## Commands

All Docker operations go through `doc-ai.sh` (`doc-ai.ps1` is a Windows
wrapper that forwards to it). Run it with no arguments for the full list.

```bash
./doc-ai.sh init                   # build + start: mongo + redis + api + one worker
./doc-ai.sh up | down | restart    # lifecycle
./doc-ai.sh scale 3                # more processing capacity, same API
./doc-ai.sh logs worker            # follow one service
./doc-ai.sh health                 # api / mongo / redis
./doc-ai.sh test                   # the whole suite, with a per-suite report
./doc-ai.sh test unit              # one level: unit | integration | edge
./doc-ai.sh test release           # CI gate; non-zero exit if anything fails
./doc-ai.sh build                  # only when requirements*.txt change
```

**Do not rebuild to pick up a code change.** The Dockerfile is multi-stage
(`base -> deps -> dev-deps -> dev | prod`) and in dev `docker-compose.override.yml`
bind-mounts `./app` read-only over the baked-in copy: uvicorn `--reload` and the
worker's `watchfiles` supervisor restart in place. The `ui` service works the
same way — `ui/Dockerfile` has its own `dev` stage running `next dev`, with
`ui/src` mounted over it (`tsconfig.json` is mounted writable: `next dev`
rewrites it on startup and exits if it cannot). Only a dependency change
invalidates a layer.

Watching is **polled** in the UI container (`WATCHPACK_POLLING`): inotify events
do not cross a bind mount from a Windows host, so without it `next dev` keeps
serving the last compile and edits silently never appear. `MODE=prod ./doc-ai.sh up` runs the `prod` stage instead
(source baked in, no test deps, no reloader) by not applying the override file.

The API is on `http://localhost:8000`, OpenAPI at `/docs`.

Tests need a real MongoDB and Redis and **skip themselves** with a clear message
when those are unreachable, so bare `pytest` outside Compose still works (unit
tests run, integration tests skip).

Every run prints a tree grouped suite -> file -> test and writes
`test-results/` (`junit.xml`, `summary.json`, `report.txt`, `html/index.html`).
That reporting lives entirely in `tests/report_plugin.py`, loaded by the root
`conftest.py`; it observes the report stream and never changes what runs.
Boundary tests carry `@pytest.mark.edge` so they are both selectable and
visible in the report. See `docs/14-test-reporting.md`.

## Architecture in one paragraph

FastAPI writes to MongoDB (source of truth) and publishes a document id to a
Redis Stream. A **separate worker process** consumes the stream with a consumer
group, claims each document with an atomic Mongo compare-and-set, sleeps to
simulate work, writes the summary, and releases the user's rate-limit slot. A
sweeper inside the worker repairs stranded state on a timer. Redis holds three
things in three namespaces: the rate-limit counter, the content cache, and the
queue.

```
app/
├── main.py            app factory + lifespan (clients, indexes, consumer group)
├── config.py          pydantic-settings; every tunable, no hardcoded strings
├── db.py              Mongo/Redis client construction, REDIS_ERRORS tuple
├── api/
│   ├── router.py      version composition (per capability, see 01-api-versioning)
│   ├── deps.py        DI providers
│   ├── errors.py      exception handlers -> one error envelope; request-id middleware
│   └── v1/            documents.py · users.py · health.py
├── core/              logging.py · decorators.py · exceptions.py
├── models/            Beanie document + declarative indexes
├── schemas/           request/response models
├── repositories/      ALL Mongo access, including the atomic claim
├── services/          ingestion · cache · ratelimit · summarizer
├── queue/             stream.py (Redis Streams) · delay.py (retry ZSET)
└── workers/           consumer.py · pipeline.py · sweeper.py · main.py
```

## Rules

1. **Never `except Exception: pass`.** It is a named pitfall in the brief. The
   catch-all is written once, in `@guarded` and in the API's unhandled-exception
   handler, and both log and re-raise or convert to a typed error. Catch
   `REDIS_ERRORS` for degradation, `AppError` for domain failures.
2. **Every error is an `AppError` subclass** with a `code`, an `http_status` and
   a `transient` flag. No bare strings, no ad-hoc `HTTPException` in handlers.
3. **All MongoDB access goes through `repositories/`.** Nothing else touches the
   collection.
4. **State transitions are compare-and-set.** The filter always names the status
   the caller believes the document is in. A transition that lost a race returns
   `False`; it never overwrites.
5. **Redis is an accelerator, never the record.** Anything Redis holds must be
   recomputable from MongoDB, and the sweeper must be able to repair it.
6. **No hardcoded connection strings or magic numbers.** Add a field to
   `Settings` and document it in `.env.example`.
7. **No version string outside `app/api/router.py`** — not in handlers, schemas,
   logs or tests. Tests build URLs through `tests/urls.py`.
8. **Scope.** The brief is the ceiling. Ideas beyond it belong in the README's
   "with more time" section, not in the code.

## Key decisions (and why, briefly)

- **Separate worker process, not `BackgroundTasks`** — an API deploy would
  silently kill in-flight 10-30s jobs, and capacity could not scale
  independently.
- **Redis Streams, not a Mongo polling loop** — Redis is already required, and a
  consumer group gives push delivery, ownership and `XAUTOCLAIM` recovery.
- **Two independent guards against double-processing** — the stream delivers to
  one consumer, *and* the Mongo claim filters on `status: queued`. Either alone
  can be defeated by redelivery.
- **Atomic Lua for the rate limit** — `GET` then `INCR` lets two concurrent
  submissions both pass at the boundary.
- **Rate limit is checked last** in the submit flow, after the cache and the
  duplicate guard: capacity is only charged for work that will actually run.
- **Cache is scoped per user** — a data-isolation decision, documented in the
  README rather than made incidentally.
- **Summary text is one template, filled from the document it belongs to** — so
  a summary served from the cache states that the upload was a duplicate and
  names the upload that was processed (`summary.source`, `summary.document`,
  `summary.origin`). `from_cache` alone is a flag a reader has to know to look
  for. `Summarizer.render` is the only place summary text is composed.
- **Retries go back on the queue with a delay**, not `sleep`-in-place, so a
  failing job does not hold a concurrency slot or die with the process.
- **`/health` is unversioned and returns 503 when a dependency is down** — a
  probe that always returns 200 is decoration.

## Gotchas

- **`tz_aware=True` on the Mongo client is required.** Without it the driver
  returns naive datetimes and every comparison with `datetime.now(timezone.utc)`
  raises.
- **The Redis socket timeout must outlast `XREADGROUP BLOCK`.** The worker
  derives its own longer timeout in `workers/main.py`; a shorter one turns every
  idle poll into a `TimeoutError` that kills the worker. This bug was real.
- **Processing durations are configuration**, so tests compress 10-30s to 0.
  Never hardcode a sleep.
- Test settings use a `_test` database suffix and a per-test stream key; the
  fixtures drop both on teardown.
