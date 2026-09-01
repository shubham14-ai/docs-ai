# LLM Gateway — Multiple Providers

> Status: **designed, not built** · Related: [05 Embedding](05-embedding-and-vector-store.md) · [08 Reranking](08-reranking-and-context.md) · [09 Generation & verification](09-generation-and-verification.md) · [Index](README.md)

[05](05-embedding-and-vector-store.md), [08](08-reranking-and-context.md) and
[09](09-generation-and-verification.md) each put one provider behind one
protocol — `Embedder`, `Reranker`, `LLMClient`. That settles *how the pipeline
calls a model*. It does not settle **which** model answers a given call, what
happens when that provider is down, or who pays for it. This note settles that,
and only that.

## 1. Requirement

A single hardcoded provider fails in four ways that are not hypothetical:

- **Availability.** A provider outage takes down generation entirely, even
  though a second provider could answer the same request.
- **Cost.** Every query pays frontier-model prices, including the ones a small
  model answers identically.
- **Capability.** No single provider is best at generation, embedding and
  reranking at once, and the best one changes between releases.
- **Lock-in.** Model deprecations are announced, not negotiated. A migration
  must be a config change, not a refactor.

## 2. Decision — one gateway behind the existing protocols

A single `app/services/gateway/` module implements `Embedder`, `Reranker` and
`LLMClient`. Call sites are unchanged: the pipeline still depends on the
protocol, never on a provider. Behind it:

```text
pipeline ── LLMClient ──▶ Gateway ──▶ router ──▶ adapter ──▶ provider SDK
                            │                     (anthropic │ openai │ …)
                            ├─ router      route by task, not by name
                            ├─ fallback    ordered chain, typed failures only
                            ├─ budget      per-tenant token + spend ceiling
                            └─ telemetry   provider, model, tokens, cost, latency
```

**Why in-process, not a sidecar proxy.** A gateway product (LiteLLM, a
self-hosted proxy) is one more deployable to run, secure and keep available on
the request path, and it hides per-call routing from the traces
[10](10-evaluation-and-observability.md) depends on. The pipeline needs three
operations; a module that speaks them directly is smaller than the thing it
would replace. Revisit if a second service in another language needs the same
routing.

**Trade-off.** Adapter code for each provider is maintained here rather than
consumed as a dependency. Accepted: an adapter is a request/response mapping
and a typed-error translation, and it is the layer that must be trustworthy.

## 3. Routing

**Decision.** Routes are declared as `(task, tier) → ordered model list` in
configuration, never as a provider name in code.

| Task | Tier | Chosen for |
|---|---|---|
| `generate` | `quality` | Grounded answers with citations — the user-facing path |
| `generate` | `cheap` | Query rewriting, keyword extraction, ingestion summaries |
| `judge` | `quality` | Tier 2 entailment checks ([09 §3](09-generation-and-verification.md#3-verification)) |
| `embed` | — | Must match the index; see below |
| `rerank` | — | Cross-encoder scoring |

**Why tiers rather than per-call model ids.** The call site knows what kind of
work it is asking for; it does not know which model is currently cheapest or
best at it. Naming a tier keeps that knowledge in one place, where a swap is
reviewable.

**Embedding is not routable at request time.** A vector index is defined by the
model that wrote it — the model version is already part of the index name
([05](05-embedding-and-vector-store.md)). Routing a query embedding to a
fallback provider returns a vector in a different space and silently produces
nonsense neighbours. **An embedding call falls back to nothing.** If the index's
provider is unavailable, retrieval fails loudly, and switching providers is a
reindex ([06](06-versioning-and-dedup.md)), not a route change.

## 4. Fallback

**Decision.** An ordered chain per route, attempted on **transient** failures
only: timeout, 429, 5xx, connection error. A 4xx that is not 429 — malformed
request, content filter, auth — is terminal and is never retried against
another provider.

Each provider gets a circuit breaker keyed on `(provider, model)`, so a
degraded provider is skipped rather than re-timed-out on every request. The
existing `AppError` taxonomy already carries `transient`; adapters translate
provider SDK exceptions into it, and nothing above the adapter sees a
provider-specific exception type.

**Why not fall back on every error.** Re-sending a content-filtered or malformed
request to a second provider spends money to get the same answer, and it turns
one clear failure into two unclear ones.

**Consequence for verification.** A fallback answer comes from a different model
than the primary, so the response's recorded `model_id` must be the model that
actually answered. [09](09-generation-and-verification.md) verifies the response
it received; a mismatch between requested and serving model is a metric, not a
rejection.

## 5. Cost and budget

**Decision.** The gateway records `provider`, `model`, prompt/completion tokens,
computed cost and latency on every call, attributed to `tenant_id`, and enforces
a per-tenant spend ceiling before dispatch.

**Why enforcement lives here.** It is the only place that sees every model call
from both the ingestion and the query paths. A limit enforced at the API edge
counts requests, not spend, and one request can fan out to an embedding, a
rerank, a generation and a judge.

Exceeding the ceiling returns a typed quota error, the same shape as the
existing rate-limit rejection. Quota state and its consistency guarantees are
deliberately open — see [12 §5](12-open-architectural-decisions.md#5-quota-state).

## 6. Keys and configuration

Provider credentials are environment-supplied and resolved once at startup, like
every other secret. Routes, tiers and fallback chains live in `Settings` — no
model id, provider name or price appears in a handler, a pipeline stage or a
test. Consistent with rule 6 in [`../../CLAUDE.md`](../../CLAUDE.md): a new
provider is a config entry plus an adapter, nothing else.

## Done when

- No pipeline code names a provider; every model call goes through the gateway.
- Killing the primary generation provider degrades to the fallback with no code change, proven by a fault-injection test.
- A terminal 4xx is not retried against a second provider, proven by test.
- An embedding call never falls back; a provider outage fails retrieval loudly.
- One trace shows provider, model, tokens and cost per call, attributed to a tenant.
- A per-tenant spend ceiling rejects with a typed quota error before dispatch.
