import asyncio
import logging
import signal

from app.consumer.handlers.ingestion_job_handler import IngestionJobHandler
from app.consumer.handlers.reconciliation_handler import ReconciliationHandler
from app.core.config import settings
from app.core.logging import setup_logging
from app.db.session import async_engine
from app.messaging.connection import connect
from app.messaging.listener import ListenQueueConfig, QueueListener
from app.messaging.publisher import Publisher

logger = logging.getLogger(__name__)


async def main() -> None:
    setup_logging()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    connection = await connect()
    publisher = Publisher(connection)
    listener = QueueListener(
        connection,
        publisher,
        [
            ListenQueueConfig(
                name=settings.INGESTION_MAIN_QUEUE,
                handler=IngestionJobHandler(publisher),
                dead_queue=settings.INGESTION_DEAD_QUEUE,
            ),
            ListenQueueConfig(
                name=settings.RECONCILIATION_MAIN_QUEUE,
                handler=ReconciliationHandler(publisher),
                dead_queue=settings.RECONCILIATION_DEAD_QUEUE,
            ),
        ],
    )

    try:
        logger.info("consumer started")
        await listener.listen(stop)
    finally:
        logger.info("consumer stopping")
        await publisher.close()
        await connection.close()
        await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
