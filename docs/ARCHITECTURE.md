# Architecture note

Two systems record the same events; neither is authoritative. The job is to find where they
disagree, per tenant, without ever serving one tenant's row to another, and to answer questions
over the result without stating numbers we can't justify.

## 1. Component boundaries

```
 CSV exports                 ┌──────────── ingestion ────────────┐
 locations / A / B ──► API ──► file store + ingestion_job ──► queue ──► ingestor (per dataset)
                             └───────────────────────────────────┘        │ upsert, tenant_id = NULL
                                                                           ▼
                                                              events (all systems, one table)
                                                                           │ queue
                             ┌───────── reconciliation ───────────┐        ▼
                             │ engine (pure): group → tenant →    │◄── load all events
                             │ classify → compare                 │
                             └────────────────────────────────────┘──► events.tenant_id, discrepancies
                                                                           │
 question ──► query API ──► planner (LLM: extract, never SQL) ──► fetch per org under RLS ──► answer
```

| Module | Owns | Why the seam is here |
|---|---|---|
| `ingestion/` | parsing, validation, normalization, loading | Dirty input is contained: every row is either loaded with its raw form kept, or rejected with a reason code. Nothing downstream re-parses text. |
| `reconciliation/engine.py` | the matching rules | **Pure** (no I/O): the rules are the product, so they are testable in isolation and runnable over files (`reconciliation/cli.py`) or the database (`reconciliation/service.py`) unchanged. |
| `reconciliation/service.py` | tenant assignment + persisting results | The only writer of `event.tenant_id` and `discrepancy`. Runs under an advisory lock so two runs can't interleave. |
| `query/` | questions → rows | The model's output is a *plan*; code owns tenancy, SQL and every number. |
| `db/` | schema, repositories, RLS | Isolation is enforced here, below the application. |

Ingestion and reconciliation are separate queue consumers so that a new export is visible only after
it has been checked against the other system, and so that re-running reconciliation needs no
re-ingest. The queue is plumbing, not architecture: the same seams work as a nightly batch.

## 2. Data model for the reconciled view

- **`event`**: one row per source row from any system (`source_system`, `external_event_id`),
  with the common fields (`location_id`, `event_date`, `amount`, `status`), system-specific fields
  in `attributes`, the untouched `raw_row`, and **`match_key`**: the record the row is about
  (System A's `record_id`; System B's `record_ref` normalized, e.g. `' REC - 1070 '` → `REC-1070`).
  A new source system is a new ingestor, not a new table.
- **The canonical record** is one per `match_key`: the group of every system's rows about that
  record, reduced to one line (`ReconciledRecord`: status, A record ids, B entry ids, A amount,
  summed B amount, date, location, exception codes, notes). Neither system wins; when they
  disagree, both values are kept side by side.
- **Status** is one of `RECONCILED` (they agree, possibly after normalizing a reference or summing
  a split), `EXCEPTION` (at least one disagreement), or `HIDDEN` (tenant conflict).
- **Rows that don't reconcile** are not dropped or "fixed". Each disagreement becomes a
  **`discrepancy`** row keyed on `(match_key, type, field)`, with `values_by_system`, machine
  evidence, and `first_detected_at` / `last_detected_at` / `resolved_at`. Re-runs update in place,
  so a disagreement that disappears is marked `RESOLVED` (with history), never silently lost.
- **Rows that can't be used at all** (unknown location, unreadable date, conflicting duplicate)
  are rejected at ingestion with a reason code and the raw row, on the ingestion job.

### Exception taxonomy

Codes are grouped by what a person has to do (`reconciliation/taxonomy.py`), each with a severity,
a plain-English meaning and an action:

| Kind | Codes | Who acts |
|---|---|---|
| **Disagreement** (A and B tell different stories) | `TENANT_CONFLICT` (critical) · `MISSING_IN_B` · `ORPHAN_IN_B` · `VALUE_MISMATCH` · `MISSING_VALUE` · `STATUS_MISMATCH` · `DUPLICATE_IN_A` (high) · `DUPLICATE_IN_B` · `DATE_MISMATCH` · `LOCATION_MISMATCH` (medium) | operations, per record |
| **Input** (row not usable) | `UNKNOWN_LOCATION` · `LOCATION_TENANT_CHANGED` (critical) · `CONFLICTING_DUPLICATE_KEY` · `UNPARSABLE_RECORD_ID` (high) · `VALIDATION_ERROR` · `WRONG_COLUMN_COUNT` (medium) · `DUPLICATE_ROW` (info) | whoever owns the export |
| **Note** (we interpreted, meaning unchanged) | `REF_NORMALIZED` · `AMOUNT_REFORMATTED` · `SPLIT_ENTRIES_SUMMED` · `SOURCE_FIELD_BLANK` | nobody; visible for audit |

Notes are deliberately *not* exceptions: `rec1034` is not a disagreement, but hiding the
interpretation would be. Evidence carries the diagnosis where we have one: `VALUE_MISMATCH` with
`b_equals_a_base_value` says "System B recorded the amount before the ~28% adjustment", which is
the actual drift pattern in this data (REC-1003, 1027, 1088).

The hard cases in the attached data, and what they become: two System B entries for REC-1055 that
*sum* to A's total are a split (reconciled, noted), while two identical entries for REC-1042 are a
duplicate (exception, counted once); `1,25,400.00` is parsed as 125,400.00 and is *still* wrong;
REC-1077 is ORG-A in System A and ORG-B in System B, so it belongs to neither.

## 3. Tenant isolation

`locations.csv` is the only source of `location → org`. An event's tenant is derived from its own
location, and is **assigned only by reconciliation** (ADR-0001):

1. **Fail closed at write time.** Ingestion always writes `tenant_id = NULL`; a re-ingested row is
   reset to `NULL`. Reconciliation sets it only if every row sharing the `match_key` resolves to the
   same org; otherwise every row stays `NULL` and a `TENANT_CONFLICT` (with `tenant_id = NULL`) is
   raised. A location can never silently move orgs (`LOCATION_TENANT_CHANGED`).
2. **Enforced by the database, not by remembering a WHERE clause.** Tenant reads run in
   `tenant_data_store(tenant_id)`: every transaction starts with `SET LOCAL ROLE
   dealeros_tenant_reader` and `app.tenant_id`. That role has `SELECT` only, on four tables, and
   Postgres **Row-Level Security** policies return only rows whose `tenant_id` equals
   `app.tenant_id`. `NULL` never matches, an unset tenant matches nothing, and `SET LOCAL` cannot
   leak to the next user of a pooled connection. A query with no filter at all (`SELECT * FROM
   event`) still returns one tenant's rows; tests assert exactly that.
3. **Defence in depth in code.** Tenant repositories require `tenant_id` on every read and have no
   unscoped `get_by_id`; every generated query also filters by `tenant_id`.
4. **The public API takes the org from credentials, never from the question.** A tenant naming
   another org gets `403`. Multi-org answers (internal API only) query each org separately under
   its own RLS scope and return them separately; rows are never mixed.

5. **The API process has no owner credentials** (migration `1.3.0`). Table owners bypass RLS, so
   the API logs in as `dealeros_api`: not an owner, not a superuser, and **no privileges at all on
   `event`, `discrepancy` or `location`**. A new endpoint that forgets `tenant_data_store` and runs
   `SELECT * FROM event` gets `permission denied`, not every org's rows. The only way in is the
   switch to `dealeros_tenant_reader` inside `tenant_data_store`. The API can write only its own
   bookkeeping (`ingestion_job`, `query_log`, `tenant_credential`), and can't delete anything. The
   consumer keeps the owner login because reconciliation must see every org to detect conflicts;
   it serves no tenant requests. We don't `FORCE ROW LEVEL SECURITY`, because that would apply RLS
   to the owner too and break exactly that cross-org check.

## 4. Deliberately not built in year one

| Not building | What has to become true first |
|---|---|
| **Per-tenant database logins** (one Postgres role per org instead of one reader role + `app.tenant_id`) | Tenants running their own queries or tools directly against the database. Today every tenant read is mediated by our code, and one reader role under RLS is enough. |
| **Fuzzy matching** (date tolerance, amount tolerance, matching without a reference) | Evidence that exact matching produces false exceptions people routinely dismiss. Until then every tolerance hides real drift. |
| **Choosing a winner / auto-correcting a source system** | The business names an authoritative system per field. The brief says neither is. |
| **Totals and aggregates beyond "sum over agreed records"** | A per-field rule for which system's number is used when they disagree (see GROUNDING.md). |
| **Tenant self-service UI, SSO, per-user permissions** | Tenants use it directly; today the consumers are internal and a few API clients. |
| **S3 drop + automatic triggers, scheduling** | More than a handful of exports a day; the manual upload + queue already has the right seams. |
| **Incremental reconciliation, `COPY` bulk loads, a warehouse (Databricks)** | A measured full re-run that is too slow or too large for memory. Today a run loads every event; the brief's scale (a few million rows) is the point to measure, not to pre-optimise. |
| **Category / actor master data** | A source for it; today the codes carry no meaning and appear in both orgs, so their scope is unknown. |
