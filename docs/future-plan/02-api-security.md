# API & Security Boundary

> Status: **designed, not built** · ← [01 Architecture](01-architecture.md) · → [03 Ingestion](03-ingestion.md) · [Index](README.md)

Only the controls the architecture actually requires. Anything that exists
purely for completeness is omitted deliberately.

## 1. Authentication

**Requirement.** `user_id` arrives in the request body and is trusted. Every
isolation guarantee downstream rests on a value the client chooses.

**Decision.** OIDC-issued **JWT bearer tokens, verified in-process** against the
issuer's JWKS (cached, refreshed on unknown `kid`). A FastAPI dependency in
[`app/api/deps.py`](../../app/api/deps.py) resolves the token into a
`Principal(user_id, tenant_id, roles, scopes)`. `user_id` leaves the request
schema entirely.

**Why.** Stateless verification means no auth round-trip per request and no
session store; identity provisioning is not our problem. In-process rather than
at a gateway because there is no gateway in this deployment
([`../api.md`](../api.md)) and the claims are needed *inside* handlers for
tenant filtering, not just at the edge.

**Trade-off.** Revocation is only as fast as token TTL. Mitigated with short
access tokens (15 min) and refresh at the IdP.

## 2. Authorization & RBAC

**Decision.** Role-based, three roles — `reader`, `editor`, `admin` — enforced
by a `require(role)` dependency on the route, plus **row-level ownership checks
in the repository layer**, never in the handler.

**Why.** Route-level RBAC answers "may this caller call this endpoint"; it
cannot answer "may they touch *this* document". Pushing the ownership predicate
into `repositories/` means it cannot be forgotten by a new handler — consistent
with the existing rule that all Mongo access goes through repositories.

## 3. Tenant isolation

**Requirement.** In a RAG system a filter bug does not return an error, it
returns *someone else's document text inside an answer*. This is the highest
consequence failure in the whole design.

**Decision.** `tenant_id` from the token is a **mandatory** predicate on every
Mongo query and every vector search, injected by the repository/vector-store
layer rather than passed by callers. Vector namespaces are partitioned per
tenant. A test asserts that no repository method can be called without it.

**Why.** Isolation enforced by convention fails eventually. Enforced by the only
layer that can reach the data, it fails closed.

**Trade-off.** Cross-tenant analytics need a deliberate, separately audited
escape hatch. That is the correct default.

## 4. Rate limiting / abuse protection

**Current.** An atomic Lua check-and-increment in Redis caps concurrent
in-flight documents per user at `MAX_ACTIVE_DOCS_PER_USER`.

**Decision.** Keep the same Lua primitive; make the limit a **script argument
resolved from the tenant's plan**, and add two more dimensions that RAG
introduces: a **token/cost budget** per tenant per rolling window, and a
**query-rate limit** (sliding window) on the ask endpoint.

**Why.** The existing script already generalises — the limit is a constant only
because there is one plan. Concurrency alone is the wrong control for query and
LLM spend, where the scarce resource is tokens, not slots.

## 5. CORS

**Decision.** An explicit allow-list of origins from `Settings`, credentials
enabled, no wildcard. Configured once in the app factory.

**Why.** The browser dashboard ([`../ui.md`](../ui.md)) is a first-class client;
`*` with credentials is rejected by browsers anyway and is a data-exfiltration
path when it works.

## 6. Request validation

**Decision.** Pydantic schemas remain the only validation layer, extended with
**upload-specific limits enforced before any bytes are parsed** — size cap,
magic-byte type sniffing, decompression-ratio cap, encryption detection. Detail
in [03](03-ingestion.md).

**Why.** A parser is the largest attack surface a document platform has. Validate
before parsing, never after.

## 7. API versioning

**Current and unchanged.** Per-capability versioning in
[`app/api/router.py`](../../app/api/router.py). New RAG surfaces enter as new
capabilities, not as a global version bump:

```text
POST   /api/v1/documents              upload (multipart)      existing, extended
GET    /api/v1/documents/{id}         status + summary        existing
POST   /api/v1/documents/{id}/versions new version            new
POST   /api/v1/query                  ask a question          new capability
GET    /api/v1/query/{id}/citations   resolve citations       new capability
```

**Why.** `query` will iterate far faster than `documents`. The registry already
lets it reach v2 alone.

## 8. Error handling

**Current and unchanged.** Every failure is an `AppError` with `code`,
`http_status` and `transient`, converted by one handler into one envelope.

**Decision.** New RAG failures join the same taxonomy, and the classification is
the operational contract: `UnparseableDocument`, `EncryptedDocument`,
`FileTooLarge`, `UnsupportedType` are **terminal**; `EmbeddingProviderError`,
`VectorStoreUnavailable`, `LLMProviderError` are **transient** and retryable;
`InsufficientContext` and `UngroundedAnswer` are terminal query outcomes with
their own status codes, not 500s.

**Why.** The retry loop already keys on `transient`. Getting the flag right is
the entire difference between a retry storm and a clean failure.

## 9. Beyond a flat limit of 3

Once identity is real, the limit stops being one number. The atomic Lua
check-and-increment already generalises — the limit becomes a script argument
rather than a constant.

| Dimension | Today | Then |
|---|---|---|
| Scope | Per `user_id` | Per user *and* per tenant |
| Value | Flat `MAX_ACTIVE_DOCS_PER_USER` | Per-plan quota, resolved at request time |
| Window | Concurrent in-flight only | Concurrency, rolling volume, and token spend |

**Cost of this phase.** Tests currently mint arbitrary user ids freely and would
need a token fixture; the dashboard ([`../ui.md`](../ui.md)) gains a login. Both
are one-time and both are cheaper now than after a corpus exists.

Open items: [12](12-open-architectural-decisions.md) §4 (token issuer), §5 (quota state).

## Done when

- No handler or schema reads `user_id` from a request body.
- A repository call without a tenant predicate fails a test, not review.
- The rate limit is resolved per plan and covers concurrency, query rate and token budget.
- Every new failure mode has an `AppError` subclass with a deliberate `transient` value.
