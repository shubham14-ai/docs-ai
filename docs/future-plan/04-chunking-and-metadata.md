# Chunking, Metadata, Summaries & Keywords

> Status: **designed, not built** · ← [03 Ingestion](03-ingestion.md) · → [05 Embedding & vector store](05-embedding-and-vector-store.md) · [Index](README.md)

## 1. Chunking

**Requirement.** Produce retrieval units that are self-contained, single-topic,
citable, and cheap enough to produce that a large corpus is affordable.

**Decision.** **Structure-aware recursive splitting**, dispatched per source
type. One algorithm, one parameter set, with the split boundaries supplied by the
parser rather than guessed:

```text
ParsedDocument
  → group blocks by section (from section_path)
  → for each section:
        if it fits the budget      → one chunk
        else                       → recursive split on
                                     paragraph → sentence → token
  → tables: one chunk per table (never split); oversized tables split
    by row groups with the header row repeated
  → spreadsheets: one chunk per row-group, sheet name + header in the text
  → prepend the section_path breadcrumb to every chunk's embedded text
```

**Why this and not semantic chunking.** Semantic chunking embeds every sentence
and cuts at similarity troughs — one extra embedding pass over the entire corpus
for boundaries the document already declares in its headings, and boundaries that
shift when the embedding model changes. Structure-aware splitting reads
boundaries the author wrote. It is deterministic (so chunk hashes are stable),
adds no ingestion latency or cost, and is debuggable: a bad chunk maps to a
visible heading, not to a cosine curve.

**Why dispatch per type rather than one strategy for everything.** A spreadsheet
row-group and a PDF section are not the same object. The dispatcher is a small
table keyed on source type; the recursive splitter underneath is shared.

**Trade-off.** Poorly structured documents (a heading-free PDF) degrade to plain
recursive splitting. Acceptable: that is exactly the case where semantic
chunking's cost would be justified, and it can be added later as one more branch
of the same dispatcher without touching anything else.

**Size and overlap.** Chunk budget is set in **tokens, measured with the
embedding model's own tokenizer**, not characters — characters are a proxy that
drifts per language. Starting point, tuned against the evaluation set in
[10](10-evaluation-and-observability.md):

| Parameter | Start | Rationale |
|---|---|---|
| `CHUNK_MAX_TOKENS` | 512 | Fits comfortably inside embedding-model context; ~8 chunks per LLM context budget |
| `CHUNK_OVERLAP_TOKENS` | 64 (~12%) | Enough to carry a sentence across a forced cut; small enough not to inflate the index |
| `CHUNK_MIN_TOKENS` | 64 | Below this, merge into the neighbour — fragments dilute retrieval |

All three are `Settings` fields. Overlap applies **only to forced splits inside a
section**, never across section boundaries — overlapping across a heading
reintroduces the topic mixing the structure-aware split exists to prevent.

## 2. Chunk metadata model

Every field earns its place by being read by retrieval, filtering, citation or
governance. Nothing is stored "for later".

| Field | Read by |
|---|---|
| `tenant_id`, `user_id` | Mandatory isolation predicate ([02](02-api-security.md)) |
| `document_id`, `chunk_id` | Citation resolution, delete-by-document |
| `document_version`, `is_active` | Version filtering — only LIVE chunks are retrievable ([06](06-versioning-and-dedup.md)) |
| `source_type` | Per-type retrieval tuning; UI display |
| `page`, `section_path` | Citation — "p. 14, §Limits" |
| `chunk_index` | Context ordering and neighbour expansion ([08](08-reranking-and-context.md)) |
| `content_hash` (chunk) | Dedup — skip re-embedding unchanged chunks |
| `embedding_model`, `embedding_version` | Re-embedding sweeps ([05](05-embedding-and-vector-store.md)) |
| `acl` (group ids) | Permission filter at query time |
| `pii_tags` | Governance policy at retrieval time ([03](03-ingestion.md)) |
| `created_at`, `updated_at` | Recency filters, audit |

**Storage split.** Metadata lives in **both** places: the full record in Mongo
(source of truth), and the filterable subset copied into the vector store's
payload so filtering happens inside the ANN search rather than after it. Chunk
*text* lives in Mongo only; the vector store holds vectors and filter fields.

**Why.** Consistent with the existing rule that Redis — and now the vector store
— is an accelerator, never the record. The whole index must be rebuildable from
Mongo alone.

## 3. Summaries and keywords

**Decision.** Two levels, both LLM-generated at ingestion, both stored on the
Mongo document:

- **Document summary** — replaces today's mock at the existing `Summarizer` seam
  ([04](04-chunking-and-metadata.md)). Used in the UI, and as the routing hint for
  document-level queries.
- **Section summaries + keywords** — one short summary and 5–8 keywords per
  top-level section. Keywords feed the **lexical half of hybrid retrieval**
  ([07](07-retrieval.md)); section summaries are what a "what is this document
  about" query retrieves instead of an arbitrary chunk.

**Why generate these at ingestion rather than at query time.** They are computed
once per document version and read on every query. Query-time generation would
put an LLM call in front of retrieval — latency on the interactive path for a
result that never changes.

**Trade-off.** Ingestion cost rises by one LLM call per document plus one per
section. Bounded by capping sections summarized and by skipping unchanged
sections on re-ingest, which the chunk hash already tells us.

## 4. Replacing the mock summarizer

`app/services/summarizer.py` is deterministic and is the **only** place summary
text is composed — it was written as a single seam so the swap is a one-file
change. `Summarizer.render` keeps its signature; what fills the placeholders
changes.

What must change alongside it:

| Concern | Change |
|---|---|
| Latency | `PROCESSING_MIN/MAX_SECONDS` stop being simulation and become a timeout budget |
| Failure | Provider errors join the existing transient/terminal split — rate limits and timeouts are transient, a malformed prompt is not |
| Cost | A cache hit stops being a latency win and becomes a money win, which changes the cache-scope calculus ([12](12-open-architectural-decisions.md) §1) |
| Determinism | Unit tests assert a deterministic template; they keep asserting the template, with the model output mocked |
| Idempotency | A retry after a partial provider response must not double-charge — the claim CAS already prevents double-processing |

**Cost.** The system stops being self-contained: a third-party outage becomes a
real source of transient failure, and the retry path in
[`../failure-handling.md`](../failure-handling.md) starts carrying real traffic
instead of a 10% dice roll.

## Done when

- Chunk boundaries never cross a heading; tables are never split mid-table.
- Chunk sizes are measured with the embedding tokenizer and configurable.
- Every chunk carries the full metadata table above; the filterable subset is present in the vector payload.
- The entire index can be rebuilt from Mongo with no other input.
