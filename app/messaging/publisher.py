import logging

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractRobustConnection, HeadersType
from pydantic import BaseModel

logger = logging.getLogger(__name__)

X_RETRY_HEADER = "X-Retry"
X_ERROR_HEADER = "X-Error"


class Publisher:
    def __init__(self, connection: AbstractRobustConnection):
        self._connection = connection
        self._channel: AbstractChannel | None = None

    async def publish(
        self,
        queue: str,
        payload: BaseModel | bytes,
        headers: HeadersType | None = None
    ) -> None:
        if self._channel is None or self._channel.is_closed:
            self._channel = await self._connection.channel(publisher_confirms=True)

        body = payload if isinstance(payload, bytes) else payload.model_dump_json().encode()
        message = aio_pika.Message(
            body=body,
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers=headers or {},
        )
        await self._channel.default_exchange.publish(message, routing_key=queue)
        logger.info("published queue=%s headers=%s", queue, headers or {})

    async def close(self) -> None:
        if self._channel is not None and not self._channel.is_closed:
            await self._channel.close()


def get_retry_count(headers: dict | None) -> int:
    try:
        return int((headers or {}).get(X_RETRY_HEADER, 0))
    except (TypeError, ValueError):
        return 0


async def publish_retry(
    publisher: Publisher,
    payload: BaseModel,
    headers: dict,
    error: str,
    retry_queues: list[str],
    dead_queue: str,
) -> bool:
    attempt = get_retry_count(headers) + 1
    queue = retry_queues[attempt - 1] if attempt <= len(retry_queues) else dead_queue

    await publisher.publish(
        queue,
        payload,
        {X_RETRY_HEADER: str(attempt), X_ERROR_HEADER: error[:500]}
    )
    return queue == dead_queue
