# LLM Generation & Verification

> Status: **designed, not built** · ← [08 Reranking & context](08-reranking-and-context.md) · → [10 Evaluation & observability](10-evaluation-and-observability.md) · [Index](README.md)

## 1. Generation contract

```text
System role      You answer strictly from the provided context.
Task             Answer the user's question.
Context          Numbered, authorized chunks with citation ids.
Response format  Structured JSON: answer, citations[], confidence, sufficient
Rules            1. Use only the context. No outside knowledge.
                 2. Every factual sentence cites at least one chunk id.
                 3. If the context does not answer the question, set
                    sufficient=false and say so. Do not partially answer.
                 4. If sources disagree, state the disagreement and cite both.
                 5. Never mention a document not present in the context.
```

**Decision.** The model returns **structured output** (schema-constrained), not
prose.

**Why.** Citations parsed out of free text with a regex is the standard way this
breaks. A schema makes the citation list a first-class field that verification
([§3](#3-verification)) can check mechanically, and makes `sufficient=false` a
value rather than a phrase to pattern-match.

**Trade-off.** Slightly more rigid answers. Acceptable — verifiability is the
product.

## 2. Model configuration and abstraction

**Decision.** One `LLMClient` protocol next to `Embedder` and `Reranker`, in
`app/services/`. Temperature 0.1, response schema enforced, `max_tokens` capped,
hard timeout, and the model id recorded on every response for evaluation.

**Why the abstraction lives at the service layer, not in a framework.** The
pipeline needs exactly three provider operations — embed, rerank, generate. Three
small protocols keep provider choice a one-class change and keep the request path
debuggable, which a general orchestration framework would obscure while pulling
in its own control flow.

**Which provider satisfies the protocol, and what happens when it fails**, is a
separate concern — see [13 LLM gateway](13-llm-gateway.md).

The prompt above is **not** a string literal next to the client. It is a
versioned, labelled entry in the prompt registry, fetched by name and label and
recorded on every response alongside the model id — see
[10](10-evaluation-and-observability.md) §3. The model configuration in this
section is versioned with it, because changing temperature changes the contract
as surely as changing rule 3 does.

**Why temperature 0.1 rather than 0.** Not 0 because a small amount of sampling
avoids degenerate repetition; not higher because variability is a liability when
answers must be reproducible for evaluation.

## 3. Verification

**Requirement.** The model can still cite a chunk that does not support the
sentence. Verification is what turns "probably grounded" into "checked".

**Decision.** A **two-tier verifier**: cheap deterministic checks on every
response; an LLM judge only when those pass but confidence is low.

**Tier 1 — deterministic, every response:**
- **Schema validation.** Malformed output → regenerate once, then reject.
- **Citation resolution.** Every cited id must exist in the assembled context. A
  hallucinated citation id is the single strongest hallucination signal available
  and costs nothing to detect.
- **Citation coverage.** Every factual sentence carries at least one citation.
- **Sufficiency consistency.** `sufficient=false` must not accompany a
  substantive answer.

**Tier 2 — LLM judge, conditional:** an entailment check of each claim against
its cited chunk, run when Tier 1 passes but rerank scores were near the floor, or
on a sampled fraction of traffic for monitoring.

**Why not judge everything.** It doubles cost and latency on every query to catch
a case Tier 1 already catches most of. Proportionality is the requirement here.

## 4. Outcomes

| Outcome | When | Behaviour |
|---|---|---|
| **ACCEPTED** | All Tier 1 checks pass; Tier 2 not triggered or passed | Return answer with citations |
| **REGENERATED** | Schema invalid, or unresolvable citation, on the first attempt | One retry with the failure fed back into the prompt. Bounded at one — a second failure is a context problem, not a sampling problem |
| **REJECTED** | Retry also fails, or Tier 2 finds unsupported claims | Return `insufficient_context` with the retrieved sources listed. Never return the unverified answer |

**Why rejection returns sources rather than nothing.** The user gets what the
system did find and can judge for themselves — far more useful than a bare
failure, and it does not require the system to assert anything it cannot support.

## Done when

- Every answer is structured, with citations resolvable to `chunk_id`s in the context.
- A hallucinated citation id cannot reach the client, proven by an injected-fault test.
- `insufficient_context` is returned rather than a low-grounding answer, with sources attached.
- Regeneration is bounded at one attempt and observable in metrics.
