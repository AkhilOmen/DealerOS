import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.db.enums import DatasetType, Environment, IngestionJobStatus, TriggerType
from app.query.plan import PlannerOutput


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


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)


class OrgQueryResult(BaseModel):
    org: str
    row_count: int
    truncated: bool
    summary: dict[str, Any]  # every number the answer states, derived from `rows`
    rows: list[dict[str, Any]]


class QueryResponse(BaseModel):
    answer: str
    plan: PlannerOutput
    results: list[OrgQueryResult]  # one entry per org; rows are never mixed across orgs


class CredentialCreate(BaseModel):
    environment: Environment = Environment.SANDBOX
    expires_at: datetime | None = None


class CredentialCreated(BaseModel):
    org: str
    client_id: uuid.UUID
    client_secret: str  # shown once; only its hash is stored
    environment: Environment
    expires_at: datetime | None
    created_at: datetime

