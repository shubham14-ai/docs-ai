# Testing

> Related: [`concurrency.md`](concurrency.md) · [`failure-handling.md`](failure-handling.md)

```bash
./doc-ai.sh test          # 109 passed, ~20s
./doc-ai.sh test unit
./doc-ai.sh test integration
```

---

## Suites

| Suite | Proves |
|---|---|
| `unit/test_hashing.py` | Content normalization and SHA-256 consistency |
| `unit/test_summarizer.py` | Template rendering, duplicate provenance, copy-of-copy cites the original processed upload |
| `unit/test_decorators.py` | `@retryable` retry counts, `@guarded` never swallows, jittered backoff distribution |
| `unit/test_validation.py` | Rejection side of every input constraint |
| `unit/test_ratelimit_degradation.py` | Rate limiter falls back to MongoDB with a deliberately broken Redis |
| `integration/test_api_documents.py` | Status codes, error envelope shape, `request_id` propagation, malformed ids, health |
| `integration/test_listing.py` | Pagination totals, status filtering, ordering, boundary values |
| `integration/test_rate_limit.py` | Sequential limit · **6 simultaneous submissions → exactly 3 accepted** · per-user isolation · slot release on completion |
| `integration/test_caching.py` | Cache hit → 200, no slot consumed, per-user scoping, survives Redis eviction via MongoDB · **5 identical simultaneous submissions → 1 enqueue, 1 shared id** · duplicate provenance in summary |
| `integration/test_worker_races.py` | **10 simultaneous claims → exactly 1 winner** · redelivery is a no-op · stale result cannot overwrite reclaimed document · sweeper requeues abandoned job · counter reconciliation |
| `integration/test_failure_handling.py` | Retry holds slot · attempts exhaust to `failed` · terminal failure releases slot · due retries promoted |

---

## Why real infrastructure

Concurrency guarantees come from MongoDB's `findOneAndUpdate` atomicity and Redis's single-threaded Lua execution. A fake would test the fake, not the guarantee.

Integration tests skip with a clear message when MongoDB or Redis are unreachable — a bare `pytest` outside Compose still runs the unit suite.

---

## Hygiene

- Processing durations are **config** (`PROCESSING_MIN/MAX_SECONDS=0` in tests) — the suite runs in ~20s, not minutes.
- Tests use a `_test` database suffix and a per-test stream key; fixtures drop both on teardown.
- No version string in any test — URLs built through [`tests/urls.py`](../tests/urls.py).
- Table-driven cases in [`tests/fixtures/`](../tests/fixtures/) as JSON — adding a case is data, not code.

---

## Known gaps

- Positive-case fixtures (`valid_user_id_cases`, `valid_title_cases`, etc.) are declared in `tests/fixtures/validation.json` but no test consumes them — a constraint tightening would leave the suite green while breaking production.
- Non-ASCII content produces `word_count=0` / `keywords=[]` due to an ASCII-only tokenizer (`[A-Za-z0-9']+`) — no test pins this behaviour.
- The service-level content limit (100 KB, below the schema's 1 MB) has no integration test covering the 100 KB–1 MB band.
