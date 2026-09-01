# Open Architectural Decisions

> Status: **deferred** · ← [11 Roadmap](11-roadmap.md) · [Index](README.md)

These decisions are intentionally deferred. The current implementation uses the
simplest production-safe option. Each decision has an explicit **trigger** for
revisiting it, so that future changes are driven by measured requirements rather
than premature complexity.

Where a layer is not built yet, "current decision" states the default the design
adopts when that layer is implemented — not existing behaviour. Only §1, §4 and
§5 describe code that runs today.

---

## 1. Content cache scope

**Current decision:** cache summaries **per user**. The cache key is:

```text
cache:summary:{user_id}:{content_hash}
```

The brief specifies reuse when *the same user* submits identical content. A
global content cache would allow a derived artifact generated from one tenant's
document to be reused by another tenant. This is therefore treated as a
**data-isolation decision, not merely a cache-key decision**, and is recorded in
[`../assumptions.md`](../assumptions.md).

**Alternative.** A global cache would significantly improve reuse for repeated
boilerplate shared across tenants — contracts, templates, forms, public filings,
and other identical documents. It becomes more valuable once summarization and
embedding carry meaningful per-call cost.

**Risk of global caching.**

- Cross-tenant information leakage.
- Timing-based disclosure that another tenant has submitted identical content.
- Incorrect reuse if summarization later depends on tenant-specific context,
  glossary, permissions, or prior documents.

**Possible future models.**

1. **Tenant-controlled sharing** — only explicitly shareable content enters the global cache.
2. **Tenant-group cache** — artifacts are shared only within an approved tenant group.
3. **Shared expensive artifacts, private rendering** — share embeddings or other
   content-derived artifacts while generating the final tenant-specific
   presentation independently.

**Decision.** Keep the cache per user/tenant until there is a concrete business
requirement for cross-tenant reuse *and* an explicit data-sharing policy
supporting it.

**Revisit when:** real summarization/embedding costs make duplicate processing
material, or a customer requirement demands cross-tenant deduplication.

**Related.** The same scope decision applies to chunk-level deduplication in
[06](06-versioning-and-dedup.md). These two decisions must remain consistent.

---

## 2. Extracted text storage

**Current decision:** store normalized/extracted text **with the document
record**. The current corpus does not justify introducing a separate
object-storage dependency.

**Alternative.** Store extracted text in object storage and retain only a
pointer in MongoDB. This becomes preferable when documents are large enough that
embedded text creates document-size pressure, larger reads, slower list/query
operations, or material storage overhead.

**Revisit when:** the corpus contains documents approaching MongoDB's
document-size limit, or document/list operations show measurable degradation
caused by embedded text.

Reference: [03](03-ingestion.md)

---

## 3. Dedicated parsing worker pool

**Current decision:** parsing and embedding **share the worker infrastructure**.
A separate parsing pool is not introduced without evidence that parsing/OCR
workloads interfere with embedding throughput.

**Alternative.** Dedicated pools — parse/OCR workers for CPU- and I/O-heavy
work, embedding workers for model-bound work — giving workload isolation so a
long-running OCR task cannot block embedding jobs.

**Decision.** Do not introduce separate pools until scheduling metrics
demonstrate a real isolation problem.

**Revisit when:** head-of-line blocking is observed in the ingestion queue, or
OCR/parse workloads materially affect embedding throughput or latency.

Reference: [10](10-evaluation-and-observability.md)

---

## 4. JWT validation and token issuer

**Current decision:** validate JWTs **in the API process**. There is no API
gateway in this deployment, so in-process validation is the appropriate
boundary. This is a deployment-driven decision, not a permanent architectural
preference.

**Alternative.** Move token validation to an API gateway or dedicated edge
layer; the API then receives trusted identity/context from the gateway.

**Revisit when:** an API gateway is introduced, or authentication becomes a
shared platform concern across multiple services.

Reference: [02](02-api-security.md)

---

## 5. Quota state

**Current decision:**

- Redis stores **volatile rate-limit counters**.
- Tenant configuration remains the **source of truth for plan/quota
  configuration**.

Redis is appropriate for short-lived counters because it provides atomic,
low-latency updates with expiration. Quota *configuration* should not depend on
Redis state.

**Decision.** Keep configuration in persistent storage and runtime counters in
Redis. A plan change updates the persistent tenant configuration and takes
effect without an application restart.

**Revisit when:** plan/quota changes require more sophisticated policy
evaluation, or quota enforcement becomes distributed across additional services.

Reference: [02](02-api-security.md)

---

## 6. Advanced retrieval techniques

The initial retrieval pipeline intentionally uses a controlled baseline rather
than implementing every available RAG technique.

**Current retrieval baseline:** metadata filtering · dense retrieval · BM25 /
lexical retrieval · hybrid ranking · re-ranking.

**Intentionally deferred:** parent-document retrieval · self-query retrieval ·
multi-query expansion · contextual compression.

These are not rejected patterns. They are **gated on measurable improvement**
against the evaluation/golden dataset.

**Decision.** Do not add advanced retrieval techniques on theoretical benefit
alone. Introduce a technique only when evaluation demonstrates that it improves
a known failure class without unacceptable latency or cost.

**Revisit when:** evaluation shows insufficient recall for a specific query
class, **and** one of the deferred techniques directly addresses that failure.

Reference: [07](07-retrieval.md), [10](10-evaluation-and-observability.md)

---

## 7. Metrics delivery and client completion notifications

### Metrics

**Current decision:** Prometheus **pull-based** collection. The workers are
long-running services, so pull is simpler and more operationally appropriate
than push.

**Revisit when:** the architecture introduces short-lived workers, where
pull-based scraping becomes impractical.

### Job completion notifications

**Current decision:** clients **poll** the job-status endpoint. Polling is
selected deliberately: simple, reliable, and requiring no persistent client
connection.

**Alternative.** Webhooks, WebSockets, or Server-Sent Events. A callback model
introduces additional reliability requirements — at-least-once delivery, retry
handling, idempotency, delivery-status tracking, and dead-letter handling —
which should reuse the patterns in
[`../failure-handling.md`](../failure-handling.md).

**Revisit when:** a client cannot reasonably poll, or polling traffic becomes a
measurable scalability concern.

---

## Decision policy

Deferred decisions are revisited on **measured evidence, customer requirements,
or a material change in deployment architecture** — not on preference.

> Prefer the simplest production-safe design today; introduce additional
> architecture only when a measurable requirement justifies it.

This prevents premature infrastructure complexity while keeping explicit
migration paths for future scale.
