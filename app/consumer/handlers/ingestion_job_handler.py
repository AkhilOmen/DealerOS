import logging

from app.core.config import settings
from app.db.enums import DatasetType
from app.db.repositories.ingestion_job import LOADED_STATUSES
from app.db.session import AsyncDataStore, AsyncSessionLocal
from app.ingestion import service
from app.messaging.base_handler import AsyncBaseHandler
from app.messaging.publisher import Publisher, get_retry_count, publish_retry
from app.schemas.messages import IngestionJobMessage, ReconciliationMessage

logger = logging.getLogger(__name__)

EVENT_DATASETS = (DatasetType.SYSTEM_A_EVENTS, DatasetType.SYSTEM_B_ENTRIES)


class IngestionJobHandler(AsyncBaseHandler):
    def __init__(self, publisher: Publisher):
        self._publisher = publisher

    async def handle_message(self, message: dict, headers: dict) -> None:
        request = IngestionJobMessage.model_validate(message)
        retry_count = get_retry_count(headers)

        ads = AsyncDataStore(db=AsyncSessionLocal())
        try:
            job = await service.run_job(ads, request.ingestion_job_id)
            if job is not None and job.dataset_type in EVENT_DATASETS and job.status in LOADED_STATUSES:
                # Also re-sent on a redelivered message; reconciliation is idempotent.
                await self._publisher.publish(
                    settings.RECONCILIATION_MAIN_QUEUE,
                    ReconciliationMessage(triggered_by_ingestion_job_id=job.id),
                )
        except Exception as ex:
            logger.exception("job=%s attempt %s failed", request.ingestion_job_id, retry_count + 1)
            await self._retry_or_dead_letter(request, headers, retry_count, f"{type(ex).__name__}: {ex}")
        finally:
            await ads.close()

    async def _retry_or_dead_letter(
        self, request: IngestionJobMessage, headers: dict, retry_count: int, error: str
    ) -> None:
        dead = await publish_retry(
            self._publisher,
            request,
            headers,
            error,
            retry_queues=settings.INGESTION_RETRY_QUEUES,
            dead_queue=settings.INGESTION_DEAD_QUEUE,
        )
        if not dead:
            logger.warning("job=%s scheduled retry %s", request.ingestion_job_id, retry_count + 1)
            return

        ads = AsyncDataStore(db=AsyncSessionLocal())
        try:
            await service.mark_failed(
                ads=ads,
                job_id=request.ingestion_job_id,
                error=f"gave up after {retry_count} retries: {error}"
            )
        except Exception:
            logger.exception("job=%s is in the dead queue but could not be marked FAILED", request.ingestion_job_id)
        finally:
            await ads.close()
