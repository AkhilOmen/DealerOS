"""The API's database login (dealeros_api, migration 1.3.0) cannot reach tenant data except through
tenant_data_store. This is the "tired engineer at 6pm on Friday" test: a new endpoint that opens a
plain session and runs SELECT * FROM event gets an error, not every org's rows."""

import pytest
from sqlalchemy import URL, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db import session as db_session
from app.db.enums import Environment
from app.db.models import Tenant
from app.db.session import AsyncDataStore
from app.query import service as query_service
from app.reconciliation.service import run_reconciliation
from app.tenancy import service as tenancy_service
from tests.conftest import API_DB_PASSWORD
from tests.test_ingestion import load_dataset
from tests.test_queries import planner

API_URL = URL.create(
    "postgresql+asyncpg",
    username="dealeros_api",
    password=API_DB_PASSWORD,
    host=settings.POSTGRES_HOST,
    port=settings.POSTGRES_PORT,
    database=settings.POSTGRES_DB,
)


@pytest.fixture
async def api_sessions(ads, publisher, file_store, monkeypatch):
    """Sessions logged in as dealeros_api, also used by tenant_data_store (as in the API process)."""
    await load_dataset(ads, publisher, file_store)
    await run_reconciliation(ads)
    engine = create_async_engine(API_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(db_session, "AsyncSessionLocal", factory)
    yield factory
    await engine.dispose()


async def test_the_api_login_is_not_an_owner_or_superuser(api_sessions):
    async with api_sessions() as s:
        row = (
            await s.execute(
                text("SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
    assert tuple(row) == ("dealeros_api", False, False)


@pytest.mark.parametrize("table", ["event", "discrepancy", "location"])
async def test_plain_session_cannot_read_tenant_data(api_sessions, table):
    async with api_sessions() as s:
        with pytest.raises(DBAPIError, match="permission denied"):
            await s.execute(text(f"SELECT * FROM dealeros.{table}"))


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE dealeros.event SET tenant_id = NULL",
        "INSERT INTO dealeros.discrepancy (id) VALUES (gen_random_uuid())",
        "DELETE FROM dealeros.ingestion_job",
        "DELETE FROM dealeros.tenant_credential",
        "UPDATE dealeros.tenant SET status = 'INACTIVE'",
    ],
)
async def test_api_login_cannot_change_tenant_data_or_delete(api_sessions, sql):
    async with api_sessions() as s:
        with pytest.raises(DBAPIError, match="permission denied"):
            await s.execute(text(sql))


async def test_tenant_data_is_reachable_only_one_org_at_a_time(api_sessions):
    async with api_sessions() as s:
        orgs = dict((await s.execute(select(Tenant.external_org_id, Tenant.id))).all())  # org list is allowed
    assert set(orgs) == {"ORG-A", "ORG-B"}

    scoped = db_session.tenant_data_store(orgs["ORG-B"])  # now on the dealeros_api login
    try:
        locations = set((await scoped.db.execute(text("SELECT external_location_id FROM dealeros.location"))).scalars())
        assert locations == {"LOC-201", "LOC-202"}
        assert (
            await scoped.db.execute(text("SELECT count(*) FROM dealeros.event WHERE match_key = 'REC-1077'"))
        ).scalar() == 0
    finally:
        await scoped.close()


async def test_query_and_credential_flows_work_on_the_api_login(api_sessions):
    ads = AsyncDataStore(db=api_sessions())
    try:
        answer = await query_service.answer_question(
            ads,
            planner(orgs=[], dataset="discrepancies", filters={"discrepancy_type": "VALUE_MISMATCH"}),
            "value mismatches",
            asked_by="test",
        )
        assert {r.org: [row["match_key"] for row in r.rows] for r in answer.results} == {
            "ORG-A": ["REC-1027", "REC-1064", "REC-1088"],
            "ORG-B": ["REC-1003"],
        }

        credential, secret = await tenancy_service.create_credential(ads, "ORG-A", Environment.SANDBOX, "test")
        principal = await tenancy_service.authenticate(ads, credential.client_id, secret)
        assert principal is not None and principal.org == "ORG-A"
    finally:
        await ads.close()


async def test_upload_flow_works_on_the_api_login(api_sessions, publisher, file_store):
    from app.db.enums import DatasetType, IngestionJobStatus
    from app.ingestion import service as ingestion_service
    from tests.test_ingestion import _chunks

    ads = AsyncDataStore(db=api_sessions())
    try:
        job = await ingestion_service.submit_job(
            ads,
            publisher,
            dataset_type=DatasetType.SYSTEM_B_ENTRIES,
            file_name="new.csv",
            chunks=_chunks(
                b"entry_id,record_ref,location_id,recorded_on,value,label\nE-1,REC-1,LOC-101,2026-03-01,1.00,x\n"
            ),
            trigger_type=ingestion_service.TriggerType.MANUAL,
            triggered_by="test",
            store=file_store,
        )
        assert job.status == IngestionJobStatus.QUEUED
    finally:
        await ads.close()
