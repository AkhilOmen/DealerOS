# Grounding contract

A wrong number stated confidently is worse than no answer. So the system states a number only when it
can show where the number came from, and it refuses to blend two systems' disagreeing figures into one.

## The contract

The system may state a number only if **all** of these hold:

1. **It was computed by code from the rows returned in the same response**, never produced by the
   language model.
2. **The rows are complete.** If the result was cut at the row limit (500 per org), no totals are
   stated; counts say "more than 500".
3. **It is one of three kinds, each with a fixed meaning:**
   - *Counts by system*: "63 System A records and 66 System B entries". Never "events", because an
     event recorded in both systems would be counted twice.
   - *Disputed records*: the ids of records in the result that have an open discrepancy.
   - *Agreed amount total*: the sum of amounts over the records that **no** open discrepancy
     touches. Disputed records are excluded and named, never averaged, never picked from one system.
4. **It is scoped to exactly one org**, taken from the caller's credentials (public API) or resolved
   per org and reported separately (internal API). Rows that belong to no org (tenant conflicts)
   are never counted for anyone.

To justify any number, the response carries: the plan the question was read as (dataset and
filters), every row with its source ids (`external_event_id`, `match_key`, `source_system`), each
row's `open_discrepancies`, and a `summary` from which every number in the answer sentence is
taken. Anyone can recompute each number from the rows in the same response.

## The mechanism

The contract is enforced by where numbers can come from, not by instructions to the model:

| Rule | Enforced by |
|---|---|
| The model can't state a number | Its output schema (`PlannerOutput`) has **no numeric field** and no free-text answer: only orgs, a dataset, filters, or a refusal reason. A test fails if a numeric field is ever added. |
| Numbers come from code | The answer sentence is a template filled from `summarize()` in `query/service.py`; `summarize()` reads only the returned rows. |
| Disputes are visible, not blended | Every event row is fetched with a subquery for its open discrepancies; the agreed total skips any row that has one. |
| Incomplete results state no totals | `summarize()` returns `agreed_amount_total = None` when the result was truncated. |
| One org per number | Queries run per org inside `tenant_data_store(tenant_id)` (Postgres RLS); the public API gets the org from the credential. |
| Every answer is auditable later | `query_log` records the question, the plan, the org(s), the row count, the model and the outcome. |

A question the data can't answer (another topic, a change request) gets `422` with the reason;
naming an unknown org gets `404`.

## Worked example: a plausible, confident, wrong number

**Question:** *"What was ORG-A's total amount in March?"*

**Naive implementations** (all look reasonable, all are wrong):

| Approach | Answer | Why it's wrong |
|---|---|---|
| `SUM(system_a.total_value)` over ORG-A's locations | **6,312,179.31** | Includes REC-1077 (83,361.40), which System B places in ORG-B: it may not be ORG-A's at all. Includes records System B disputes. |
| `SUM(system_b.value)` over ORG-A's locations | **6,335,172.12** | Counts REC-1042's duplicate entry twice, includes the orphan entry for REC-1999 (a record System A never had), and uses the pre-adjustment amounts System B recorded for REC-1027 and REC-1088. |
| `COUNT(*)` of events over both systems | **130 events** | Every event is recorded by both systems, so it's counted twice. |

The two sums differ by 22,992.81 and each one is stated with full confidence. Neither system is
authoritative, so neither is "the" answer.

**What this design returns** (`tests/test_grounding.py` pins these values):

> Events between 2026-03-01 and 2026-03-31. ORG-A: 63 System A records and 66 System B entries;
> 4 records disputed (REC-1027, REC-1042, REC-1088, REC-1999); agreed total 5897773.52 over 60
> undisputed records.

Plus, in the response: all 129 rows, each with its ids and `open_discrepancies` (e.g. REC-1027 →
`["VALUE_MISMATCH"]`), and the plan it was read as. REC-1077 does not appear: it belongs to no org
until someone resolves the conflict. The total is smaller than both naive sums on purpose. It's the
largest number both systems agree on, and the four records that could change it are named, so
someone can follow up on each one.

## What this deliberately doesn't do

- **Pick a winner** for disputed records (e.g. "use System A's amount"). That needs a business rule
  per field, which the brief says doesn't exist ("neither is authoritative").
- **General aggregates** (group by location, averages). Each new number type needs its own
  definition under the rules above before it's allowed.
- **Let the model write the answer text.** A fluent summary would be pleasant, and it's exactly how a
  confident wrong number gets in.
