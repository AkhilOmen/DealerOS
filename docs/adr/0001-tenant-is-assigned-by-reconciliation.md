# ADR-0001: A row's tenant is assigned by reconciliation, never at ingestion

**Status:** accepted

## Context

`locations.csv` maps every location to exactly one org. Both source systems put a location on every
row, so the obvious implementation is: when a row is ingested, look up its location and set
`tenant_id`. It's one line, it passes every single-system test, and it's what a new engineer will
write.

It's wrong, because the two systems don't always agree on the location. In the attached data,
REC-1077 is at LOC-102 (ORG-A) in System A and at LOC-201 (ORG-B) in System B. With tenant-at-ingest:

- Load System A, then System B: B's entry is immediately ORG-B's, so **ORG-B can read an entry about
  ORG-A's record** until something notices.
- Re-upload a corrected file that moves a row to another org: the row keeps its old tenant until
  something re-checks it.

Whether a row's tenant is *knowable* depends on the other system's rows, which ingestion of one
file can't see. The failure mode is a cross-tenant leak, the one thing the brief says must never
happen, and it is silent.

## Options

1. **Tenant at ingestion from the row's own location.** Simple; leaks as above.
2. **Tenant at ingestion, plus a later job that nulls conflicts.** Leaks in the window between the
   two, and the window is exactly when a new export has just arrived.
3. **Tenant at ingestion from System A only.** Picks System A as authoritative for ownership, which
   the brief rules out. Orphan System B rows would have no tenant rule.
4. **No tenant at ingestion (`NULL`, invisible); reconciliation assigns it after comparing every
   row for the record, and leaves every row `NULL` if they disagree.**

## Decision

Option 4.

- Ingestion always writes `tenant_id = NULL`, and resets it to `NULL` when a row is re-ingested.
- Reconciliation is the **only** writer of `event.tenant_id`: all rows sharing a `match_key` must
  resolve to the same org, or all stay `NULL` and a `TENANT_CONFLICT` discrepancy (itself with
  `tenant_id = NULL`) is raised.
- Tenant reads go through Postgres RLS, where `NULL` never matches, so an unassigned row is
  invisible by construction, not by a filter someone has to remember.

## Consequences

- **Good:** the default is safe. Forgetting to run reconciliation hides data; it never leaks it.
  A new source system inherits the rule without new code.
- **Cost:** new data isn't visible to tenants until reconciliation has run. Reconciliation is
  triggered automatically after each System A / B load, so this is seconds, but it's a real delay
  and it's visible in the product.
- **Cost:** a conflicted record is invisible to *both* orgs, including its probable owner, until a
  person resolves it. That's the intended trade: an unanswered question over a wrong answer.
- **Rule for reviewers:** any code that writes `event.tenant_id` outside
  `reconciliation/service.py` is a defect. Reading tenant data outside `tenant_data_store` is not
  just a defect but impossible from the API: its database login has no privileges on those tables
  (migration 1.3.0).
- **Known limitation:** a record whose System A row hasn't arrived yet (B first, then A) is judged
  on System B's location alone until A arrives. Closing that would mean hiding every B-only
  record, which would also hide genuine orphans; we accept the window and document it.
