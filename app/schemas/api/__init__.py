import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.db.enums import DatasetType, IngestionJobStatus, TriggerType


class IngestionJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    dataset_type: DatasetType
    trigger_type: TriggerType
    triggered_by: str
    file_name: str
    file_uri: str
    file_hash: str
    file_size_bytes: int | None
    status: IngestionJobStatus
    duplicate_of_job_id: uuid.UUID | None
    rows_received: int | None
    rows_loaded: int | None
    rows_rejected: int | None
    rejected_row_details: list[dict[str, Any]] | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
