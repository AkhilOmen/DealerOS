import uuid
from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.base import SYSTEM_INGEST
from app.db.models import Location
from app.db.repositories.base import AsyncBaseRepository
from app.db.session import AsyncDataStore


class LocationRepository(AsyncBaseRepository[
                             Location
                         ]):

    async def get_by_external_ids(
            self,
            ads: AsyncDataStore,
            external_location_ids: Iterable[str]
    ) -> dict[str, Location]:
        ids = sorted(set(external_location_ids))
        if not ids:
            return {}

        query = (
            select(self.model)
            .where(self.model.external_location_id.in_(ids))
        )

        result = await ads.db.execute(query)
        return {
            loc.external_location_id: loc for loc in result.scalars()
        }

    async def upsert_many(self, ads: AsyncDataStore, rows: list[dict]) -> None:
        if not rows:
            return
        stmt = pg_insert(self.model).values(
            [
                {
                    "id": uuid.uuid4(),
                    "created_by": SYSTEM_INGEST,
                    "updated_by": SYSTEM_INGEST,
                    **row
                } for row in rows
            ]
        )
        await ads.db.execute(
            stmt.on_conflict_do_update(
                index_elements=[Location.external_location_id],
                set_={
                    "location_name": stmt.excluded.location_name,
                    "source": stmt.excluded.source,
                    "updated_by": SYSTEM_INGEST,
                    "updated_at": func.now(),
                },
            )
        )


location_repository = LocationRepository(Location)
