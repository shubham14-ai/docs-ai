# Target Architecture: Document Intelligence / RAG

> Status: **designed, not built** · → [02 API & security](02-api-security.md) · [Index](README.md)

## What we are building

The current service is a **submit → summarize → poll** pipeline over a JSON
string. The target is a **Document Intelligence platform**: files are ingested,
parsed with their structure intact, chunked, embedded and indexed; a user asks a
question and gets an answer grounded in *their* authorized chunks, with
citations.

The existing system is not thrown away. Its async spine — Mongo as record,
Redis Streams as queue, a separate worker, compare-and-set state transitions,
lease + sweeper recovery — is exactly what a RAG ingestion pipeline needs. RAG
is added **as new stages inside the existing worker pipeline**, not as a second
system.

## Lifecycle

```text
                       INGESTION (async, worker)
Upload → Validate → Parse → Structure → Chunk → Metadata/Summary/Keywords
                                                    ↓
                                          Embed → Index → Version LIVE

                       QUERY (sync, API)
Query → AuthN/AuthZ → Classify → Transform → Retrieve → Filter
                                                    ↓
                     Rerank → Context Assembly → LLM → Verify → Response
```

## The one decision that shapes everything else

**Requirement.** Two very different workloads: ingestion is slow, bursty and
retryable; query is interactive and must answer in seconds.

**Decision.** Keep them on opposite sides of the existing split. Ingestion runs
entirely in the worker process and stays fully async through the queue. Query is
synchronous in the API process and touches no queue.

**Why.** The current architecture already proves the async half. Putting
retrieval on the queue would add polling latency to an interactive request for
no benefit; putting embedding in the request path would reintroduce the exact
problem the worker was created to solve.

**Trade-off.** Two code paths that both need the embedding client. It is
resolved by making the embedder a service with no process affinity, injected in
both.

## Current / Next / Future

| | |
|---|---|
| **Current** (built, working) | JSON-text submit, content hash + per-user cache, per-user concurrency limit, Redis Streams queue with consumer group, worker with atomic Mongo claim, retry-with-delay, lease sweeper, mock summarizer, versioned API, typed error envelope, Next.js dashboard |
| **Next** (immediately actionable) | Real auth so `user_id` stops being a client claim ([02](02-api-security.md)); file upload and parsing ([03](03-ingestion.md)); a real summarizer at the existing seam ([04](04-chunking-and-metadata.md)) |
| **Future** | Chunking + chunk metadata ([04](04-chunking-and-metadata.md)), embeddings + vector store ([05](05-embedding-and-vector-store.md)), dedup + zero-downtime versioning ([06](06-versioning-and-dedup.md)), retrieval ([07](07-retrieval.md)), rerank + context assembly ([08](08-reranking-and-context.md)), generation ([09](09-generation-and-verification.md)), verification, evaluation and observability ([10](10-evaluation-and-observability.md)) |

Nothing from **Next** or **Future** exists in the codebase today.

## What stays true

The rules in [`../../CLAUDE.md`](../../CLAUDE.md) are not relaxed by any of it:
every new failure is an `AppError`; all Mongo access stays in `repositories/`;
the vector store is an *accelerator over* Mongo, never the record; every new
tunable is a `Settings` field.
