# Deduplication & Document Versioning

> Status: **designed, not built** · ← [05 Embedding & vector store](05-embedding-and-vector-store.md) · → [07 Query & retrieval](07-retrieval.md) · [Index](README.md)

## 1. Hashing

**Current.** `hash_content(content)` produces a per-document SHA-256 that keys
the per-user summary cache.

**Decision.** Extend the same idea to two levels, both computed over
**normalized** text so formatting noise does not change a hash:

```text
document_hash = sha256(normalized_full_text)
chunk_hash    = sha256(chunk_text + section_path + source_type)
```

`chunk_hash` includes the section path because identical boilerplate under two
different headings is two different retrieval units — merging them would misplace
the citation.

**Why hash chunks at all.** Editing one paragraph of a 400-page contract changes
one chunk. Without chunk hashes, a new version re-embeds 400 pages; with them, it
re-embeds one chunk and copies the rest by reference. Embedding is the dominant
ingestion cost, and this is the single largest lever on it.

## 2. Version identifiers

| Identifier | Changes when | Purpose |
|---|---|---|
| `document_version` | New bytes uploaded for the same logical document | Retrieval filters to the LIVE version |
| `embedding_version` | Embedding model or its config changes | Prevents mixed-model comparison ([05](05-embedding-and-vector-store.md)) |
| `index_version` | Index schema/params change | Enables index rebuild without a document change |

The three are independent on purpose: a re-embed must not look like a document
edit, and an index rebuild must not touch document history.

## 3. Zero-downtime update

**Requirement.** A document is being queried while a new version is ingested. At
no point may queries see a half-indexed version, and at no point may they see
nothing.

**Decision.** Build the new version fully, validate, then **flip a flag** —
never mutate in place.

```text
Upload v(n+1) → status=processing, is_active=false
   → parse → chunk → diff chunk_hashes against v(n)
   → embed only new/changed chunks; reference unchanged vectors
   → index under document_version = n+1, still is_active=false
   → VALIDATE: expected chunk count present, sample search returns them
   → atomic flip:  v(n+1).is_active = true ;  v(n).is_active = false
   → retention: v(n) chunks deleted after RETENTION_VERSIONS newer versions exist
```

The flip is a **compare-and-set on the document record** filtered on the version
the caller believes is live — the same guard rule the current state machine uses
([`../concurrency.md`](../concurrency.md)). Two concurrent ingests of the same
document cannot both win.

**Why flag-flip rather than delete-then-write.** Delete-then-write has a window
where the document is unretrievable, and a crash inside that window leaves the
document permanently gone. A flip has no such window, and rollback is the reverse
flip.

**Trade-off.** Two versions of the chunks exist simultaneously, so storage peaks
at roughly double for that document. Bounded by retention.

## 4. Failure semantics

| Failure | Effect on the live version |
|---|---|
| Parse/chunk/embed fails | **None.** v(n) stays live; v(n+1) is marked `failed` and its partial chunks are swept |
| Validation fails | **None.** No flip happens; v(n+1) marked `failed` |
| Crash mid-index | **None.** v(n+1) never flipped; the existing lease sweeper reclaims it and retries |
| Flip fails | **None.** The CAS either applied or did not; there is no partial flip |

**The invariant:** a failed ingest can never degrade what is already live. This is
the same guarantee the current pipeline gives for summaries, extended to the
index.

Partial chunks from a failed version are removed by extending the existing
sweeper with a "delete vectors for non-live, non-processing versions" pass —
reusing the recovery mechanism rather than adding a second one.

## 5. Why chunk hashing is the incremental-processing mechanism

Today the cache is all-or-nothing: change one character and the whole document
is reprocessed. Chunk hashing is what makes reprocessing proportional to the
edit — and it only pays off once processing has real per-unit cost, which
parsing and embedding supply.

Two constraints the hashing must satisfy, or the saving evaporates:

1. **Boundary stability.** A change inside chunk 3 must not reshuffle chunks
   4..n. Structure-aware splitting ([04](04-chunking-and-metadata.md)) gives
   this for free — boundaries come from headings, which an unrelated edit does
   not move. This is a second, independent reason to prefer it over similarity-based
   boundaries, which shift whenever nearby text changes.
2. **Segmentation is a compatibility surface.** Changing the splitter
   invalidates every stored chunk hash. It is therefore versioned exactly like
   the embedding model, and a splitter change is a reindex, not a patch.

**Cost.** Summary provenance gets harder: a document summary is now partly
reused and partly fresh, and the self-identifying summary template
([`../api.md`](../api.md)) has to say so honestly rather than claiming a single
`source`.

## Done when

- Re-uploading an identical file embeds zero chunks.
- Editing one paragraph re-embeds only the affected chunks, proven by a counter in the ingest result.
- Queries during an ingest always return the previous version, never a mixture.
- A crash at any stage of a new version leaves the live version untouched, proven by a test per stage.
