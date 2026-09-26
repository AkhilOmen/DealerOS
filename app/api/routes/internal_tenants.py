from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import require_admin
from app.db.session import AsyncDataStore, get_async_data_store
from app.schemas.api import CredentialCreate, CredentialCreated
from app.tenancy import service
from app.utils.error import OrgNotFoundError, UniqueKeyViolationError

router = APIRouter(prefix="/v1/internal/tenants", tags=["internal"])


@router.post("/{org}/credentials", response_model=CredentialCreated, status_code=status.HTTP_201_CREATED)
async def create_tenant_credential(
    org: str,
    body: CredentialCreate,
    principal: Annotated[str, Depends(require_admin)],
    ads: Annotated[AsyncDataStore, Depends(get_async_data_store)],
):
    try:
        credential, secret = await service.create_credential(
            ads=ads,
            org=org.upper(),
            environment=body.environment,
            created_by=principal,
            expires_at=body.expires_at,
        )
    except OrgNotFoundError as ex:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(ex)) from ex
    except UniqueKeyViolationError as ex:
        raise HTTPException(status.HTTP_409_CONFLICT, "credential is being created concurrently; retry") from ex

    return CredentialCreated(
        org=org.upper(),
        client_id=credential.client_id,
        client_secret=secret,
        environment=credential.environment,
        expires_at=credential.expires_at,
        created_at=credential.created_at,
    )
