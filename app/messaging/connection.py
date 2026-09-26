import aio_pika
from aio_pika.abc import AbstractRobustConnection

from app.core.config import settings


async def connect() -> AbstractRobustConnection:
    return await aio_pika.connect_robust(
        settings.MQ_URL,
        client_properties={"connection_name": settings.SERVICE_NAME}
    )
