# Docker

Everything needed to build, run, and operate the stack.

---

## Image stages

The `Dockerfile` is multi-stage. Each stage builds on the previous one:

| Stage | Contents | Used by |
|---|---|---|
| `base` | Python 3.12-slim + non-root user (`appuser`, uid 10001) | All stages |
| `deps` | + runtime requirements (`requirements.txt`) | `prod`, `dev` |
| `dev-deps` | + test requirements (`requirements-dev.txt`) | `dev` |
| `dev` | + source, pytest config | API/worker in dev mode, test runner |
| `prod` | `deps` + source only — no test deps, no reloader | API/worker in production (default) |

A source edit never re-runs `pip` — requirements are installed before source is copied. In dev mode the source is not even baked in; Compose bind-mounts `./app`, so a code change reloads in place with no build at all.

---

## Services

Defined in `docker-compose.yml`, with `docker-compose.override.yml` applied automatically in dev:

| Service | Image | Port | Notes |
|---|---|---|---|
| `mongo` | `mongo:7` | 27017 | Persistent volume `mongo-data`; `appendonly yes` |
| `redis` | `redis:7-alpine` | 6379 | Persistent volume `redis-data`; `appendonly yes` |
| `api` | `docs-ai:prod` / `docs-ai:dev` | 8000 | FastAPI + uvicorn |
| `worker` | `docs-ai:prod` / `docs-ai:dev` | — | Same image as `api`, different command; `stop_grace_period: 45s` |
| `ui` | `docs-ai-ui:prod` / `docs-ai-ui:dev` | 3000 (configurable via `UI_PORT`) | Next.js dashboard |
| `tests` | `docs-ai:dev` | — | `--profile test` only; never started by `up` |

`api` and `worker` share one image — same build, different `command`.

---

## Dev vs prod

| | Dev (default) | Prod |
|---|---|---|
| Image stage | `dev` | `prod` |
| Source | Bind-mounted from `./app` | Baked into the image |
| API reload | `uvicorn --reload` | No reloader |
| Worker reload | `watchfiles python -m app.workers.main` | No reloader |
| UI | `next dev` (on-demand compile) | Pre-built `next start` |
| Trigger | `docker compose up` (override applied automatically) | `docker compose -f docker-compose.yml up` |

A code change in dev costs nothing — no build, no layer, no restart. Only a change to `requirements*.txt` needs a rebuild.

---

## CLI wrapper — `doc-ai.sh` / `doc-ai.ps1`

```bash
./doc-ai.sh <command> [args]
```

| Command | What |
|---|---|
| `init` | Build + start (first run) |
| `up` | Start the stack |
| `down` | Stop the stack |
| `restart` | Stop then start |
| `restart-svc <service>` | Restart one service (`api`, `worker`, `mongo`, `redis`) |
| `build` | Full rebuild, no cache (use when dependencies change) |
| `rebuild` | Cached rebuild + recreate |
| `scale <n>` | Run `n` worker processes (default 3) |
| `logs [service]` | Tail logs; omit service for all |
| `status` | `docker compose ps` + resource usage |
| `health` | Ping `/health`, MongoDB, and Redis |
| `shell [service]` | Open a shell in a container (default `api`) |
| `test [level]` | Run the test suite (see below) |
| `prune` | `docker system prune` |

Prefix with `MODE=prod` to run the prod flavour:

```bash
MODE=prod ./doc-ai.sh up
MODE=prod ./doc-ai.sh init
```

---

## Running tests

```bash
./doc-ai.sh test                  # full suite
./doc-ai.sh test unit             # unit only
./doc-ai.sh test integration      # integration only
./doc-ai.sh test edge             # @pytest.mark.edge cases
./doc-ai.sh test release          # unit + integration, CI-shaped, no tree
./doc-ai.sh test tests/unit -v    # pass anything directly to pytest
```

Reports are written to `./test-results/` on the host:

| File | Contents |
|---|---|
| `junit.xml` | JUnit-compatible XML |
| `summary.json` | Machine-readable summary |
| `report.txt` | Plain-text output |
| `html/index.html` | HTML report |

The test runner uses a separate `_test` database and Redis DB 1, and compresses processing durations to 0 via config — the suite runs in ~20s.

---

## Scaling

```bash
./doc-ai.sh scale 3   # three worker processes, one consumer group
```

Redis delivers each stream entry to one consumer in the group. Three processes share the queue; each runs up to `WORKER_CONCURRENCY` (default 10) documents concurrently via asyncio. These are two independent axes:

| Axis | Knob |
|---|---|
| Documents in flight per process | `WORKER_CONCURRENCY` env var |
| Worker processes | `./doc-ai.sh scale N` |

---

## Building a single stage

```bash
docker build --target dev  -t docs-ai:dev  .
docker build --target prod -t docs-ai:prod .
```

BuildKit is enabled by default (`DOCKER_BUILDKIT=1`) for pip cache mounts and stage-level parallelism.
