from abc import abstractmethod
from decimal import Decimal
from typing import Any, ClassVar

from app.db.enums import DatasetType, SourceSystem
from app.db.models import IngestionJob
from app.db.repositories.event import event_repository
from app.db.repositories.location import location_repository
from app.db.session import AsyncDataStore
from app.ingestion.ingestors.base import BaseIngestor, IngestResult, ParsedRow
from app.ingestion.normalizers import normalize_record_ref
from app.schemas.rows import CsvRow, SystemARow, SystemBRow
from app.utils.error import RowRejection

LOCATION_CODE = "location_code"


def _json_decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


class EventIngestor[RowT: CsvRow](BaseIngestor[RowT]):
    source_system: ClassVar[SourceSystem]

    @abstractmethod
    def to_event(self, row: RowT) -> dict[str, Any]:
        """Map a validated row to event columns, including LOCATION_CODE (the system's own
        location code). Raise RowRejection for rows that can't be stored."""

    async def load(
        self, ads: AsyncDataStore, job: IngestionJob, rows: list[ParsedRow[RowT]], result: IngestResult
    ) -> None:
        source = f"csv:{job.file_name}"
        mapped: list[tuple[ParsedRow[RowT], dict[str, Any]]] = []
        for p in rows:
            try:
                mapped.append((p, self.to_event(p.row)))
            except RowRejection as ex:
                result.reject(p.row_number, p.raw, ex.reason, ex.detail)

        locations = await location_repository.get_by_external_ids(
            ads, (values[LOCATION_CODE] for _, values in mapped)
        )

        to_upsert = []
        for p, values in mapped:
            location_code = values.pop(LOCATION_CODE)
            location = locations.get(location_code)
            if location is None:
                result.reject(p.row_number, p.raw, "UNKNOWN_LOCATION", f"{location_code!r} is not a known location")
                continue
            to_upsert.append(
                {
                    **values,
                    "source_system": self.source_system,
                    "location_id": location.id,
                    "raw_row": p.raw,
                    "ingestion_job_id": job.id,
                    "source_row_number": p.row_number,
                    "source": source,
                }
            )

        await event_repository.upsert_many(ads, to_upsert)
        result.rows_loaded += len(to_upsert)


class SystemAIngestor(EventIngestor[SystemARow]):
    dataset_type = DatasetType.SYSTEM_A_EVENTS
    row_model = SystemARow
    key_field = "record_id"
    source_system = SourceSystem.SYSTEM_A

    def to_event(self, row: SystemARow) -> dict[str, Any]:
        match_key, method = normalize_record_ref(row.record_id)
        if match_key is None:
            raise RowRejection("UNPARSABLE_RECORD_ID", f"record_id {row.record_id!r} is not a valid record id")
        return {
            "external_event_id": row.record_id,
            "match_key": match_key,
            "match_key_method": method,
            LOCATION_CODE: row.location_id,
            "event_date": row.event_date,
            "amount": row.total_value,
            "status": row.state.upper(),
            "attributes": {
                "base_value": _json_decimal(row.base_value),
                "adjustment": _json_decimal(row.adjustment),
                "category_code": row.category_code,
                "actor_id": row.actor_id,
            },
        }


class SystemBIngestor(EventIngestor[SystemBRow]):
    dataset_type = DatasetType.SYSTEM_B_ENTRIES
    row_model = SystemBRow
    key_field = "entry_id"
    source_system = SourceSystem.SYSTEM_B

    def to_event(self, row: SystemBRow) -> dict[str, Any]:
        match_key, method = normalize_record_ref(row.record_ref)
        return {
            "external_event_id": row.entry_id,
            "match_key": match_key,
            "match_key_method": method,
            LOCATION_CODE: row.location_id,
            "event_date": row.recorded_on,
            "amount": row.value,
            "status": None,
            "attributes": {"label": row.label, "raw_record_ref": row.record_ref},
        }
