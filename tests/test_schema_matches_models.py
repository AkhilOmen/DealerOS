"""Migrations are hand-written SQL, so this guards against the SQL and the SQLAlchemy models
drifting apart: same tables, columns, types, nullability and constraint / index names."""

from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

import app.db.models  # noqa: F401
from app.core.config import settings
from app.db.base import Base
from app.db.session import async_engine

SCHEMA = settings.DB_SCHEMA
PG = postgresql.dialect()


def _db_schema(sync_conn) -> dict:
    insp = inspect(sync_conn)
    tables = {}
    for table in insp.get_table_names(schema=SCHEMA):
        if table == "flyway_schema_history":
            continue
        tables[table] = {
            "columns": {
                c["name"]: (c["type"].compile(dialect=PG), c["nullable"]) for c in insp.get_columns(table, schema=SCHEMA)
            },
            "names": {
                insp.get_pk_constraint(table, schema=SCHEMA)["name"],
                *(fk["name"] for fk in insp.get_foreign_keys(table, schema=SCHEMA)),
                *(uq["name"] for uq in insp.get_unique_constraints(table, schema=SCHEMA)),
                *(ck["name"] for ck in insp.get_check_constraints(table, schema=SCHEMA)),
                *(ix["name"] for ix in insp.get_indexes(table, schema=SCHEMA)),
            },
        }
    return tables


def _model_schema() -> dict:
    tables = {}
    for table in Base.metadata.tables.values():
        tables[table.name] = {
            "columns": {c.name: (c.type.compile(dialect=PG), c.nullable) for c in table.columns},
            "names": {c.name for c in table.constraints} | {ix.name for ix in table.indexes},
        }
    return tables


async def test_sql_migrations_match_models():
    async with async_engine.connect() as conn:
        db = await conn.run_sync(_db_schema)
    models = _model_schema()

    assert set(db) == set(models)
    for table in models:
        assert db[table]["columns"] == models[table]["columns"], table
        # Postgres reports unique constraints as indexes too; compare as sets of names.
        assert db[table]["names"] == models[table]["names"], table
