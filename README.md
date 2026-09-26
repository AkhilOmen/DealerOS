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
  api/            FastAPI app, routes, auth (internal client id + secret)
  consumer/       queue consumer entrypoint + handlers (ingestion, reconciliation)
  core/           settings, logging
  db/             models, repositories, enums, session, migration/ (SQL)
  ingestion/      storage, ingestors per dataset, normalizers, service
  reconciliation/ engine (pure matching rules) + service
  messaging/      publisher, listener, base handler
  schemas/        Pydantic: CSV rows, queue messages, API responses
  utils/          custom exceptions
infra/rabbitmq/   queues, retry/dead-letter topology, dev user
tests/            unit + integration tests (golden discrepancy set in test_reconciliation.py)
```

## Phase 2 (TODOs in code)

S3 uploads (+ Lambda bridge publishing the same message) · Databricks · categories / actors tables ·
incremental reconciliation · `COPY`-based bulk load · internal clients stored in the DB.
