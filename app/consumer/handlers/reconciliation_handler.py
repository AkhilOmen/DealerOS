import logging

from app.core.config import settings
from app.db.session import AsyncDataStore, AsyncSessionLocal
from app.messaging.base_handler import AsyncBaseHandler
from app.messaging.publisher import Publisher, get_retry_count, publish_retry
from app.reconciliation import service
from app.schemas.messages import ReconciliationMessage

logger = logging.getLogger(__name__)


class ReconciliationHandler(AsyncBaseHandler):
    def __init__(self, publisher: Publisher):
        self._publisher = publisher

    async def handle_message(self, message: dict, headers: dict) -> None:
        request = ReconciliationMessage.model_validate(message)
        retry_count = get_retry_count(headers)

        ads = AsyncDataStore(db=AsyncSessionLocal())
        try:
            await service.run_reconciliation(ads)
        except Exception as ex:
            logger.exception(
                "reconciliation (triggered by job=%s) attempt %s failed",
                request.triggered_by_ingestion_job_id,
                retry_count + 1,
            )
            dead = await publish_retry(
                self._publisher,
                request,
                headers,
                f"{type(ex).__name__}: {ex}",
                retry_queues=settings.RECONCILIATION_RETRY_QUEUES,
                dead_queue=settings.RECONCILIATION_DEAD_QUEUE,
            )
            if dead:
                logger.error("reconciliation gave up after %s retries", retry_count)
        finally:
            await ads.close()
