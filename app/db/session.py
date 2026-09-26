from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

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
