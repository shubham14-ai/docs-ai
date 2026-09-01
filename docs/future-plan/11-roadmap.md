# Implementation Roadmap

> Status: **plan** · ← [10 Evaluation & observability](10-evaluation-and-observability.md) · → [12 Deferred decisions](12-open-architectural-decisions.md) · [Index](README.md)

Phases are ordered by dependency, not by the reference sequence. Two deviations
from it, both forced by the existing codebase:

- **Auth ships first** and not merely alongside. `tenant_id` is a mandatory
  predicate on every Mongo query and every vector payload from the first chunk
  written. Retrofitting a tenant key onto an index is a full reindex.
- **The evaluation set is built in Phase 5**, not last. Chunk size, top-k and
  thresholds are chosen in Phases 4–8, and choosing them without measurement is
  guesswork.

```text
1 Auth & tenancy → 2 Upload & parsing → 3 Structure & chunking
  → 4 Metadata & summaries → 5 Embedding, vector store & eval harness
  → 6 Dedup & versioning → 7 Retrieval → 8 Rerank & context
  → 9 Generation → 10 Verification & evaluation → 11 Hardening
```

---

## Phase 1 — Auth & tenancy foundation

- **Goal.** Identity and tenant are derived from a token; `user_id` is never a client claim.
- **Implementation.** JWT/JWKS dependency in `app/api/deps.py` yielding `Principal`; `tenant_id` added to `DocumentModel` and forced into every repository query; RBAC dependency; plan-resolved rate limit as a Lua argument; CORS allow-list.
- **Decision.** Verify in-process, enforce isolation in the repository layer. ([02](02-api-security.md))
- **Why.** Everything written after this point carries a tenant key. Adding it later means rewriting the corpus.
- **Depends on.** Nothing.
- **Done when.** `user_id` appears in no request schema; a repository call without a tenant predicate fails a test; existing suites pass with a token fixture.

## Phase 2 — Upload & parsing

- **Goal.** Real files enter the system.
- **Implementation.** Presigned upload + completion hook; validation chain (size → magic bytes → zip ratio → encryption); parsers for PDF/DOCX/XLSX/MD/TXT behind one `Parser` protocol emitting `ParsedDocument`; new terminal `AppError`s.
- **Decision.** Validate before parsing; one parser per type. ([03](03-ingestion.md))
- **Why.** A parser is the largest attack surface; universal extractors discard the structure Phase 3 needs.
- **Depends on.** Phase 1.
- **Done when.** All five types produce `ParsedDocument`; zip bomb, encrypted PDF and spoofed extension each fail with a distinct terminal error before parsing; bytes never buffer in an API worker.

## Phase 3 — Structure detection & chunking

- **Goal.** Documents become retrieval units that respect their own structure.
- **Implementation.** Heading/section detection into `section_path`; table extraction to Markdown; structure-aware recursive splitter with per-type dispatch; token-based sizing with the embedding tokenizer.
- **Decision.** Structure-aware recursive splitting, not semantic chunking. ([04](04-chunking-and-metadata.md))
- **Why.** Deterministic, free, debuggable, and it reads boundaries the author already wrote.
- **Depends on.** Phase 2.
- **Done when.** No chunk crosses a heading; no table is split mid-table; chunk sizes are configurable and token-measured.

## Phase 4 — Metadata, summaries & keywords

- **Goal.** Every chunk carries what retrieval, citation and governance need.
- **Implementation.** Chunk metadata model in Mongo; real summarizer at the existing `Summarizer` seam; per-section summaries and keywords; PII tagging.
- **Decision.** Generate summaries at ingestion, not query time. ([04](04-chunking-and-metadata.md))
- **Why.** Computed once, read on every query; query-time generation puts an LLM call ahead of retrieval.
- **Depends on.** Phase 3.
- **Done when.** Every chunk carries the full metadata table; the mock summarizer is gone; document-level questions have section summaries to retrieve.

## Phase 5 — Embedding, vector store & evaluation harness

- **Goal.** Chunks are searchable, and search quality is measurable.
- **Implementation.** `Embedder` protocol + hosted 1024-dim model; Qdrant in Compose and in prod; HNSW collections partitioned per tenant; `VectorStore` protocol with the six operations; **golden set of ~150 triples plus a recall/MRR harness**.
- **Decision.** Qdrant + HNSW; index name includes model and embedding version. ([05](05-embedding-and-vector-store.md), [10](10-evaluation-and-observability.md))
- **Why.** Filtered ANN is the deciding requirement, and every later phase tunes parameters that cannot be tuned without measurement.
- **Depends on.** Phase 4.
- **Done when.** Filtered search returns full `k` under a selective tenant filter; the index rebuilds from Mongo alone; the harness produces a baseline Recall@50.

## Phase 6 — Deduplication & versioning

- **Goal.** Re-ingest is cheap and updates are invisible to readers.
- **Implementation.** Document and chunk hashing over normalized text; `document_version` / `embedding_version` / `index_version`; build-validate-flip lifecycle with a CAS on the flip; sweeper extension for orphaned vectors.
- **Decision.** Flag flip, never mutate in place. ([06](06-versioning-and-dedup.md))
- **Why.** Delete-then-write has a window where a crash loses the document permanently.
- **Depends on.** Phase 5.
- **Done when.** An identical re-upload embeds zero chunks; a one-paragraph edit re-embeds only affected chunks; a crash at any stage leaves the live version untouched, tested per stage.

## Phase 7 — Retrieval

- **Goal.** A query returns the right chunks, filtered to what the caller may see.
- **Implementation.** `POST /api/v1/query` as a new capability; BM25 sparse index alongside dense; RRF fusion; rules-first query classification; conversational rewriting; retriever-injected mandatory filters.
- **Decision.** Hybrid + RRF, nothing more yet. ([07](07-retrieval.md))
- **Why.** Dense-only fails on identifiers and codes — the queries users consider trivial.
- **Depends on.** Phase 6.
- **Done when.** Exact-identifier and paraphrase queries both succeed; a cross-tenant query is indistinguishable from an empty corpus; the baseline Recall@50 improves over dense-only, measured.

## Phase 8 — Reranking & context assembly

- **Goal.** The model sees a small, ordered, non-redundant context.
- **Implementation.** `Reranker` protocol + hosted cross-encoder with timeout and fusion-order fallback; score floor; exact and near-duplicate removal; neighbour expansion; document-then-index ordering; token budgeting with no truncation.
- **Decision.** Cross-encoder rerank over top-30 to top-8. ([08](08-reranking-and-context.md))
- **Why.** Joint scoring corrects exactly where dense retrieval errs, and shorter context measurably improves answers.
- **Depends on.** Phase 7.
- **Done when.** Reranked top-8 beats fusion top-8 on the golden set; reranker failure degrades with a flag, never a 500; no truncated or duplicate chunk reaches the model.

## Phase 9 — LLM generation

- **Goal.** Grounded, cited, schema-valid answers.
- **Implementation.** `LLMClient` protocol; the generation contract; structured output with `answer`, `citations[]`, `confidence`, `sufficient`; explicit `insufficient_context` path. Self-hosted Langfuse: prompt registry with labelled versions and a local fallback, plus trace ingestion for every model call.
- **Decision.** Schema-constrained output, provider behind a service protocol. ([09](09-generation-and-verification.md))
- **Why.** Citations parsed from prose is how grounding breaks; a schema makes them checkable.
- **Depends on.** Phase 8.
- **Done when.** Every answer is structured with resolvable citations; empty retrieval never reaches the model; the model id *and prompt version* are recorded on every response; no prompt text exists at a call site.

## Phase 10 — Verification & evaluation

- **Goal.** No unverified answer reaches a client.
- **Implementation.** Tier 1 deterministic checks; conditional Tier 2 judge; ACCEPTED / REGENERATED / REJECTED with one bounded retry; groundedness and answer-relevance metrics added to the harness; the golden set mirrored as a Langfuse dataset so each run's scores attach to the prompt version that produced them.
- **Decision.** Two tiers, judge only when cheap checks pass and confidence is low. ([09](09-generation-and-verification.md))
- **Why.** Judging every response doubles cost to catch what Tier 1 already catches.
- **Depends on.** Phase 9.
- **Done when.** A hallucinated citation id cannot reach a client, proven by fault injection; rejection returns sources; regeneration is bounded at one and observable; a prompt change ships only with its before/after golden-set numbers, and rolls back by moving a label.

## Phase 11 — Production hardening

- **Goal.** Operable at cost, under load, with governance.
- **Implementation.** OTel spans per stage; Prometheus `/metrics`; the multi-provider gateway ([13](13-llm-gateway.md)) with routing, fallback chains and per-tenant cost ceilings; per-provider timeouts and degradations; split parse/embed worker pools; query-embedding and answer caches with version-correct invalidation; token budgets; retention and hard delete across Mongo + vectors; dead-letter replay.
- **Decision.** Extend existing mechanisms rather than introduce new systems. ([10](10-evaluation-and-observability.md))
- **Why.** Redis Streams already gives ordering, consumer groups and recovery at this scale.
- **Depends on.** Phase 10.
- **Done when.** One trace shows per-stage latency, provider, model, tokens and cost; killing the primary generation provider degrades to a fallback with no code change; every external call has a timeout and a defined degradation; a tenant deletion removes records and vectors together, verified.
