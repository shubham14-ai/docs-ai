# Embedding, Vector Storage & Indexing

> Status: **designed, not built** · ← [04 Chunking & metadata](04-chunking-and-metadata.md) · → [06 Dedup & versioning](06-versioning-and-dedup.md) · [Index](README.md)

## 1. Embedding model

**Requirement.** One model, good enough for mixed business prose and tables,
cheap enough to embed a corpus repeatedly during tuning, and replaceable.

**Decision.** **A hosted embedding API at 1024 dimensions**, selected by a
written rule rather than by name, and reached only through an `Embedder`
protocol:

```python
class Embedder(Protocol):
    model_id: str          # recorded on every chunk
    dimensions: int
    async def embed(self, texts: list[str], kind: Literal["document", "query"]) -> list[Vector]: ...
```

**Selection rule** — the model chosen is the cheapest one that satisfies all of:
retrieval quality within 2 points of the best available on our own evaluation set
([10](10-evaluation-and-observability.md)); ≥ 8k token input; multilingual;
supports asymmetric document/query prefixes; dimensions ≤ 1024.

**Why hosted rather than self-hosted.** Self-hosting adds a GPU to the deployment
and an inference service to operate, to save cost at a corpus size this system
does not have. The `Embedder` protocol means the decision is reversible in one
class.

**Why 1024 dimensions.** Index memory and search latency scale linearly with
dimension; above ~1024 the quality curve is flat for prose retrieval while cost
keeps rising. Where the provider supports Matryoshka truncation, this is a
truncation of a larger model rather than a smaller model.

**Similarity.** **Cosine**, with vectors L2-normalized at write time so the store
computes a dot product. Normalizing once at ingestion removes the per-query cost.

**Cost control.** Embed in batches; skip any chunk whose `content_hash` already
exists for the tenant ([06](06-versioning-and-dedup.md)); cache query embeddings
in Redis keyed by `hash(model_id + query)` — repeated questions are common and
this reuses the existing Redis dependency and degradation story.

## 2. Model versioning and re-embedding

**Requirement.** Vectors from two models are not comparable. A model change that
half-migrates an index silently destroys retrieval quality.

**Decision.** `embedding_model` and `embedding_version` are stored on every chunk
and are **part of the index name**: `chunks_{tenant}_{model}_{version}`. Changing
the model means **building a new index alongside the old one, validating it, then
flipping a pointer** — the same live/inactive pattern as document versioning
([06](06-versioning-and-dedup.md)).

**Why.** It makes a mixed index structurally impossible rather than a thing to be
careful about, and it makes rollback a pointer flip.

**Trade-off.** Peak storage doubles during a migration. That is the correct price
for a reversible migration.

## 3. Vector store

**Requirement.** Millions of chunks, p95 search under ~100 ms, **mandatory
metadata filtering with the filter applied inside the search**, hard tenant
isolation, minimal operational burden, and no rewrite when scale grows.

**Decision.** **Qdrant**, run as a container in the existing Compose stack for
development and as managed cloud in production. One collection per tenant tier,
with `tenant_id` as an indexed payload field and per-tenant partitioning enabled.

**Why.** The deciding requirement is filtered search. Retrieval here is *never*
unfiltered — tenant, version and permission predicates always apply — and a store
that filters after the ANN search returns fewer than `k` results, or silently
degrades recall, exactly when the filter is selective. Qdrant applies payload
filters inside HNSW traversal, so recall holds under selective filters. It also
runs as a single container locally, which keeps the "one `./doc-ai.sh init` and
everything is up" property this project already has, and is the same engine in
production — no dev/prod divergence.

**Why not the alternatives** (stated only because this is the decision the
architecture rests on): a managed-only service breaks local development and the
test suite's use of real infrastructure; an embedded store does not survive
multiple worker processes; adding vector search to Mongo would keep one database
but gives a weaker filtered-ANN implementation and couples index scaling to
record storage.

**Trade-off.** A fourth stateful dependency to operate and back up. Mitigated by
the rule that it is rebuildable from Mongo — a lost index is a reindex job, not
data loss.

## 4. Index

**Decision.** **HNSW**, `m = 16`, `ef_construct = 128`, `ef_search` tuned per
query class (64 default, 128 for high-recall queries), with **scalar
quantization** enabled above ~1 M vectors per collection.

**Why HNSW.** At this scale — millions, not billions — HNSW gives the best
recall/latency point with no training step, and it supports incremental inserts
and deletes. IVF requires a training pass and re-training as the distribution
drifts, which conflicts with continuous ingestion. DiskANN's advantage only
appears when the index no longer fits in memory, which is a scale this system
does not have; if it ever does, the store supports it without an application
change.

**Trade-off.** HNSW memory grows with `m` and dimension. Scalar quantization cuts
it ~4× for a small recall loss, and is enabled by a threshold, not by default.

## 5. Vector-store operations

Behind one `VectorStore` protocol, tenant predicate injected by the
implementation, never by callers:

| Op | Semantics |
|---|---|
| **Create** | Batch upsert keyed by `chunk_id`; idempotent, so a retried worker job is safe |
| **Read** | Fetch by `chunk_id` for citation resolution |
| **Update** | Upsert by the same key — metadata-only updates (e.g. `is_active`) do not re-embed |
| **Delete** | By `document_id` + `document_version` filter; hard delete on tenant deletion (GDPR) |
| **Search** | Top-K ANN with mandatory filter and score threshold |
| **Filter** | Payload predicates evaluated inside traversal: tenant, `is_active`, ACL, source type, recency |

Every write is idempotent because the worker's at-least-once delivery guarantees
retries — the same reasoning that made the Mongo claim a compare-and-set.

## Done when

- No application code names an embedding provider or a vector store outside its adapter.
- A model change produces a second index and a pointer flip, with rollback proven in a test.
- Filtered search returns full `k` under a highly selective tenant filter.
- Dropping the vector store and reindexing from Mongo restores identical results.
