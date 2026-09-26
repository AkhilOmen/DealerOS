# DealerOS

Two systems (A and B) record the same events; neither is authoritative. DealerOS ingests both
exports, **finds where they disagree**, and keeps every row inside its tenant (org): a row belonging
to one org is never visible to another.

```
CSV upload ─► API ─► file saved + ingestion_job (QUEUED) ─► ingestion-main-queue
                                                               │
                                        consumer: parse, validate, upsert events (hidden)
                                                               │ (System A / B jobs)
                                                               ▼
                                                  reconciliation-main-queue
                                                               │
                        consumer: match A ↔ B, assign tenants, write discrepancies
```

- **Tenant = org.** `locations.csv` is the only place `location → org` exists. An event's tenant
  comes from its own location, and is only set by reconciliation (ingestion writes `NULL` = hidden).
  If System A and System B disagree on the tenant for the same record, both stay hidden
  (`TENANT_CONFLICT`).
- **Matching** is by `match_key` (A's `record_id`, B's normalized `record_ref`), comparing
  amount (`total_value` vs summed `value`), date, location and status.

## The brief, and where each part is

| Part | Where |
|---|---|
| 1. Architecture note | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): components, the reconciled data model and exception taxonomy, tenant isolation, what I'm not building in year one |
| 2. Grounding contract | [`docs/GROUNDING.md`](docs/GROUNDING.md): the contract, the enforcing mechanism, and the naive-vs-grounded example ("What was ORG-A's total amount in March?"), pinned by `tests/test_grounding.py` |
| 3. ADR | [`docs/adr/0001-tenant-is-assigned-by-reconciliation.md`](docs/adr/0001-tenant-is-assigned-by-reconciliation.md) |
| 4. Code slice: the reconciliation core | `app/reconciliation/` (the engine is pure; `cli.py` runs it over the CSVs) + `tests/test_reconciliation*.py` |
| Decisions | [`DECISIONS.md`](DECISIONS.md) |

## Run the reconciliation core (no Docker, no database)

```bash
uv sync
uv run python -m app.reconciliation.cli \
    --locations tests/fixtures/dataset/locations.csv \
    --system-a  tests/fixtures/dataset/system_a.csv \
    --system-b  tests/fixtures/dataset/system_b.csv \
    --out out/
```

```
records: 121  EXCEPTION=11  HIDDEN=1  RECONCILED=109
exceptions: 12
  CRITICAL TENANT_CONFLICT            (no org)   1
  HIGH     MISSING_IN_B               ORG-A      1
  ...
```

- `out/reconciled.csv`: one **canonical record** per record id: org (blank = hidden), status
  (`RECONCILED` / `EXCEPTION` / `HIDDEN`), A record ids, B entry ids, A amount, summed B amount,
  date, location, exception codes, and notes (how messy input was interpreted, e.g.
  `REF_NORMALIZED`, `SPLIT_ENTRIES_SUMMED`).
- `out/exceptions.csv`: every exception, most serious first, with a machine-readable
  `reason_code`, its kind (`DISAGREEMENT` / `INPUT`), severity, what it means, what to do, and
  JSON evidence. Unusable rows (unknown location, unreadable date, conflicting duplicates) appear
  here too, with file and line number.

## What I built

- **The reconciliation core**: pure matching rules (`engine.py`), the exception taxonomy
  (`taxonomy.py`), a file-based runner (`files.py`, `cli.py`), and the same engine behind the
  database service. The golden test pins the 12 disagreements in the attached data; separate tests
  cover each trap (dirty references, split vs duplicate, `1,25,400.00`, the cross-tenant record).
- **Tenant isolation**: tenant assigned only by reconciliation (hidden until then), Postgres
  Row-Level Security under a SELECT-only role for every tenant read, tenant-scoped repositories,
  and tests that try to read across the boundary with unfiltered SQL.
- **The query layer**: plain-language questions → a structured plan from an LLM (Claude or any
  OpenAI-compatible model) → per-org queries under RLS → an answer whose every number comes from
  the returned rows. A public API scoped to the caller's credentials, and an internal one.
- **Around it**: an upload API, a RabbitMQ consumer for ingestion and reconciliation with retries
  and dead-lettering, Flyway migrations, Docker Compose.

## What I deliberately did not build

See [`docs/ARCHITECTURE.md` § 4](docs/ARCHITECTURE.md#4-deliberately-not-built-in-year-one) for
the year-one list and what has to become true first (per-tenant database logins,
fuzzy matching, picking a winner, general aggregates, tenant UI/SSO, automated S3 ingestion,
incremental reconciliation, a warehouse).

**A note on scope:** the brief asks for one narrow slice of running code. That slice is
`app/reconciliation/` and runs on its own with the command above. The ingestion service, queue,
and query API go beyond it: they grew out of the product this is meant to become (questions over
tenant data), and they're where the isolation and grounding mechanisms actually run. None of it is
needed to review the slice.

## How I worked with the agent

I used Claude Code (Claude Opus 5.5) as a pair: it read the three CSVs and listed every anomaly
before any design, then we agreed the design one piece at a time (tenancy tables, ingestion
tracking, the single event table, matching rules, discrepancies) before it wrote code. I pointed it
at our existing internal libraries so the models, repositories, migrations and consumer followed
our house patterns.

What I kept control of: naming (`match_key`, `CommonBase`, `rejected_row_details`), and scope.
I cut things it proposed as over-engineering: a rejected-rows table, a reconciliation-runs table,
ORM relationships, an early version of tenant credentials, regex-based org detection, and
aggregates in the query layer. I also chose raw SQL migrations over Alembic, and kept commits and
database migrations in my own hands.

Where it was wrong and how it was caught: the first design set the tenant at ingestion, and it
switched to "hidden until reconciled" while implementing, once the REC-1077 leak window was spotted
(ADR-0001); a "retry works" check that hadn't actually taken the database down was re-run
properly; it had added `temperature=0`, which GPT-5 models reject (caught before the first real
call); and its first query answers counted events across both systems, which is the kind of
confident wrong number the grounding contract now prevents. Every claim about the data is backed
by a test against the real files rather than by its say-so.

## Stack

Python 3.12 · uv · FastAPI · SQLAlchemy 2 (async) · Pydantic v2 · PostgreSQL 16 · RabbitMQ 4
(aio-pika) · Flyway (raw SQL migrations) · Docker Compose

## Quick start

Needs Docker only.

```bash
docker compose up -d --build
docker compose ps                 # postgres, rabbitmq, api, consumer running; migrate exited (0)
curl localhost:8000/health        # {"status":"ok"}
```

This starts Postgres (host port **5433**), RabbitMQ with all queues from
`infra/rabbitmq/definitions.json`, runs the Flyway migrations, then the API (`:8000`) and the
consumer. Nothing needs to be created by hand.

| | URL | Login |
|---|---|---|
| API docs | http://localhost:8000/docs | internal client headers (below) |
| RabbitMQ UI | http://localhost:15672 (vhost `dealeros`) | `dealeros` / `dealeros` |
| Postgres | `localhost:5433`, db `dealeros`, schema `dealeros` | `dealeros` / `dealeros` |

## Ingest the dataset

Upload **locations first**, then System A and B. Reconciliation runs automatically after each
System A / B load.

```bash
ID="X-Internal-Client-Id: <INTERNAL_CLIENT_ID>"          # values: docker-compose.yml (x-app-env)
SECRET="X-Internal-Client-Secret: <INTERNAL_CLIENT_SECRET>"
DIR=path/to/dataset

curl -X POST localhost:8000/v1/ingestion-jobs -H "$ID" -H "$SECRET" -F dataset_type=LOCATIONS        -F file=@$DIR/locations.csv
curl -X POST localhost:8000/v1/ingestion-jobs -H "$ID" -H "$SECRET" -F dataset_type=SYSTEM_A_EVENTS  -F file=@$DIR/system_a.csv
curl -X POST localhost:8000/v1/ingestion-jobs -H "$ID" -H "$SECRET" -F dataset_type=SYSTEM_B_ENTRIES -F file=@$DIR/system_b.csv
```

- `202` = queued, `200` = same file already loaded (`SKIPPED_DUPLICATE`), `401` = bad credentials.
- Job status: `GET /v1/ingestion-jobs` or `GET /v1/ingestion-jobs/{id}` (same headers).
- **Postman:** the two headers above; Body → form-data → `dataset_type` (Text) and `file` (File).
  Don't set `Content-Type` yourself.

Check the result (expect 12 open discrepancies: 6 ORG-A, 5 ORG-B, 1 hidden):

```bash
docker compose exec postgres psql -U dealeros -d dealeros -c \
  "select coalesce(t.external_org_id,'(hidden)') org, d.match_key, d.type, d.values_by_system
     from dealeros.discrepancy d left join dealeros.tenant t on t.id = d.tenant_id
    where d.status = 'OPEN' order by 1, 2;"
docker compose logs -f consumer
```

## Tenant isolation

Reads for an org run in `tenant_data_store(tenant_id)`: every transaction is `SET LOCAL ROLE
dealeros_tenant_reader` with `app.tenant_id` set, and **Postgres Row-Level Security**
(migration `1.1.0`) only returns that org's rows (SELECT only, and nothing at all when no tenant
is set). This is the safety net for the query layer: even a query without a tenant filter can't
see another org's data.

The API container logs in as **`dealeros_api`** (migration `1.3.0`), a non-owner with no access to
`event`, `discrepancy` or `location`. A session that skips `tenant_data_store` gets `permission
denied`. The consumer keeps the owner login (reconciliation must see every org). Password:
`API_DB_PASSWORD` (default `dealeros_api`), passed to Flyway as a placeholder and to the API.

## Ask questions

Two endpoints, same engine:

| Endpoint | Auth | Answers for |
|---|---|---|
| `POST /v1/queries` (public) | tenant `X-Client-Id` + `X-Client-Secret` | **only the caller's org**; naming another org → `403` |
| `POST /v1/internal/queries` | internal `X-Internal-Client-Id` + `X-Internal-Client-Secret` | the orgs named in the question, or every org when none is named |

Create a tenant's credentials (internal headers). The secret is returned **once**; creating another
one for the same environment deactivates the previous one:

```bash
curl -X POST localhost:8000/v1/internal/tenants/ORG-A/credentials -H "$ID" -H "$SECRET" \
     -H 'content-type: application/json' -d '{"environment": "SANDBOX"}'

curl -X POST localhost:8000/v1/queries -H "X-Client-Id: <client_id>" -H "X-Client-Secret: <client_secret>" \
     -H 'content-type: application/json' -d '{"question": "Which records have value mismatches?"}'
```

How a question is answered:
1. The model reads the question and returns the org(s) it names, the dataset (events or
   discrepancies) and the filters (dates, location, system, record, discrepancy type). It never
   writes SQL and never sees any rows.
2. Code decides the orgs: public → the caller's own org; internal → the named orgs, or all.
   Unknown org → `404`; unanswerable question → `422`.
3. Code builds the query and runs it **separately for each org**, under that org's RLS scope
   (max 500 rows per org). Rows are never mixed across orgs.
4. The response has one result per org (`results: [{org, row_count, rows}]`), what the model
   extracted, and a one-line answer. Every call is recorded in `query_log`.

The model is swappable (`.env.example`):

| Provider | Settings |
|---|---|
| Claude (default) | `LLM_PROVIDER=anthropic`, `LLM_MODEL=claude-opus-5`, `LLM_API_KEY=...` |
| Kimi / any OpenAI-compatible API | `LLM_PROVIDER=openai_compatible`, `LLM_BASE_URL=https://api.moonshot.ai/v1`, `LLM_MODEL=<model id>`, `LLM_API_KEY=...` |

Pick one with the eval set (15 golden questions, one billed call each):
`uv run python -m evals.run_planner_eval` — run it once per provider and compare.


```bash
docker compose down -v                                    # deletes DB + RabbitMQ volumes
find data/incoming -mindepth 1 ! -name .gitkeep -delete   # stored uploads (while containers are down)
docker compose up -d --build
```

Only emptying the tables (keeps containers):
`TRUNCATE dealeros.discrepancy, dealeros.event, dealeros.ingestion_job, dealeros.tenant_credential, dealeros.location, dealeros.tenant CASCADE;`

## Local development

Run infrastructure in Docker and the app on the host (e.g. to debug in an IDE):

```bash
uv sync
cp .env.example .env                              # host values: localhost, port 5433
docker compose up -d postgres rabbitmq migrate
docker compose stop api consumer                  # so two consumers don't compete for messages

uv run python -m app.api.main                     # API on :8000
uv run python -m app.consumer.main                # consumer

uv run python -m app.cli ingest --dataset-type LOCATIONS path/to/locations.csv   # without the API
uv run python -m app.cli reconcile                                               # manual run
```

Checks:

```bash
uv run pytest          # needs postgres up; uses a separate dealeros_test database
uv run ruff check app tests
uv run pyright
```

## Migrations

Flyway-style raw SQL, same convention as CGF common-library:

```
app/db/migration/<version>/V<version>__<n>.postgresql-db-script.sql
```

- Add a new version folder for every change (e.g. `1.1.0/V1.1.0__1.postgresql-db-script.sql`),
  then `docker compose run --rm migrate`.
- **Never edit a file that has been applied** — Flyway validates checksums and will refuse to run.
- `tests/test_schema_matches_models.py` fails if the SQL and the SQLAlchemy models drift apart.

## Project layout

```
app/
  reconciliation/ engine.py (pure rules) · taxonomy.py (reason codes) · notes.py
                  files.py + cli.py (CSV in → reconciled.csv + exceptions.csv) · service.py (DB runs)
  ingestion/      parsing, normalizers, ingestors per dataset, file storage, job service
  query/          plan (what the model returns) · planner(s) · compiler (SQL) · service (grounding)
  tenancy/        tenant credentials: create + authenticate
  api/            FastAPI app, routes, auth dependencies
  consumer/       queue consumer entrypoint + handlers (ingestion, reconciliation)
  db/             models, repositories, enums, session (RLS-scoped tenant sessions), migration/ (SQL)
  messaging/      publisher, listener, base handler
  schemas/        Pydantic: CSV rows, queue messages, API bodies
  utils/          custom exceptions
docs/             ARCHITECTURE.md · GROUNDING.md · adr/
evals/            golden questions for the query planner (real model calls)
infra/rabbitmq/   queues, retry/dead-letter topology, dev user
tests/            unit + integration tests
```

## Phase 2 notes (TODOs in code)

S3 uploads (+ Lambda bridge publishing the same message) · Databricks · categories / actors tables ·
incremental reconciliation · `COPY`-based bulk load · internal clients stored in the DB.
