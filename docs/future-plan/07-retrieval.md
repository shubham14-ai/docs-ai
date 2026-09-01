# Query Processing & Retrieval

> Status: **designed, not built** · ← [06 Dedup & versioning](06-versioning-and-dedup.md) · → [08 Reranking & context](08-reranking-and-context.md) · [Index](README.md)

## Pipeline

```text
User query
  → Schema validation
  → AuthN/AuthZ (Principal from token)          [08]
  → Build mandatory filter: tenant + ACL + is_active + embedding_version
  → Classify query
  → Transform query
  → Hybrid retrieval (dense + lexical), filtered
  → Threshold & validity filtering
  → Rerank                                      [14]
  → Context assembly                            [14]
```

## 1. Retrieval strategy

**Requirement.** Business documents contain identifiers, product codes, error
codes and acronyms. Pure semantic search misses exact tokens; pure keyword search
misses paraphrase. Both failure modes are common in the same corpus.

**Decision.** **Hybrid retrieval — dense vectors + BM25 lexical — fused with
Reciprocal Rank Fusion**, with mandatory metadata filters, and nothing else in
the first implementation.

```text
dense:   top 50 from Qdrant  (filtered)
lexical: top 50 from BM25    (same filter)
fuse:    RRF, score = Σ 1/(60 + rank_i)
→ top 30 to the reranker
```

**Why hybrid over dense-only.** Dense-only fails on exactly the queries users
consider trivially answerable — "what does error E-4021 mean" — and those
failures destroy trust faster than a mediocre paraphrase answer.

**Why RRF over score-weighted fusion.** Dense cosine scores and BM25 scores are
on incomparable scales; any weighted blend needs a tuned normalization that
drifts with corpus and model. RRF uses only ranks, has one constant, and needs no
retuning when the embedding model changes.

**Why not parent-document, self-query, multi-query or contextual compression
now.** Each adds an LLM call or an index in front of an interactive request.
Their gains are real but conditional; hybrid + rerank is the smallest
architecture that gets most of the quality. The pipeline is a sequence of steps
behind one `Retriever` protocol, so each can be added later as a step — and
**must be justified by a measured gain on the evaluation set** ([10](10-evaluation-and-observability.md)), not by being available.

**Trade-off.** A lexical index alongside the vector index. Qdrant's sparse-vector
support carries BM25 in the same store, so this is a second index, not a second
system.

## 2. Query classification

**Decision.** A cheap **rules-first classifier**, LLM only as fallback, into three
classes that change routing:

| Class | Signal | Routing |
|---|---|---|
| `factual` | Default | Standard hybrid retrieval |
| `document_level` | "summarize", "what is this about", document scoped | Retrieve section summaries, not chunks ([04](04-chunking-and-metadata.md)) |
| `unanswerable` | No document scope, out-of-domain | Short-circuit before retrieval |

**Why classify at all.** "Summarize this document" retrieves an arbitrary chunk
under normal retrieval and produces a confidently wrong answer. Routing it to
section summaries is a one-branch fix for the most visible failure mode.

**Why rules first.** An LLM call in front of every query adds latency and cost to
the interactive path for a decision that is usually obvious from the words.

## 3. Query transformation

**Decision.** Minimal and deterministic: normalization (same normalizer as
ingestion), the embedding model's **query prefix**, and **conversational
rewriting only when a conversation history exists** — resolving "what about the
second one?" into a standalone query.

**Why only that.** Rewriting is the one transformation whose absence breaks
retrieval outright: a pronoun-only follow-up embeds to nothing useful. Expansion
and hypothetical-document generation are additional LLM calls with conditional
gains; they belong behind an eval result.

## 4. Retrieval controls

| Control | Value | Rationale |
|---|---|---|
| `RETRIEVE_TOP_K` | 50 per retriever | Reranker input; recall matters more than precision here |
| `RERANK_TOP_N` | 8 | What reaches the context budget |
| `SIMILARITY_THRESHOLD` | 0.35 cosine, tuned on eval | Floor to distinguish "weak match" from "no match" |
| `RERANK_SCORE_MIN` | Tuned on eval | The threshold that actually gates the answer |
| Mandatory filters | `tenant_id`, ACL groups, `is_active`, `embedding_version` | Never caller-supplied |

**Filters are injected by the retriever, never passed in.** The same reasoning as
the repository-level tenant predicate ([02](02-api-security.md)): a filter a
caller can forget is a filter that will be forgotten.

## 5. Retrieval outcomes

| Outcome | Behaviour |
|---|---|
| **No results** | Return `insufficient_context` with the filters applied, as a normal 200 response body — never an empty LLM call, never a fabricated answer |
| **Low confidence** (all below `RERANK_SCORE_MIN`) | Same as no results, plus the best-effort sources so the user can judge |
| **Conflicting results** | Answer *with* the conflict surfaced and both sources cited; the LLM contract requires stating disagreement rather than picking ([09](09-generation-and-verification.md)) |
| **Unauthorized** | Structurally unreachable — the filter is applied inside the search, so unauthorized chunks are never candidates. The response is identical to "no results": **no existence disclosure** |

**Why "no results" is a 200 and not a 404.** The query succeeded; the corpus had
no answer. A 404 conflates "this endpoint does not exist" with "I do not know",
and clients handle them differently.

## Done when

- Every retrieval call carries tenant, ACL and version filters that no caller can omit.
- An exact-identifier query and a paraphrase query both return the right chunk.
- A cross-tenant query is indistinguishable from an empty-corpus query.
- Zero-result and low-confidence queries produce an explicit `insufficient_context` response, verified by tests.
