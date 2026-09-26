import uuid
from types import SimpleNamespace

import pytest

from app.consumer.handlers import ingestion_job_handler
from app.consumer.handlers.ingestion_job_handler import IngestionJobHandler
from app.core.config import settings
from app.db.enums import DatasetType, IngestionJobStatus
from app.messaging.publisher import X_RETRY_HEADER, publish_retry
from app.schemas.messages import IngestionJobMessage
from tests.conftest import FakePublisher


@pytest.mark.parametrize(
    ("headers", "queue", "attempt", "dead"),
    [
        ({}, "ingestion-retry-1-queue", "1", False),
        ({X_RETRY_HEADER: "2"}, "ingestion-retry-3-queue", "3", False),
        ({X_RETRY_HEADER: "3"}, "ingestion-dead-queue", "4", True),
    ],
)
async def test_retry_queue_progression(headers, queue, attempt, dead):
    publisher = FakePublisher()
    message = IngestionJobMessage(ingestion_job_id=uuid.uuid4())
    went_dead = await publish_retry(
        publisher, message, headers, "boom", settings.INGESTION_RETRY_QUEUES, settings.INGESTION_DEAD_QUEUE  # type: ignore[arg-type]
    )
    ((published_queue, _, published_headers),) = publisher.published
    assert (published_queue, published_headers[X_RETRY_HEADER], went_dead) == (queue, attempt, dead)


async def test_transient_failure_is_retried_then_dead_lettered(monkeypatch):
    failed: list[uuid.UUID] = []

    async def boom(ads, job_id, **kwargs):
        raise ConnectionError("db down")

    async def record_failed(ads, job_id, error):
        failed.append(job_id)

    monkeypatch.setattr(ingestion_job_handler.service, "run_job", boom)
    monkeypatch.setattr(ingestion_job_handler.service, "mark_failed", record_failed)

    publisher = FakePublisher()
    handler = IngestionJobHandler(publisher)  # type: ignore[arg-type]
    job_id = uuid.uuid4()
    message = {"ingestion_job_id": str(job_id)}

    await handler.handle_message(message, {})
    await handler.handle_message(message, {X_RETRY_HEADER: "3"})

    (q1, _, h1), (q2, _, h2) = publisher.published
    assert (q1, h1[X_RETRY_HEADER]) == (settings.INGESTION_RETRY_QUEUES[0], "1")
    assert (q2, h2[X_RETRY_HEADER]) == (settings.INGESTION_DEAD_QUEUE, "4")
    assert failed == [job_id]  # only marked FAILED once it reaches the dead queue


async def _handle_with_result(monkeypatch, dataset_type, status):
    job = SimpleNamespace(id=uuid.uuid4(), dataset_type=dataset_type, status=status)

    async def fake_run_job(ads, job_id, **kwargs):
        return job

    monkeypatch.setattr(ingestion_job_handler.service, "run_job", fake_run_job)
    publisher = FakePublisher()
    await IngestionJobHandler(publisher).handle_message({"ingestion_job_id": str(job.id)}, {})  # type: ignore[arg-type]
    return job, publisher


@pytest.mark.parametrize("dataset_type", [DatasetType.SYSTEM_A_EVENTS, DatasetType.SYSTEM_B_ENTRIES])
@pytest.mark.parametrize("status", [IngestionJobStatus.SUCCEEDED, IngestionJobStatus.PARTIALLY_SUCCEEDED])
async def test_loaded_event_job_triggers_reconciliation(monkeypatch, dataset_type, status):
    job, publisher = await _handle_with_result(monkeypatch, dataset_type, status)
    assert publisher.published == [
        (settings.RECONCILIATION_MAIN_QUEUE, {"triggered_by_ingestion_job_id": str(job.id)}, {})
    ]


@pytest.mark.parametrize(
    ("dataset_type", "status"),
    [
        (DatasetType.LOCATIONS, IngestionJobStatus.SUCCEEDED),
        (DatasetType.SYSTEM_A_EVENTS, IngestionJobStatus.FAILED),
        (DatasetType.SYSTEM_B_ENTRIES, IngestionJobStatus.SKIPPED_DUPLICATE),
    ],
)
async def test_other_jobs_do_not_trigger_reconciliation(monkeypatch, dataset_type, status):
    _, publisher = await _handle_with_result(monkeypatch, dataset_type, status)
    assert publisher.published == []
