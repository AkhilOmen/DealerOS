from datetime import date
from enum import Enum

from pydantic import BaseModel, Field

from app.db.enums import DiscrepancyType, SourceSystem


class Dataset(str, Enum):
    EVENTS = "events"
    DISCREPANCIES = "discrepancies"


class Filters(BaseModel):
    date_from: date | None = Field(default=None, description="events: event_date >= this (inclusive)")
    date_to: date | None = Field(default=None, description="events: event_date <= this (inclusive)")
    location: str | None = Field(default=None, description="events: location code, e.g. LOC-101")
    source_system: SourceSystem | None = Field(default=None, description="events: SYSTEM_A or SYSTEM_B")
    match_key: str | None = Field(default=None, description="record id, e.g. REC-1003")
    discrepancy_type: DiscrepancyType | None = Field(default=None, description="discrepancies only")


class PlannerOutput(BaseModel):
    orgs: list[str] = Field(
        default_factory=list,
        description="every org named in the question, e.g. ORG-A"
    )
    dataset: Dataset = Dataset.EVENTS
    filters: Filters = Field(default_factory=Filters)
    unsupported_reason: str | None = Field(
        default=None,
        description="set when the question can't be answered from this data"
    )
