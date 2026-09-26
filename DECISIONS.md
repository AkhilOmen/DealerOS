# Decisions

Each entry: the decision, the alternative rejected, and the reasoning that separated them.

1. **An event's tenant is assigned by reconciliation, never at ingestion** (ADR-0001).
   Rejected: set `tenant_id` from the row's own location when it's loaded.
   Why: whether a row's owner is knowable depends on the *other* system's rows, and getting it wrong
   is a silent cross-tenant leak.

2. **When systems disagree on a record's org, the record belongs to no org** (`TENANT_CONFLICT`,
   hidden from both). Rejected: trust System A's location.
   Why: the brief says neither system is authoritative; an unanswered question beats a leaked row.

3. **Tenant reads are enforced by Postgres Row-Level Security under a SELECT-only role, and the API
   logs in as a non-owner that can't touch tenant tables at all.** Rejected: `WHERE tenant_id = ?`
   in application code, on the owner login.
   Why: a filter is one forgotten line away from a leak, and owners bypass RLS; with no privileges
   the forgotten line is an error, not a leak.

4. **The model extracts a plan (orgs, dataset, filters); it never writes SQL, never sees rows,
   and never states a number.** Rejected: text-to-SQL with the model writing the answer.
   Why: every number must be recomputable from the returned rows, and a fluent model answer is the
   easiest way for a confident wrong number to appear.

5. **The only total is the sum over records both systems agree on, with disputed records named.**
   Rejected: sum System A's (or B's) amounts.
   Why: either sum is a confident number over records that are known to be in dispute.

6. **Several System B entries for one record: identical entries are a duplicate, different entries
   are a split and are summed.** Rejected: flag every multi-entry record.
   Why: REC-1055's two entries add up to A's total exactly, so flagging it would train people to
   ignore the exceptions list.

7. **Exceptions carry a severity, a plain-English meaning and an action; interpretations (a messy
   reference, a reformatted amount) are notes, not exceptions.** Rejected: a list of field diffs.
   Why: the reader is an operator deciding what to fix, not an engineer reading a diff.

8. **Blank means missing, never zero; dirty values are parsed strictly and the raw text is kept.**
   Rejected: coerce blanks to 0 and parse leniently.
   Why: `MISSING_VALUE` (REC-1050) and a real zero are different facts, and lenient parsing turns
   `1,25,400.00` into 1 (or drops it).

9. **One `event` table for every source system, matched on a normalized `match_key`.**
   Rejected: one table per system.
   Why: matching and tenancy rules are the same for every system; a third system should be a new
   ingestor, not a new schema.

10. **Exact matching only (dates, amounts to the cent).** Rejected: tolerances.
    Why: the disagreements people care about are small (a 2-day date shift, the missing ~28%
    adjustment); a tolerance hides exactly those.
