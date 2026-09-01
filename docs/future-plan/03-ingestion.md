# Document Ingestion, Parsing & Structure

> Status: **designed, not built** · ← [02 API & security](02-api-security.md) · → [04 Chunking & metadata](04-chunking-and-metadata.md) · [Index](README.md)

## Pipeline

```text
Upload → Validate → Identify type → Parse (per type) → Preserve structure
      → Normalize → Chunk → Metadata/Summary/Keywords → Safety checks
      → Embed → Index
```

Stages up to *Normalize* run in the worker's existing `ProcessingPipeline`,
which already owns claim, retry and settle. Chunk onward is
[04](04-chunking-and-metadata.md) and [05](05-embedding-and-vector-store.md).

## 1. Upload transport

**Requirement.** Bytes must not occupy an API worker; submit does no work.

**Decision.** **Presigned direct-to-object-storage upload.** `POST /documents`
returns an upload URL plus a document id in `pending_upload`; the client PUTs to
object storage; a completion call (or bucket notification) moves the document to
`queued` and publishes to the existing stream.

**Why.** It preserves the property the current design was built around — the API
does no heavy work — and it makes large files a storage problem rather than a
process-memory problem.

**Trade-off.** Object storage becomes a third stateful dependency, with its own
row in the degradation table ([`../failure-handling.md`](../failure-handling.md)).

## 2. Validation, before parsing

Ordered, and the order is the point:

1. **Size cap** at the edge (presign enforces max content-length).
2. **Magic-byte sniffing** — the extension and declared content type are both client claims.
3. **Decompression-ratio cap** — DOCX/XLSX are ZIP containers; a 40 KB upload can expand to gigabytes.
4. **Encryption detection** — an encrypted PDF must fail fast with a clear terminal error, not deep inside a parser.

Only then does a parser see the bytes.

## 3. Parsing strategy per type

One parser per type, behind a single `Parser` protocol returning a normalized
`ParsedDocument(blocks[])` where each block carries `type`, `text`, `page`,
`section_path`.

| Type | Parser | Why this one |
|---|---|---|
| PDF (digital) | **PyMuPDF** | Fastest pure-Python extraction with real geometry — coordinates and font sizes are what heading detection needs. Layout-aware, no service dependency. |
| PDF (scanned) | PyMuPDF → **OCR fallback** when a page yields near-zero text | Scanned pages are otherwise silently ingested as empty chunks, which is worse than failing. |
| DOCX | **python-docx** | Reads the real OOXML outline — heading styles, lists and tables are explicit in the format, so structure is read rather than inferred. |
| XLSX | **openpyxl**, sheet → row-group serialization | A spreadsheet has no prose to segment; the useful unit is a sheet with its header row repeated per row-group. |
| Markdown | **markdown-it-py** to an AST | The heading hierarchy is already unambiguous; parsing to an AST keeps it that way instead of regexing it back out. |
| Plain text | Direct, paragraph-split | Nothing to recover. |

**Why one library per type rather than a single universal extractor.** Universal
extractors normalize to plain text and discard the structure that each format
states explicitly. That structure is the input to chunking, and chunk quality is
the dominant term in retrieval quality.

**Trade-off.** Five dependencies and five failure modes instead of one. Contained
by the shared `Parser` protocol and one `UnparseableDocument` error.

## 4. Structure preservation

The parser output must retain **pages, heading hierarchy, section boundaries,
tables, and lists**.

**Why it matters — concretely.** A chunk that crosses a heading boundary mixes
two topics and matches neither query well. A table flattened into prose loses the
column that gave each cell meaning. A chunk with no page or section cannot be
cited, and an answer that cannot be cited cannot be verified
([09](09-generation-and-verification.md)).

**Decision.** Tables are extracted separately and serialized as Markdown tables,
kept whole, and never split by the text chunker. Headings become a
`section_path` (`["Chapter 2", "Limits"]`) attached to every block beneath them.

## 5. Normalization

Whitespace collapse, de-hyphenation across line breaks, repeated
header/footer removal, Unicode NFKC. Deterministic and pure — it must run before
hashing so the same document always yields the same hash ([06](06-versioning-and-dedup.md)).

## 6. Safety / governance checks

**Decision.** One pass over normalized text before embedding: **PII detection and
tagging** (not redaction) into chunk metadata, plus per-tenant policy on whether
PII-tagged chunks are retrievable.

**Why tag, not redact.** Redaction is irreversible and destroys legitimate
content — a contract is largely PII. Tagging pushes the decision to retrieval
time, where the caller's permissions are known.

## 7. Cost of this layer

- A new failure taxonomy — unparseable, encrypted, corrupt, too large, wrong
  type — each needing an `AppError` with a **terminal** classification, since
  none of them are worth retrying.
- Object storage becomes a third stateful dependency, with its own row in the
  degradation table ([`../failure-handling.md`](../failure-handling.md)).
- Parsing has real per-page cost, which is precisely what makes the chunk-level
  dedup in [06](06-versioning-and-dedup.md) pay off.

Open items: [12](12-open-architectural-decisions.md) §2 (where extracted text lives), §3
(separate parse worker pool).

## Done when

- Each of the five types round-trips to `ParsedDocument` with pages and section paths populated.
- A zip bomb, an encrypted PDF and a spoofed extension each fail with a distinct terminal `AppError` before parsing begins.
- Tables appear in output as whole Markdown tables, never split.
- A scanned PDF either OCRs or fails loudly — never ingests empty.
