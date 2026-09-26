"""Postgres Row-Level Security (migration 1.1.0): a tenant-scoped session only ever sees its own
rows, even for SQL without any tenant filter (which is what the AI query layer will send)."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.config import settings
from app.db.models import Tenant
from app.db.session import TENANT_READER_ROLE, AsyncDataStore, AsyncSessionLocal, tenant_data_store
from app.reconciliation.service import run_reconciliation
from tests.test_ingestion import load_dataset

ORG_A_KEYS = {"REC-1015", "REC-1027", "REC-1042", "REC-1064", "REC-1088", "REC-1999"}
ORG_B_KEYS = {"REC-1003", "REC-1009", "REC-1019", "REC-1050", "REC-1061"}


@pytest.fixture
async def dataset(ads, publisher, file_store) -> dict:
    await load_dataset(ads, publisher, file_store)
    await run_reconciliation(ads)
    return dict((await ads.db.execute(select(Tenant.external_org_id, Tenant.id))).all())


async def _count(ads: AsyncDataStore, sql: str) -> int:
    return (await ads.db.execute(text(sql))).scalar_one()


async def test_raw_sql_in_tenant_session_only_sees_own_rows(ads, dataset):
    for org, tenant_id in dataset.items():
        expected_events = await _count(ads, f"SELECT count(*) FROM dealeros.event WHERE tenant_id = '{tenant_id}'")
        scoped = tenant_data_store(tenant_id)
        try:
            # No WHERE clause at all: RLS alone does the filtering.
            assert await _count(scoped, "SELECT count(*) FROM dealeros.event") == expected_events
            assert await _count(scoped, "SELECT count(*) FROM dealeros.tenant") == 1
            keys = set((await scoped.db.execute(text("SELECT match_key FROM dealeros.discrepancy"))).scalars())
            assert keys == (ORG_A_KEYS if org == "ORG-A" else ORG_B_KEYS)
            assert await _count(scoped, "SELECT count(*) FROM dealeros.event WHERE match_key = 'REC-1077'") == 0
        finally:
            await scoped.close()


async def test_reader_role_without_tenant_sees_nothing(ads, dataset):
    session = AsyncSessionLocal()
    try:
        await session.execute(text(f"SET LOCAL ROLE {TENANT_READER_ROLE}"))
        for table in ("tenant", "location", "event", "discrepancy"):
            assert (await session.execute(text(f"SELECT count(*) FROM dealeros.{table}"))).scalar_one() == 0
    finally:
        await session.close()


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE dealeros.event SET amount = 0",
        "DELETE FROM dealeros.discrepancy",
        "SELECT count(*) FROM dealeros.ingestion_job",
        "SELECT count(*) FROM dealeros.tenant_credential",
    ],
)
async def test_reader_role_cannot_write_or_see_internal_tables(ads, dataset, sql):
    scoped = tenant_data_store(dataset["ORG-A"])
    try:
        with pytest.raises(DBAPIError, match="permission denied"):
            await scoped.db.execute(text(sql))
    finally:
        await scoped.close()


async def test_scope_is_reapplied_after_commit_and_does_not_leak_to_the_pool(ads, dataset):
    scoped = tenant_data_store(dataset["ORG-A"])
    try:
        before = await _count(scoped, "SELECT count(*) FROM dealeros.event")
        await scoped.db.commit()  # new transaction starts on the next query
        assert await _count(scoped, "SELECT count(*) FROM dealeros.event") == before
        assert (await scoped.db.execute(text("SELECT current_user"))).scalar_one() == TENANT_READER_ROLE
    finally:
        await scoped.close()

    fresh = AsyncSessionLocal()
    try:
        assert (await fresh.execute(text("SELECT current_user"))).scalar_one() == settings.POSTGRES_USER
        assert (await fresh.execute(text("SELECT count(*) FROM dealeros.event"))).scalar_one() == 241
    finally:
        await fresh.close()
