from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.api.routes import ingestion_jobs
from app.core.config import settings
from app.core.logging import setup_logging
from app.db.session import async_engine
from app.messaging.connection import connect
from app.messaging.publisher import Publisher


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    connection = await connect()
    app.state.publisher = Publisher(connection)
    try:
        yield
    finally:
        await app.state.publisher.close()
        await connection.close()
        await async_engine.dispose()


app = FastAPI(title="DealerOS", version="0.1.0", lifespan=lifespan)
app.include_router(ingestion_jobs.router)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("app.api.main:app", host=settings.API_HOST, port=settings.API_PORT)
