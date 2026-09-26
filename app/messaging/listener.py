import asyncio
import json
import logging
from dataclasses import dataclass
from functools import partial

from aio_pika.abc import AbstractIncomingMessage, AbstractRobustConnection

from app.core.config import settings
from app.messaging.base_handler import AsyncBaseHandler
from app.messaging.publisher import X_ERROR_HEADER, Publisher

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ListenQueueConfig:
    name: str
    handler: AsyncBaseHandler
    dead_queue: str


class QueueListener:
    def __init__(
        self,
        connection: AbstractRobustConnection,
        publisher: Publisher,
        queues: list[ListenQueueConfig]
    ):
        self._connection = connection
        self._publisher = publisher
        self._queues = queues

    async def listen(self, stop: asyncio.Event) -> None:
        for config in self._queues:
            channel = await self._connection.channel()
            await channel.set_qos(prefetch_count=settings.PREFETCH_COUNT)
            queue = await channel.get_queue(config.name)
            await queue.consume(partial(self._on_message, config))

            logger.info("listening queue=%s handler=%s", config.name, type(config.handler).__name__)

        await stop.wait()

    async def _on_message(self, config: ListenQueueConfig, message: AbstractIncomingMessage) -> None:
        try:
            await config.handler.handle_message(json.loads(message.body), dict(message.headers or {}))
        except Exception as ex:
            logger.exception("message failed on queue=%s", config.name)
            await self._dead_letter(config, message, f"{type(ex).__name__}: {ex}")
            return

        await message.ack()

    async def _dead_letter(self, config: ListenQueueConfig, message: AbstractIncomingMessage, error: str) -> None:
        try:
            headers = {**dict(message.headers or {}), X_ERROR_HEADER: error[:500]}
            await self._publisher.publish(config.dead_queue, message.body, headers)
            await message.ack()
        except Exception:
            logger.exception("could not dead-letter; requeueing on queue=%s", config.name)
            await message.nack(requeue=True)
