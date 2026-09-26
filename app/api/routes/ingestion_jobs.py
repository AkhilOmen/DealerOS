import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status

from app.api.deps import get_publisher, require_admin
from app.db.enums import DatasetType, IngestionJobStatus, TriggerType
from app.db.repositories.ingestion_job import ingestion_job_repository
from app.db.session import AsyncDataStore, get_async_data_store
from app.ingestion import service
from app.messaging.publisher import Publisher
from app.schemas.api import IngestionJobRead
from app.utils.error import EnqueueError, FileStorageError, FileTooLargeError

router = APIRouter(prefix="/v1/ingestion-jobs", tags=["ingestion"])

CHUNK_SIZE = 1024 * 1024


async def _iter_upload(upload: UploadFile) -> AsyncIterator[bytes]:
    while chunk := await upload.read(CHUNK_SIZE):
        yield chunk


@router.post(
    "",
    response_model=IngestionJobRead,
    status_code=status.HTTP_202_ACCEPTED,
    responses={200: {"description": "Same file already loaded; recorded as SKIPPED_DUPLICATE, not queued"}},
)
async def create_ingestion_job(
    response: Response,
    dataset_type: Annotated[DatasetType, Form()],
    file: Annotated[UploadFile, File()],
    principal: Annotated[str, Depends(require_admin)],
    ads: Annotated[AsyncDataStore, Depends(get_async_data_store)],
    publisher: Annotated[Publisher, Depends(get_publisher)],
):

    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "only .csv files are accepted")

    try:
        job = await service.submit_job(
            ads,
            publisher,
            dataset_type=dataset_type,
            file_name=file.filename or "upload.csv",
            chunks=_iter_upload(file),
            trigger_type=TriggerType.MANUAL,
            triggered_by=principal,
        )
    except FileTooLargeError as ex:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, str(ex)) from ex
    except FileStorageError as ex:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "could not store the uploaded file") from ex
    except EnqueueError as ex:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "job recorded but could not be queued") from ex

    if job.status == IngestionJobStatus.SKIPPED_DUPLICATE:
        response.status_code = status.HTTP_200_OK

    return job


@router.get("/{job_id}", response_model=IngestionJobRead)
async def get_ingestion_job(
    job_id: uuid.UUID,
    _: Annotated[str, Depends(require_admin)],
    ads: Annotated[AsyncDataStore, Depends(get_async_data_store)],
):
    job = await ingestion_job_repository.get_by_id(ads, job_id)

    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ingestion job not found")

    return job


@router.get("", response_model=list[IngestionJobRead])
async def list_ingestion_jobs(
    _: Annotated[str, Depends(require_admin)],
    ads: Annotated[AsyncDataStore, Depends(get_async_data_store)],
    job_status: Annotated[IngestionJobStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    return await ingestion_job_repository.list_recent(ads=ads, limit=limit, status=job_status)
