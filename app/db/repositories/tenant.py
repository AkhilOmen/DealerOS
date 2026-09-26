import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.base import SYSTEM_INGEST
from app.db.models import Tenant
from app.db.repositories.base import AsyncBaseRepository
from app.db.session import AsyncDataStore


class TenantRepository(AsyncBaseRepository[Tenant]):

    async def create_missing(
            self,
            ads: AsyncDataStore,
            external_org_ids: Iterable[str],
            source: str
    ) -> None:
        org_ids = sorted(set(external_org_ids))
        if not org_ids:
            return

        # TODO(phase-2): Databricks needs MERGE instead of ON CONFLICT.
        await ads.db.execute(
            pg_insert(self.model)
            .values(
                [
                    {
                        "id": uuid.uuid4(),
                        "external_org_id": org_id,
                        "name": org_id,
                        "source": source,
                        "created_by": SYSTEM_INGEST,
                        "updated_by": SYSTEM_INGEST,
                    }
                    for org_id in org_ids
                ]
            )
            .on_conflict_do_nothing(index_elements=[Tenant.external_org_id])
        )

    async def get_ids_by_external_org_ids(
            self,
            ads: AsyncDataStore,
            external_org_ids: Iterable[str]
    ) -> dict[str, uuid.UUID]:
        org_ids = sorted(set(external_org_ids))
        if not org_ids:
            return {}

        result = await ads.db.execute(
            select(self.model.external_org_id, self.model.id)
            .where(self.model.external_org_id.in_(org_ids))
        )

        return dict(result.all())


tenant_repository = TenantRepository(Tenant)
