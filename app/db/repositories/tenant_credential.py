import uuid

from sqlalchemy import select, update

from app.db.base import utcnow
from app.db.enums import Environment, Status
from app.db.models import TenantCredential
from app.db.repositories.base import AsyncBaseRepository
from app.db.session import AsyncDataStore


class TenantCredentialRepository(AsyncBaseRepository[TenantCredential]):
    async def get_by_client_id(self, ads: AsyncDataStore, client_id: uuid.UUID) -> TenantCredential | None:
        result = await ads.db.execute(select(self.model).where(self.model.client_id == client_id))
        return result.scalars().first()

    async def deactivate_active(
        self,
        ads: AsyncDataStore,
        tenant_id: uuid.UUID,
        environment: Environment,
        updated_by: str
    ) -> None:
        await ads.db.execute(
            update(self.model)
            .where(
                self.model.tenant_id == tenant_id,
                self.model.environment == environment,
                self.model.status == Status.ACTIVE,
            )
            .values(
                status=Status.INACTIVE,
                revoked_at=utcnow(),
                updated_by=updated_by,
                updated_at=utcnow()
            )
        )


tenant_credential_repository = TenantCredentialRepository(TenantCredential)
