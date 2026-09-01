# Configuration

All values read by [`app/config.py`](../app/config.py) via `pydantic-settings`. Every variable is documented in [`.env.example`](../.env.example). No connection string or magic number lives in source — adding a tunable means a `Settings` field and a line in `.env.example`.

Settings are injected as a FastAPI dependency so tests override them without touching the environment.

---

## Dependencies

| Variable | Default | Notes |
|---|---|---|
| `MONGODB_URI` | `mongodb://mongo:27017` | Inside Compose, hostnames are service names |
| `MONGODB_DB` | `document_insights` | Tests append `_test` suffix |
| `REDIS_URL` | `redis://redis:6379/0` | |
| `REDIS_SOCKET_TIMEOUT` | `5` | Ordinary commands. Worker derives a longer timeout for its blocking read — must outlast `STREAM_BLOCK_SECONDS` or every idle poll raises `TimeoutError`. |

## Rate limiting

| Variable | Default | Notes |
|---|---|---|
| `MAX_ACTIVE_DOCS_PER_USER` | `3` | 429 above this |
| `RATE_LIMIT_TTL_SECONDS` | `86400` | Long enough to outlive any job; sweeper reconciles drift regardless |

## Content cache

| Variable | Default | Notes |
|---|---|---|
| `CACHE_TTL_SECONDS` | `3600` | Longer raises hit rate, shorter bounds staleness. Summaries are deterministic so a longer TTL is defensible. |
| `INFLIGHT_TTL_SECONDS` | `300` | In-flight duplicate guard TTL |

## Simulated processing

| Variable | Default | Notes |
|---|---|---|
| `PROCESSING_MIN_SECONDS` | `10` | Set to `0` in tests |
| `PROCESSING_MAX_SECONDS` | `30` | Set to `0` in tests |
| `FAILURE_RATE` | `0.1` | ~10% per the brief; set to `0` in tests |

## Worker

| Variable | Default | Notes |
|---|---|---|
| `MAX_ATTEMPTS` | `3` | Then `failed` |
| `RETRY_BASE_SECONDS` | `2` | Exponential base |
| `RETRY_MAX_SECONDS` | `60` | Backoff ceiling (with full jitter) |
| `WORKER_CONCURRENCY` | `10` | Documents in flight per process (asyncio). Scale processes with `./doc-ai.sh scale N`. |
| `STREAM_BLOCK_SECONDS` | `5` | How long `XREADGROUP` blocks waiting for work |
| `LEASE_SECONDS` | `120` | Claimed document assumed abandoned after this |
| `SWEEPER_INTERVAL_SECONDS` | `15` | How often the repair pass runs |
| `STALE_QUEUED_SECONDS` | `120` | `queued` document untouched this long is re-enqueued |

## Input validation

| Variable | Default |
|---|---|
| `MAX_CONTENT_LENGTH` | `100000` |
| `MAX_TITLE_LENGTH` | `300` |

## Observability & UI

| Variable | Default | Notes |
|---|---|---|
| `LOG_LEVEL` | `INFO` | |
| `LOG_FORMAT` | `json` | `console` for readable local output |
| `UI_PORT` | `3000` | Host port for the Next.js dashboard |
