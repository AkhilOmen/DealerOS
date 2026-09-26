from app.db.enums import DatasetType
from app.db.models import IngestionJob
from app.db.repositories.location import location_repository
from app.db.repositories.tenant import tenant_repository
from app.db.session import AsyncDataStore
from app.ingestion.ingestors.base import BaseIngestor, IngestResult, ParsedRow
from app.schemas.rows import LocationRow


class LocationsIngestor(BaseIngestor[LocationRow]):
    dataset_type = DatasetType.LOCATIONS
    row_model = LocationRow
    key_field = "location_id"

    async def load(
        self, ads: AsyncDataStore, job: IngestionJob, rows: list[ParsedRow[LocationRow]], result: IngestResult
    ) -> None:
        source = f"csv:{job.file_name}"
        org_ids = {p.row.org_id for p in rows}
        await tenant_repository.create_missing(ads, org_ids, source)
        tenant_ids = await tenant_repository.get_ids_by_external_org_ids(ads, org_ids)
        existing = await location_repository.get_by_external_ids(ads, (p.row.location_id for p in rows))

        to_upsert = []
        for p in rows:
            tenant_id = tenant_ids[p.row.org_id]
            current = existing.get(p.row.location_id)
            if current is not None and current.tenant_id != tenant_id:
                result.reject(
                    p.row_number,
                    p.raw,
                    "LOCATION_TENANT_CHANGED",
                    f"{p.row.location_id} already belongs to another org; refusing to move it to {p.row.org_id}",
                )
                continue
            to_upsert.append(
                {
                    "tenant_id": tenant_id,
                    "external_location_id": p.row.location_id,
                    "location_name": p.row.location_name,
                    "source": source,
                }
            )

        await location_repository.upsert_many(ads, to_upsert)
        result.rows_loaded += len(to_upsert)
