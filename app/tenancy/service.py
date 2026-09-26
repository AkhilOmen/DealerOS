import uuid
from dataclasses import dataclass
from datetime import datetime

from app.core.security import generate_client_secret, hash_client_secret, verify_client_secret
from app.db.base import utcnow
from app.db.enums import Environment, Status
from app.db.models import TenantCredential
from app.db.repositories.tenant import tenant_repository
from app.db.repositories.tenant_credential import tenant_credential_repository
from app.db.session import AsyncDataStore
from app.utils.error import OrgNotFoundError

_DUMMY_HASH = hash_client_secret("not-a-real-secret")


@dataclass(frozen=True)
class TenantPrincipal:
    tenant_id: uuid.UUID
    org: str
    client_id: uuid.UUID


async def create_credential(
    ads: AsyncDataStore,
    org: str,
    environment: Environment,
    created_by: str,
    expires_at: datetime | None = None,
) -> tuple[TenantCredential, str]:
    tenant = await tenant_repository.get_first_by(ads, external_org_id=org)
    if tenant is None or tenant.status != Status.ACTIVE:
        raise OrgNotFoundError(f"Org {org} not found.")

    secret = generate_client_secret()
    await tenant_credential_repository.deactivate_active(ads, tenant.id, environment, created_by)
    credential = await tenant_credential_repository.create(
        ads,
        TenantCredential(
            tenant_id=tenant.id,
            client_id=uuid.uuid4(),
            client_secret_hash=hash_client_secret(secret),
            environment=environment,
            status=Status.ACTIVE,
            expires_at=expires_at,
            source="api:internal",
            created_by=created_by,
            updated_by=created_by,
        ),
    )
    return credential, secret


async def authenticate(ads: AsyncDataStore, client_id: uuid.UUID, secret: str) -> TenantPrincipal | None:
    credential = await tenant_credential_repository.get_by_client_id(ads, client_id)
    secret_ok = verify_client_secret(secret, credential.client_secret_hash if credential else _DUMMY_HASH)
    if credential is None or not secret_ok or credential.status != Status.ACTIVE:
        return None
    if credential.expires_at is not None and credential.expires_at <= utcnow():
        return None

    tenant = await tenant_repository.get_by_id(ads, credential.tenant_id)
    if tenant is None or tenant.status != Status.ACTIVE:
        return None
    return TenantPrincipal(tenant_id=tenant.id, org=tenant.external_org_id, client_id=credential.client_id)
