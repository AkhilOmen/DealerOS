import uuid
from itertools import batched

from sqlalchemy import Row, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.base import SYSTEM_INGEST, SYSTEM_RECONCILE
from app.db.models import Event, Location
from app.db.repositories.base import AsyncBaseTenantRepository
from app.db.session import AsyncDataStore

UPSERT_BATCH_SIZE = 1000

_UPDATABLE_COLUMNS = (
    "match_key",
    "match_key_method",
    "location_id",
    "event_date",
    "amount",
    "status",
    "attributes",
    "raw_row",
    "ingestion_job_id",
    "source_row_number",
    "source",
)


class EventRepository(AsyncBaseTenantRepository[Event]):
    async def upsert_many(self, ads: AsyncDataStore, rows: list[dict]) -> None:
        # TODO(phase-2): Databricks needs MERGE INTO instead of ON CONFLICT.
        for batch in batched(rows, UPSERT_BATCH_SIZE):
            stmt = pg_insert(self.model).values(
                [
                    {
                        "id": uuid.uuid4(),
                        "created_by": SYSTEM_INGEST,
                        "updated_by": SYSTEM_INGEST,
                        **row,
                    }
                    for row in batch
                ]
            )
            await ads.db.execute(
                stmt.on_conflict_do_update(
                    constraint="uq_event_source_system_external_event_id",
                    set_={
                        **{
                            col: stmt.excluded[col]
                            for col in _UPDATABLE_COLUMNS
                        },
                        "tenant_id": None,
                        "updated_by": SYSTEM_INGEST,
                        "updated_at": func.now(),
                    },
                )
            )

    async def list_with_location(self, ads: AsyncDataStore) -> list[Row[Event, str, uuid.UUID]]:
        # TODO(phase-2): load only the match_keys touched by the triggering ingestion job.
        query = (
            select(self.model, Location.external_location_id, Location.tenant_id)
            .join(Location, Location.id == self.model.location_id)
        )

        result = await ads.db.execute(query)
        return list(result.all())

    async def assign_tenants(self, ads: AsyncDataStore, tenant_by_event: dict[uuid.UUID, uuid.UUID | None]) -> int:
        by_tenant: dict[uuid.UUID | None, list[uuid.UUID]] = {}
        for event_id, tenant_id in tenant_by_event.items():
            by_tenant.setdefault(tenant_id, []).append(event_id)

        changed = 0
        for tenant_id, event_ids in by_tenant.items():
            for batch in batched(event_ids, UPSERT_BATCH_SIZE):
                result = await ads.db.execute(
                    update(self.model)
                    .where(self.model.id.in_(batch), self.model.tenant_id.is_distinct_from(tenant_id))
                    .values(tenant_id=tenant_id, updated_by=SYSTEM_RECONCILE, updated_at=func.now())
                )
                changed += result.rowcount or 0  # type: ignore[attr-defined]
        return changed


event_repository = EventRepository(Event)
