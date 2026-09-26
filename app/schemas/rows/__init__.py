"""One Pydantic model per dataset. Field names equal the CSV headers; validation errors become
rejected_row_details entries on the ingestion job."""

from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, StringConstraints

from app.ingestion.normalizers import blank_to_none, parse_amount, parse_iso_date

RequiredStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
OptionalStr = Annotated[str | None, BeforeValidator(blank_to_none)]
Amount = Annotated[Decimal | None, BeforeValidator(parse_amount)]
RequiredAmount = Annotated[Decimal, BeforeValidator(parse_amount)]
IsoDate = Annotated[date | None, BeforeValidator(parse_iso_date)]
RequiredIsoDate = Annotated[date, BeforeValidator(parse_iso_date)]


class CsvRow(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class LocationRow(CsvRow):
    location_id: RequiredStr
    org_id: RequiredStr
    location_name: OptionalStr = None


class SystemARow(CsvRow):
    record_id: RequiredStr
    location_id: RequiredStr
    event_date: RequiredIsoDate
    category_code: OptionalStr = None
    actor_id: OptionalStr = None
    base_value: Amount = None
    adjustment: Amount = None
    total_value: RequiredAmount
    state: RequiredStr


class SystemBRow(CsvRow):
    entry_id: RequiredStr
    # Kept exactly as received; normalized into match_key by the ingestor.
    record_ref: str | None = None
    location_id: RequiredStr
    recorded_on: IsoDate = None
    # Blank stays None (reported later as MISSING_VALUE), never 0.
    value: Amount = None
    label: OptionalStr = None
