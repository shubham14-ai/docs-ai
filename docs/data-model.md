# Data Model

> Related: [`api.md`](api.md) · [`concurrency.md`](concurrency.md) · [`assumptions.md`](assumptions.md)

One collection: `documents`. All MongoDB access goes through [`app/repositories/document.py`](../app/repositories/document.py) — nothing else touches the collection.

---

## Fields

| Field | Type | Notes |
|---|---|---|
| `_id` | ObjectId | Serialized as `document_id` string at the API boundary |
| `user_id` | str | Indexed |
| `title` | str | |
| `content` | str | |
| `content_hash` | str | SHA-256 of normalized content |
| `status` | enum | `queued` · `processing` · `completed` · `failed` |
| `summary` | dict \| None | Populated on completion; self-identifying (source, origin, provenance) |
| `attempts` | int | Retry counter |
| `error` | dict \| None | Typed error on terminal failure |
| `lease_expires_at` | datetime \| None | Set on claim; drives stuck-job recovery |
| `worker_id` | str \| None | Lease holder — diagnostics and the filter that drops stale writes |
| `schema_version` | int | Enables lazy per-document migration on access |
| `created_at` / `updated_at` | datetime | |

---

## Indexes

| Index | Serves |
|---|---|
| `(user_id, status, created_at desc)` | List endpoint with/without status filter; rate-limit MongoDB fallback count |
| `(user_id, content_hash)` | Cache-miss reconstruction; duplicate detection |
| `(status, lease_expires_at)` | Sweeper stuck-job query |

Compound index ordered equality-fields-first, sort-field-last — a filtered and an unfiltered list query are both served without an in-memory sort.

No standalone `content_hash` index: the cache is per-user (see [`assumptions.md`](assumptions.md)), so no cross-user query exists.

Index creation is idempotent; every process calls `beanie.init_beanie()` on boot. No migration runner — deliberately cut to stay within budget.

---

## ObjectId handling

`document_id` is a string on the wire. A malformed id returns **404**, not a 500 from a failed `ObjectId()` construction. The Pydantic field serializes to `str` on the way out.

---

## Gotcha: `tz_aware=True`

Required on the Motor client. Without it the driver returns naive datetimes and every comparison with `datetime.now(timezone.utc)` raises a `TypeError`.
