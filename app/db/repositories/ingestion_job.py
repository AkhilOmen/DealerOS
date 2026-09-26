from sqlalchemy import select

from app.db.enums import DatasetType, IngestionJobStatus
from app.db.models import IngestionJob
from app.db.repositories.base import AsyncBaseRepository
from app.db.session import AsyncDataStore

LOADED_STATUSES = (IngestionJobStatus.SUCCEEDED, IngestionJobStatus.PARTIALLY_SUCCEEDED)
TERMINAL_STATUSES = (*LOADED_STATUSES, IngestionJobStatus.FAILED, IngestionJobStatus.SKIPPED_DUPLICATE)


class IngestionJobRepository(AsyncBaseRepository[
                                 IngestionJob
                             ]):

    async def get_loaded_by_hash(
            self,
            ads: AsyncDataStore,
            dataset_type: DatasetType,
            file_hash: str
    ) -> IngestionJob | None:

        query = (
            select(self.model)
            .where(
                self.model.dataset_type == dataset_type,
                self.model.file_hash == file_hash,
                self.model.status.in_(LOADED_STATUSES),
            )
            .limit(1)
        )

        result = await ads.db.execute(query)
        return result.scalars().first()

    async def list_recent(
        self, ads: AsyncDataStore, limit: int, status: IngestionJobStatus | None = None
    ) -> list[IngestionJob]:

        query = (
            select(self.model)
            .order_by(self.model.created_at.desc())
            .limit(limit)
        )

        if status is not None:
            query = query.where(self.model.status == status)

        results = await ads.db.execute(query)
        return list(results.scalars())


ingestion_job_repository = IngestionJobRepository(IngestionJob)
