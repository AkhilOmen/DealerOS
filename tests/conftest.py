"""Integration tests run against a separate database (dealeros_test) on the docker-compose
Postgres, so local dev data is never touched. Start it with `docker compose up -d postgres`."""

import os

os.environ["POSTGRES_DB"] = "dealeros_test"  # must be set before app settings are imported

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import text

from app.core.config import settings
from app.db.session import AsyncDataStore, AsyncSessionLocal
from app.ingestion.storage import LocalFileStore

ROOT = Path(__file__).resolve().parent.parent
DATASET = Path(__file__).resolve().parent / "fixtures" / "dataset"
TABLES = ("query_log", "discrepancy", "event", "ingestion_job", "tenant_credential", "location", "tenant")
MIGRATIONS = ROOT / "app" / "db" / "migration"


def _version(path: Path) -> tuple[int, ...]:
    # V1.2.0__1.postgresql-db-script.sql -> (1, 2, 0, 1)
    version, _, rest = path.name[1:].partition("__")
    return (*map(int, version.split(".")), int(rest.split(".")[0]))


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS.glob("*/V*.sql"), key=_version)


async def _connect(database: str) -> asyncpg.Connection:
    return await asyncpg.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD.get_secret_value(),
        database=database,
    )


async def _create_test_database() -> None:
    """Fresh schema every session, built from the same SQL files Flyway applies."""
    conn = await _connect("postgres")
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", settings.POSTGRES_DB):
            await conn.execute(f'CREATE DATABASE "{settings.POSTGRES_DB}"')
    finally:
        await conn.close()

    conn = await _connect(settings.POSTGRES_DB)
    try:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{settings.DB_SCHEMA}" CASCADE')
        for path in migration_files():
            await conn.execute(path.read_text())
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> None:
    try:
        asyncio.run(_create_test_database())
    except OSError as ex:
        pytest.exit(f"Postgres not reachable ({ex}); run `docker compose up -d postgres`", returncode=1)


@pytest.fixture
async def ads() -> AsyncIterator[AsyncDataStore]:
    session = AsyncSessionLocal()
    await session.execute(text(f"TRUNCATE {', '.join(f'{settings.DB_SCHEMA}.{t}' for t in TABLES)} CASCADE"))
    await session.commit()
    store = AsyncDataStore(db=session)
    try:
        yield store
    finally:
        await store.close()


@pytest.fixture
def file_store(tmp_path) -> LocalFileStore:
    return LocalFileStore(root=str(tmp_path / "incoming"))


class FakePublisher:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.published: list[tuple[str, dict, dict]] = []

    async def publish(self, queue, payload, headers=None):
        if self.fail:
            raise ConnectionError("broker down")
        self.published.append((queue, payload.model_dump(mode="json"), headers or {}))


@pytest.fixture
def publisher() -> FakePublisher:
    return FakePublisher()
