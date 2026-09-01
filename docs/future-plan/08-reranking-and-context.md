# Reranking & Context Assembly

> Status: **designed, not built** · ← [07 Query & retrieval](07-retrieval.md) · → [09 Generation & verification](09-generation-and-verification.md) · [Index](README.md)

## Pipeline

```text
~30 fused chunks
  → Rerank (cross-encoder)
  → Relevance filtering (score floor)
  → Deduplication (chunk_hash + near-duplicate)
  → Neighbour expansion (adjacent chunk_index, same document)
  → Ordering (document, then chunk_index)
  → Context budgeting (token cap)
  → Final context (≤ 8 chunks, with citation ids)
```

## 1. Reranking

**Requirement.** Retrieval optimizes recall at k=50. Handing 50 chunks to an LLM
is expensive, slow, and measurably *worse* — accuracy degrades as irrelevant
context grows.

**Decision.** A **hosted cross-encoder reranker** over the top ~30 fused
candidates, cutting to 8, behind a `Reranker` protocol.

**Why a cross-encoder.** It scores query and chunk *jointly*, so it sees term
interaction that a bi-encoder cannot — this is precisely where dense retrieval's
errors are, and it is the highest-value single component in the pipeline: it
typically moves answer quality more than swapping the embedding model.

**Why hosted rather than self-hosted, and why over 30 not 50.** Cross-encoders
cost one forward pass per candidate and are the pipeline's latency floor. Hosted
avoids adding a GPU to the deployment; 30 candidates keeps p95 within an
interactive budget while the fusion step has already discarded the obvious
misses.

**Trade-off.** An external call on the interactive path. Bounded by an aggressive
timeout with a defined fallback: **on reranker failure, fall through to fusion
order with a degraded flag on the response** — the same "degrade, do not fail"
posture Redis already has ([`../failure-handling.md`](../failure-handling.md)).

## 2. Preventing waste in the context window

Three mechanisms, in order:

1. **Score floor.** Anything below `RERANK_SCORE_MIN` is dropped even if fewer
   than 8 chunks survive. A short context beats a padded one; if nothing
   survives, the answer is `insufficient_context`.
2. **Deduplication.** Exact by `chunk_hash`, then near-duplicate by cosine
   similarity between retained chunks above 0.95 — overlapping chunks and boilerplate
   repeated across documents otherwise consume the budget several times over.
   The highest-scoring copy is kept; the rest are recorded as additional citations
   on it rather than discarded silently.
3. **Token budgeting.** The budget is counted with the generation model's
   tokenizer and filled highest-score-first; a chunk that does not fit whole is
   excluded, never truncated. A truncated chunk is the classic source of an
   answer citing a sentence that was cut away.

## 3. Neighbour expansion

**Decision.** For each surviving chunk, optionally include its immediate
neighbours from the same document when the budget allows.

**Why.** The chunk that matched the query often is not the chunk containing the
answer — a heading-adjacent chunk matches, the value sits in the next one. This
recovers most of what parent-document retrieval provides at the cost of a metadata
lookup instead of a second index.

## 4. Ordering

**Decision.** Group by document, order by `chunk_index` within it, and place the
highest-scoring document first. Each chunk is labelled with its citation id,
document title and `page`/`section_path`.

**Why not score order.** Reading order preserves the argument the author wrote;
score order shuffles a document into fragments and hurts multi-chunk reasoning.
Putting the strongest document first keeps the strongest evidence away from the
middle of the context, where models attend least.

## 5. Context validation

Before generation: at least one chunk survives; total tokens within budget; every
chunk's tenant matches the caller (a redundant assertion — a mismatch here is a
bug, and it should raise rather than answer); every chunk carries a resolvable
citation id.

## Done when

- Reranked top-8 outperforms fusion top-8 on the evaluation set, measured.
- Reranker timeout degrades to fusion order with a flagged response, never a 500.
- No duplicate or truncated chunk ever reaches the model.
- Every context chunk is traceable to a citation the API can resolve.
