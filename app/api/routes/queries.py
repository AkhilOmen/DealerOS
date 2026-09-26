from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_planner, require_admin, require_tenant
from app.db.session import AsyncDataStore, get_async_data_store
from app.query import service
from app.query.planner import QueryPlanner
from app.schemas.api import OrgQueryResult, QueryRequest, QueryResponse
from app.tenancy.service import TenantPrincipal
from app.utils.error import (
    OrgAccessDeniedError,
    OrgNotFoundError,
    PlannerError,
    PlannerNotConfiguredError,
    UnsupportedQuestionError,
)

internal_router = APIRouter(prefix="/v1/internal/queries", tags=["internal"])
public_router = APIRouter(prefix="/v1/queries", tags=["queries"])


@internal_router.post("", response_model=QueryResponse)
async def ask_internal(
    body: QueryRequest,
    principal: Annotated[str, Depends(require_admin)],
    ads: Annotated[AsyncDataStore, Depends(get_async_data_store)],
    planner: Annotated[QueryPlanner | None, Depends(get_planner)],
):
    """Any org named in the question, or all orgs when none is named."""
    return await _answer(ads, planner, body.question, asked_by=principal, tenant=None)


@public_router.post("", response_model=QueryResponse)
async def ask_public(
    body: QueryRequest,
    tenant: Annotated[TenantPrincipal, Depends(require_tenant)],
    ads: Annotated[AsyncDataStore, Depends(get_async_data_store)],
    planner: Annotated[QueryPlanner | None, Depends(get_planner)],
):
    """Only the org of the caller's credentials."""
    return await _answer(ads, planner, body.question, asked_by=f"tenant:{tenant.client_id}", tenant=tenant)


async def _answer(
    ads: AsyncDataStore,
    planner: QueryPlanner | None,
    question: str,
    asked_by: str,
    tenant: TenantPrincipal | None,
) -> QueryResponse:
    try:
        answer = await service.answer_question(
            ads=ads,
            planner=planner,
            question=question,
            asked_by=asked_by,
            tenant=tenant
        )
    except OrgAccessDeniedError as ex:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(ex)) from ex
    except OrgNotFoundError as ex:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(ex)) from ex
    except UnsupportedQuestionError as ex:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(ex)) from ex
    except PlannerNotConfiguredError as ex:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(ex)) from ex
    except PlannerError as ex:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"query planner failed: {ex}") from ex

    return QueryResponse(
        answer=answer.answer,
        plan=answer.plan,
        results=[
            OrgQueryResult(org=r.org, row_count=len(r.rows), truncated=r.truncated, summary=r.summary, rows=r.rows)
            for r in answer.results
        ],
    )
