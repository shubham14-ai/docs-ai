# Future Plan

> Related: [`../README.md`](../README.md) · [`../assumptions.md`](../assumptions.md)

Everything here is **outside the assignment's ceiling**. Nothing in this folder
is implemented, and nothing in it should be implemented while the brief is the
specification. It exists so that the reasoning survives the deadline.

This is **one plan, not a pile of ideas**: evolving the current Document
Insights API into a production Document Intelligence / RAG platform. It reads in
order — architecture first, then one note per layer of the pipeline, in the
order data flows through it, then the build order.

Start at [01](01-architecture.md). If you only read two, read that and
[11](11-roadmap.md).

## The sequence

```text
01 Architecture ── the whole lifecycle, and current vs next vs future
      │
      ├─ 02 API & security ......... the boundary every request crosses
      │
      ├─ INGEST ─ 03 Ingestion, parsing & structure
      │           04 Chunking, metadata, summaries & keywords
      │           05 Embedding, vector storage & indexing
      │           06 Deduplication & document versioning
      │
      ├─ QUERY ── 07 Query processing & retrieval
      │           08 Reranking & context assembly
      │           09 LLM generation & verification
      │
      ├─ 10 Evaluation, observability & production readiness
      │
      ├─ 11 Roadmap ── the same layers as 11 dated phases
      │
      ├─ 12 Deferred decisions ── what is not decided, and the trigger for each
      │
      └─ 13 LLM gateway ── which provider serves 05, 08 and 09, and what
                           happens when one is down
```

| # | Note | The decision it settles | State |
|---|---|---|---|
| 01 | [Architecture](01-architecture.md) | The full lifecycle; RAG rides the existing async spine, not a second system | Designed |
| 02 | [API & security](02-api-security.md) | Token-derived identity, RBAC, tenant isolation as a mandatory predicate, plan-based limits, error taxonomy | Designed |
| 03 | [Ingestion, parsing & structure](03-ingestion.md) | Presigned upload, validate-before-parse, one parser per type, structure preserved for retrieval | Designed |
| 04 | [Chunking & metadata](04-chunking-and-metadata.md) | Structure-aware recursive splitting, the chunk metadata model, ingestion-time summaries and keywords, the real summarizer | Designed |
| 05 | [Embedding & vector store](05-embedding-and-vector-store.md) | Provider behind a protocol, Qdrant + HNSW, model version in the index name | Designed |
| 06 | [Dedup & versioning](06-versioning-and-dedup.md) | Document and chunk hashing, build-validate-flip with zero downtime | Designed |
| 07 | [Query & retrieval](07-retrieval.md) | Hybrid dense + BM25 fused with RRF, mandatory filters, defined retrieval outcomes | Designed |
| 08 | [Reranking & context](08-reranking-and-context.md) | Cross-encoder 30→8, dedup, ordering, token budget with no truncation | Designed |
| 09 | [Generation & verification](09-generation-and-verification.md) | The generation contract, structured citations, accept / regenerate / reject | Designed |
| 10 | [Evaluation & observability](10-evaluation-and-observability.md) | Golden set before tuning, prompts versioned in a self-hosted Langfuse registry, per-stage metrics and LLM traces, production mechanisms | Designed |
| 11 | [Roadmap](11-roadmap.md) | The build order, with concrete completion criteria per phase | Plan |
| 12 | [Open architectural decisions](12-open-architectural-decisions.md) | The seven deferred decisions, the option taken today, and the trigger to revisit each | Deferred |
| 13 | [LLM gateway](13-llm-gateway.md) | Multi-provider routing by task tier, transient-only fallback, per-tenant cost ceiling; embeddings never fall back | Designed |

## Reading the notes

Every note in 02–10 answers the same five questions per decision, in this order:

1. **Requirement** — the problem, stated before any technology.
2. **Decision** — one approach, chosen.
3. **Why** — what makes it the right one *here*.
4. **Trade-off** — only where there is a real one.
5. **Done when** — concrete, checkable completion criteria.

Alternatives appear only where they are needed to justify the choice. Anything
deliberately deferred lives in [12](12-open-architectural-decisions.md) with the option taken
today and the trigger that would revisit it — never hedged inside a note that is
supposed to be a decision.

**Nothing here describes existing behaviour.** For what the service does today,
see [`../README.md`](../README.md); [01](01-architecture.md) carries the
explicit current / next / future split.
