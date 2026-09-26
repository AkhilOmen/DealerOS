import uuid
from datetime import datetime
from itertools import batched

from sqlalchemy import func, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.base import SYSTEM_RECONCILE
from app.db.enums import DiscrepancyStatus
from app.db.models import Discrepancy
from app.db.repositories.base import AsyncBaseTenantRepository, commit_or_raise
from app.db.session import AsyncDataStore

UPSERT_BATCH_SIZE = 1000
RECONCILIATION_LOCK_KEY = 7_401_001


class DiscrepancyRepository(AsyncBaseTenantRepository[Discrepancy]):
    async def lock_for_reconciliation(self, ads: AsyncDataStore) -> None:
        await ads.db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": RECONCILIATION_LOCK_KEY}
        )

    async def sync(self, ads: AsyncDataStore, rows: list[dict], run_at: datetime) -> int:
        # TODO(phase-2): Databricks needs MERGE INTO instead of ON CONFLICT.
        for batch in batched(rows, UPSERT_BATCH_SIZE):
            stmt = pg_insert(self.model).values(
                [
                    {
                        "id": uuid.uuid4(),
                        "status": DiscrepancyStatus.OPEN,
                        "first_detected_at": run_at,
                        "last_detected_at": run_at,
                        "created_by": SYSTEM_RECONCILE,
                        "updated_by": SYSTEM_RECONCILE,
                        **row,
                    }
                    for row in batch
                ]
            )
            await ads.db.execute(
                stmt.on_conflict_do_update(
                    constraint="uq_discrepancy_match_key_type_field",
                    set_={
                        "tenant_id": stmt.excluded.tenant_id,
                        "event_ids": stmt.excluded.event_ids,
                        "values_by_system": stmt.excluded.values_by_system,
                        "details": stmt.excluded.details,
                        "status": DiscrepancyStatus.OPEN,
                        "last_detected_at": run_at,
                        "resolved_at": None,
                        "updated_by": SYSTEM_RECONCILE,
                        "updated_at": func.now(),
                    },
                )
            )

        resolved = await ads.db.execute(
            update(self.model)
            .where(
                self.model.status == DiscrepancyStatus.OPEN,
                self.model.last_detected_at < run_at
            )
            .values(
                status=DiscrepancyStatus.RESOLVED,
                resolved_at=run_at,
                updated_by=SYSTEM_RECONCILE,
                updated_at=func.now(),
            )
        )
        await commit_or_raise(ads)
        return resolved.rowcount or 0  # type: ignore[attr-defined]


discrepancy_repository = DiscrepancyRepository(Discrepancy)
