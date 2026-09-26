import uuid

from pydantic import BaseModel


class IngestionJobMessage(BaseModel):
    ingestion_job_id: uuid.UUID


class ReconciliationMessage(BaseModel):
    # The ingestion job that triggered this run (None = manual). Every run reconciles all events.
    triggered_by_ingestion_job_id: uuid.UUID | None = None
