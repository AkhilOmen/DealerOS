import uuid
from collections.abc import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

TENANT_READER_ROLE = "dealeros_tenant_reader"

async_engine = create_async_engine(
    settings.SQLALCHEMY_DATABASE_URI,
    pool_pre_ping=True,
    pool_size=settings.POOL_SIZE,
    max_overflow=settings.MAX_OVERFLOW,
    pool_recycle=settings.POOL_RECYCLE_SECONDS,
    connect_args={"server_settings": {"jit": "off"}},
)

AsyncSessionLocal = async_sessionmaker(
    async_engine,
    autoflush=True,
    expire_on_commit=False,
    class_=AsyncSession
)


class AsyncDataStore:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def close(self) -> None:
        await self.db.close()


async def get_async_data_store() -> AsyncGenerator[AsyncDataStore, None]:
    ads = AsyncDataStore(db=AsyncSessionLocal())
    try:
        yield ads
    finally:
        await ads.close()


def tenant_data_store(tenant_id: uuid.UUID) -> AsyncDataStore:
    if tenant_id is None:
        raise ValueError(
            "tenant_id is required"
        )
    session = AsyncSessionLocal()

    @event.listens_for(session.sync_session, "after_begin")
    def _scope_to_tenant(_session, _transaction, connection):
        connection.execute(
            text(f"SET LOCAL ROLE {TENANT_READER_ROLE}")
        )
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)}
        )

    return AsyncDataStore(db=session)
