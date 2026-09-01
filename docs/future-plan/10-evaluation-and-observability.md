# Evaluation, Observability & Production Readiness

> Status: **designed, not built** · ← [09 Generation & verification](09-generation-and-verification.md) · → [11 Roadmap](11-roadmap.md) · [Index](README.md)

## 1. Why evaluation comes before tuning

Every tuning decision in [04](04-chunking-and-metadata.md)–[08](08-reranking-and-context.md)
— chunk size, overlap, top-k, thresholds, whether a retrieval technique earns its
latency — is unanswerable without measurement. Without an evaluation set, RAG
tuning is guesswork that feels like progress.

**Decision.** A **golden set of ~150 question/answer/expected-chunk triples**
drawn from the real corpus, versioned in the repo, run in CI as a **non-blocking
report** and manually as a gate before any pipeline change ships.

The set is held as a **Langfuse dataset** as well as in the repo: the repo copy
is the reviewable, diffable source of truth, and the Langfuse copy is what a run
attaches its scores and traces to, so every historical run is comparable against
the prompt version and pipeline parameters that produced it
([§3](#3-prompt-versioning), [§4](#4-logging-tracing-and-llm-observability)).

**Why non-blocking in CI.** Retrieval metrics are noisy and depend on external
providers; a flaky gate gets disabled. A tracked report with a regression
threshold gets read.

## 2. Metrics

| Stage | Metric | Why this one |
|---|---|---|
| Retrieval | Recall@50, MRR@10 | Recall bounds everything downstream — a chunk not retrieved cannot be answered from |
| Reranking | nDCG@8 vs pre-rank | Isolates whether the reranker earns its latency |
| Context | Precision@8, tokens used / budget | Detects padding and waste |
| Generation | Groundedness, answer relevance | The two failure modes users actually experience |
| Verification | Accept / regenerate / reject rates | A rising reject rate is a retrieval regression showing up late |
| Operational | p50/p95 per stage, tokens, cost per query, error rate by `AppError` code | Cost and latency are per-query here, unlike the current service |
| Prompt | Every metric above, sliced by prompt version | A quality change is attributable to the edit that caused it ([§3](#3-prompt-versioning)) |

Per-stage latency matters because the query path has five external calls
(embed, dense, lexical, rerank, generate) and "the query is slow" is otherwise
undiagnosable.

## 3. Prompt versioning

**Requirement.** The generation contract in
[09](09-generation-and-verification.md) §1 is a prompt, and a prompt is the one
input to the pipeline that is neither code nor data. Edited inline it is
invisible: an answer quality change has no diff, an evaluation run cannot say
*which* prompt it scored, and a bad edit cannot be rolled back without a deploy.

**Decision.** Prompts live in a **Langfuse prompt registry**, addressed by name
and label — `rag-answer@production` — never inlined at the call site. Every
version is immutable; `production` and `staging` are moving labels pointing at a
version. The SDK caches the fetched prompt in-process with a TTL and falls back
to the last known good version on a Langfuse outage, so the registry is never in
the request path's failure domain.

**Why Langfuse.** It is the same system that already has to hold the traces
([§4](#4-logging-tracing-and-llm-observability)) and the evaluation runs
([§1](#1-why-evaluation-comes-before-tuning)). Keeping prompt version, trace and
score in one place is what makes "this regression started at prompt v7" a query
rather than a correlation exercise across three tools. It is open source and
self-hostable, so no prompt or document content leaves our infrastructure — a
hard requirement given [02](02-api-security.md)'s tenancy model.

**What is versioned.** The system prompt and the rules block, the response
schema, and the model configuration bound to them (model id, temperature,
`max_tokens`). These change together; versioning the text alone reproduces
nothing.

**Trade-off.** A prompt fetched at runtime is a dependency the repository does
not fully describe. Mitigated by pinning `production` explicitly in config, by
the local fallback copy, and by requiring every label move to be accompanied by
its golden-set numbers ([§1](#1-why-evaluation-comes-before-tuning)).

**Rollback.** Moving the `production` label to the previous version. No deploy,
seconds, and the trace of every affected request already names the version that
served it.

## 4. Logging, tracing and LLM observability

**Current.** Structured JSON logging with bound context and a request id
propagated through the worker.

**Decision.** Keep the structured logger; add **OpenTelemetry tracing with one
span per pipeline stage**, and Prometheus metrics scraped from `/metrics`.

**Why tracing specifically, and not more tools.** The RAG query path is a
sequential fan-out of external calls with a per-request cost. Logs answer *what
happened*, metrics answer *how often*; only a trace answers *where the 3 seconds
went for this request* — the question that will actually be asked. Prometheus
because it is the least operationally expensive way to get the aggregate view,
and the existing `/health` deployment model already fits it.

Every span carries `trace_id`, `tenant_id`, `document_id`/`query_id`, model ids,
token counts and cost. A logged query records its retrieved chunk ids and scores,
which is what makes a user-reported bad answer reproducible offline.

**Why one more tool, and only one.** OTel and Prometheus are generic: a span
records that `generate` took 2.1s, not what was sent, what came back, which
prompt version produced it or whether the answer was any good. That is the
question this pipeline gets asked. **Langfuse** (self-hosted) is the LLM-specific
layer over the same trace:

| Recorded per query | Used for |
|---|---|
| Prompt name + version that served it | Attributing a quality change to a prompt change ([§3](#3-prompt-versioning)) |
| Full input/output of every model call, with token counts and cost | Cost per query per tenant; reproducing a bad answer exactly |
| Retrieved chunk ids and rerank scores as spans | Deciding whether a bad answer was retrieval or generation |
| Verification outcome and judge scores ([09](09-generation-and-verification.md) §3) | Rejection rate as a retrieval-regression alarm |
| Golden-set runs as datasets and scores | Before/after numbers attached to the prompt version they belong to |

**Why not fold this into OTel spans.** Nothing stops a span carrying prompt text
as an attribute, but nothing then lets a reviewer read a conversation, diff two
prompt versions, or attach a human score to a run. Traces, prompt registry and
evaluation results converge on one identifier: `trace_id`. One tool holding all
three is the reason "which prompt version regressed groundedness on which
tenant's documents" is answerable at all.

**Boundaries.** Langfuse is an **observability sink, never a dependency of the
answer path** — the same rule Redis lives under ([`../../CLAUDE.md`](../../CLAUDE.md)
rule 5). Ingestion is asynchronous and non-blocking; a Langfuse outage drops
telemetry and nothing else, and the prompt registry's local fallback keeps
generation running. It is self-hosted next to Mongo and Redis because prompts and
document content are tenant data. Sampling is 100% at this volume, with a
per-tenant retention window rather than a sample rate, so a reported bad answer
is always present.

## 5. Production mechanisms

Most already exist. The table states what changes.

| Mechanism | Current | Change for RAG |
|---|---|---|
| Async processing | Redis Streams + consumer group | Unchanged; ingestion stages ride the same queue |
| Background workers | Separate process, scalable | Add a parse-heavy worker pool separate from embed-heavy, so a slow OCR job does not starve embedding |
| Retry | Delay ZSET, bounded attempts, `transient` flag | Unchanged; provider errors classified transient ([02](02-api-security.md)) |
| Idempotency | Mongo compare-and-set claim | Extended: vector upserts keyed by `chunk_id` are idempotent by construction ([05](05-embedding-and-vector-store.md)) |
| Failure recovery | Lease + sweeper | Extended: sweeper also removes vectors of non-live failed versions ([06](06-versioning-and-dedup.md)) |
| Timeouts | Redis socket, derived from block duration | Add per-provider timeouts; every external call has one, and every one has a defined degradation |
| Caching | Per-user summary cache in Redis | Add query-embedding cache; full answer caching only per tenant+query+`document_version`, so a document update invalidates it correctly |
| Rate limiting | Atomic Lua, concurrency per user | Plan-resolved limits + token budget ([02](02-api-security.md)) |
| Multi-tenancy | Not present | Mandatory predicate in repository and vector store |
| Data governance | Not present | PII tagging at ingest, retention per tenant, hard delete removes Mongo records and vectors together |

**The simplest thing that holds at this scale.** No Kafka, no orchestration
framework, no separate feature store: Redis Streams already provides ordering,
consumer groups and recovery, and the corpus size does not justify replacing it.
Each addition above is a change to an existing mechanism rather than a new one.

## 6. Operator surfaces

Three things an operator needs that no metric provides:

| Surface | Why | Enters through |
|---|---|---|
| **Queue depth** as a first-class metric | It is the single number that says whether to scale workers, and nothing exposes it today | `/metrics`, unversioned like `/health` |
| **Dead-letter collection + replay endpoint** | A `failed` document is currently terminal and manual; operators need the class of failure and a way to re-drive a batch | [`app/repositories/`](../../app/repositories/) |
| **Query replay** | A user-reported bad answer must be reproducible from its logged chunk ids and scores, offline, against a changed pipeline | The query log |

**Cost.** Metrics and traces are a second contract: once someone alerts on a
metric, its name and meaning are frozen. Worth paying, worth paying
deliberately — which is why this layer follows the golden set rather than
preceding it.

Open items: [12](12-open-architectural-decisions.md) §3 (worker pool split), §7 (push vs pull,
completion callbacks).

## Done when

- The golden set runs on demand and in CI, and produces a diffable report, with
  every run recorded against the prompt version that produced it.
- No prompt is inlined at a call site; `production` rolls back by moving a label,
  with no deploy.
- Every pipeline-parameter change is accompanied by its before/after numbers.
- A single trace shows per-stage latency, tokens and cost for one query.
- A bad answer can be reproduced offline from its logged chunk ids and scores.
