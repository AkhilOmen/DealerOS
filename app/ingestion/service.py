import csv
import logging
import uuid
from collections.abc import AsyncIterator

from app.core.config import settings
from app.db.base import utcnow
from app.db.enums import DatasetType, IngestionJobStatus, TriggerType
from app.db.models import IngestionJob
from app.db.repositories.ingestion_job import TERMINAL_STATUSES, ingestion_job_repository
from app.db.session import AsyncDataStore
from app.ingestion.registry import get_ingestor
from app.ingestion.storage import LocalFileStore, file_store, safe_file_name
from app.messaging.publisher import Publisher
from app.schemas.messages import IngestionJobMessage
from app.utils.error import EnqueueError, FileFormatError, UniqueKeyViolationError

logger = logging.getLogger(__name__)


async def submit_job(
    ads: AsyncDataStore,
    publisher: Publisher,
    *,
    dataset_type: DatasetType,
    file_name: str,
    chunks: AsyncIterator[bytes],
    trigger_type: TriggerType,
    triggered_by: str,
    store: LocalFileStore = file_store,
) -> IngestionJob:

    job_id = uuid.uuid4()
    stored = await store.save(job_id, dataset_type, file_name, chunks)
    duplicate_of = await ingestion_job_repository.get_loaded_by_hash(ads, dataset_type, stored.sha256)

    job = IngestionJob(
        id=job_id,
        dataset_type=dataset_type,
        trigger_type=trigger_type,
        triggered_by=triggered_by,
        file_name=safe_file_name(file_name),
        file_uri=stored.uri,
        file_hash=stored.sha256,
        file_size_bytes=stored.size_bytes,
        status=IngestionJobStatus.SKIPPED_DUPLICATE if duplicate_of else IngestionJobStatus.QUEUED,
        duplicate_of_job_id=duplicate_of.id if duplicate_of else None,
        finished_at=utcnow() if duplicate_of else None,
        source=f"csv:{safe_file_name(file_name)}",
        created_by=triggered_by,
        updated_by=triggered_by,
    )
    await ingestion_job_repository.create(ads, job)

    if duplicate_of:
        logger.info("job=%s skipped: same file already loaded by job=%s", job.id, duplicate_of.id)
        return job

    try:
        await publisher.publish(settings.INGESTION_MAIN_QUEUE, IngestionJobMessage(ingestion_job_id=job.id))
    except Exception as ex:
        logger.exception("job=%s could not be queued", job.id)
        await ingestion_job_repository.update(
            ads, job, status=IngestionJobStatus.FAILED, error_message=f"could not enqueue: {ex}", finished_at=utcnow()
        )
        raise EnqueueError(str(ex)) from ex

    logger.info("job=%s queued dataset_type=%s file=%s", job.id, dataset_type.value, job.file_name)
    return job


async def run_job(ads: AsyncDataStore, job_id: uuid.UUID, store: LocalFileStore = file_store) -> IngestionJob | None:
    job = await ingestion_job_repository.get_by_id(
        ads=ads, id=job_id
    )
    if job is None:
        logger.warning("job=%s not found; dropping message", job_id)
        return None
    if job.status in TERMINAL_STATUSES:
        logger.info("job=%s already %s; skipping", job.id, job.status.value)
        return job

    duplicate_of = await ingestion_job_repository.get_loaded_by_hash(
        ads=ads, dataset_type=job.dataset_type, file_hash=job.file_hash
    )
    if duplicate_of is not None:
        return await _mark_skipped_duplicate(ads, job, duplicate_of.id)

    await ingestion_job_repository.update(
        ads=ads,
        obj=job,
        status=IngestionJobStatus.RUNNING,
        started_at=utcnow(),
        error_message=None
    )

    try:
        fh = store.open_text(job.file_uri)
    except (OSError, ValueError) as ex:  # missing file, bad uri: retrying won't help
        return await mark_failed(ads, job.id, f"cannot open {job.file_uri}: {ex}")

    try:
        with fh:
            result = await get_ingestor(
                dataset_type=job.dataset_type
            ).ingest(
                ads=ads,
                job=job,
                fh=fh
            )
    except (FileFormatError, UnicodeDecodeError, csv.Error) as ex:
        await ads.db.rollback()
        return await mark_failed(ads, job.id, f"{type(ex).__name__}: {ex}")
    except BaseException:
        await ads.db.rollback()
        raise

    if result.rows_received == 0:
        status, error = IngestionJobStatus.FAILED, "file has no data rows"
    elif result.rows_loaded == 0:
        status, error = IngestionJobStatus.FAILED, "all rows rejected"
    elif result.rows_rejected:
        status, error = IngestionJobStatus.PARTIALLY_SUCCEEDED, None
    else:
        status, error = IngestionJobStatus.SUCCEEDED, None

    try:
        await ingestion_job_repository.update(
            ads=ads,
            obj=job,
            status=status,
            rows_received=result.rows_received,
            rows_loaded=result.rows_loaded,
            rows_rejected=result.rows_rejected,
            rejected_row_details=result.rejected_row_details or None,
            error_message=error,
            finished_at=utcnow(),
        )
    except UniqueKeyViolationError:
        reloaded = await ingestion_job_repository.get_by_id(ads, job_id)
        if reloaded is None:
            raise

        duplicate_of = await ingestion_job_repository.get_loaded_by_hash(
            ads, reloaded.dataset_type, reloaded.file_hash
        )
        if duplicate_of is None:
            raise
        return await _mark_skipped_duplicate(ads, reloaded, duplicate_of.id)

    logger.info(
        "job=%s %s received=%s loaded=%s rejected=%s",
        job.id,
        status.value,
        result.rows_received,
        result.rows_loaded,
        result.rows_rejected,
    )
    return job


async def mark_failed(ads: AsyncDataStore, job_id: uuid.UUID, error: str) -> IngestionJob | None:
    job = await ingestion_job_repository.get_by_id(ads, job_id)
    if job is None:
        return None

    await ingestion_job_repository.update(
        ads=ads,
        obj=job,
        status=IngestionJobStatus.FAILED,
        error_message=error[:2000],
        finished_at=utcnow()
    )
    logger.error("job=%s FAILED: %s", job.id, error)
    return job


async def _mark_skipped_duplicate(ads: AsyncDataStore, job: IngestionJob, duplicate_of_id: uuid.UUID) -> IngestionJob:
    await ingestion_job_repository.update(
        ads=ads,
        obj=job,
        status=IngestionJobStatus.SKIPPED_DUPLICATE,
        duplicate_of_job_id=duplicate_of_id,
        finished_at=utcnow(),
    )
    logger.info("job=%s skipped: same file already loaded by job=%s", job.id, duplicate_of_id)
    return job
