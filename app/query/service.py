import logging
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.db.base import utcnow
from app.db.enums import QueryStatus, SourceSystem, Status
from app.db.models import QueryLog, Tenant
from app.db.repositories.query_log import query_log_repository
from app.db.session import AsyncDataStore, tenant_data_store
from app.query.compiler import MAX_ROWS, fetch_discrepancies, fetch_events
from app.query.plan import Dataset, PlannerOutput
from app.query.planner import QueryPlanner
from app.tenancy.service import TenantPrincipal
from app.utils.error import (
    OrgAccessDeniedError,
    OrgNotFoundError,
    PlannerNotConfiguredError,
    UnsupportedQuestionError,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OrgResult:
    org: str
    rows: list[dict[str, Any]]
    truncated: bool
    summary: dict[str, Any]


@dataclass(frozen=True)
class QueryAnswer:
    plan: PlannerOutput
    results: list[OrgResult]
    answer: str


def _normalize(org: str) -> str:
    return org.strip().upper().replace("_", "-").replace(" ", "-")


async def _resolve_orgs(ads: AsyncDataStore, named: list[str]) -> dict[str, uuid.UUID]:
    tenants = dict(
        (
            await ads.db.execute(
                select(Tenant.external_org_id, Tenant.id)
                .where(Tenant.status == Status.ACTIVE)
                .order_by(Tenant.external_org_id)
            )
        ).all()
    )
    orgs = sorted({_normalize(o) for o in named})
    if not orgs:
        return tenants

    unknown = [o for o in orgs if o not in tenants]
    if unknown:
        raise OrgNotFoundError(f"Org {', '.join(unknown)} not found. Known orgs: {', '.join(tenants)}.")
    return {o: tenants[o] for o in orgs}


async def _fetch_for_org(tenant_id: uuid.UUID, plan: PlannerOutput) -> tuple[list[dict[str, Any]], bool]:
    scoped = tenant_data_store(tenant_id)
    try:
        fetch = fetch_events if plan.dataset == Dataset.EVENTS else fetch_discrepancies
        rows = await fetch(scoped, tenant_id, plan.filters)
    finally:
        await scoped.close()
    return rows[:MAX_ROWS], len(rows) > MAX_ROWS


async def answer_question(
    ads: AsyncDataStore,
    planner: QueryPlanner | None,
    question: str,
    asked_by: str,
    tenant: TenantPrincipal | None = None,
) -> QueryAnswer:
    """tenant=None: internal caller, any org(s) named in the question (all when none is named).
    tenant set: public caller, only its own org; naming any other org is refused."""
    started = time.monotonic()
    log = QueryLog(
        question=question,
        created_by=asked_by,
        updated_by=asked_by,
        source="api:queries"
    )
    try:
        if planner is None:
            raise PlannerNotConfiguredError("the query planner is not configured (LLM_API_KEY)")
        log.model = planner.model
        plan = await planner.plan(question, utcnow().date())
        log.plan = plan.model_dump(mode="json")
        if plan.unsupported_reason:
            raise UnsupportedQuestionError(plan.unsupported_reason)

        if tenant is not None:
            others = sorted({_normalize(o) for o in plan.orgs} - {tenant.org})
            if others:
                raise OrgAccessDeniedError(f"You can only query your own org ({tenant.org}), not {', '.join(others)}.")
            orgs = {tenant.org: tenant.tenant_id}
        else:
            orgs = await _resolve_orgs(ads, plan.orgs)
        log.external_org_id = ",".join(orgs)[:64]
        log.tenant_id = next(iter(orgs.values())) if len(orgs) == 1 else None

        results = []
        for org, tenant_id in orgs.items():
            rows, truncated = await _fetch_for_org(tenant_id, plan)
            results.append(OrgResult(org=org, rows=rows, truncated=truncated, summary=summarize(plan, rows, truncated)))

        log.status, log.row_count = QueryStatus.ANSWERED, sum(len(r.rows) for r in results)
        return QueryAnswer(
            plan=plan,
            results=results,
            answer=_describe(plan=plan, results=results)
        )
    except (OrgNotFoundError, OrgAccessDeniedError) as ex:
        log.status, log.error_message = QueryStatus.REJECTED, str(ex)
        raise
    except UnsupportedQuestionError as ex:
        log.status, log.error_message = QueryStatus.UNSUPPORTED, str(ex)
        raise
    except Exception as ex:
        log.status, log.error_message = QueryStatus.FAILED, f"{type(ex).__name__}: {ex}"
        raise
    finally:
        log.latency_ms = int((time.monotonic() - started) * 1000)
        try:
            await query_log_repository.create(ads, log)
        except Exception:
            logger.exception("could not write query_log")


def summarize(plan: PlannerOutput, rows: list[dict[str, Any]], truncated: bool) -> dict[str, Any]:
    if plan.dataset == Dataset.DISCREPANCIES:
        by_type: dict[str, int] = {}
        for row in rows:
            by_type[row["discrepancy_type"]] = by_type.get(row["discrepancy_type"], 0) + 1
        return {"discrepancies": len(rows), "by_type": by_type, "complete": not truncated}

    for row in rows:
        row["open_discrepancies"] = row.get("open_discrepancies") or []
    a_rows = [r for r in rows if r["source_system"] == SourceSystem.SYSTEM_A.value]
    disputed = sorted({r["match_key"] for r in rows if r["open_discrepancies"] and r["match_key"]})
    agreed = [r for r in a_rows if not r["open_discrepancies"]]
    return {
        "system_a_records": len(a_rows),
        "system_b_entries": len(rows) - len(a_rows),
        "disputed_records": disputed,
        "agreed_records": len(agreed),
        "agreed_amount_total": None if truncated else f"{sum(Decimal(r['amount']) for r in agreed if r['amount']):.2f}",
        "complete": not truncated,
    }


def _describe(plan: PlannerOutput, results: list[OrgResult]) -> str:
    f = plan.filters
    scope = []
    if f.date_from or f.date_to:
        scope.append(f"between {f.date_from or '…'} and {f.date_to or '…'}")
    if f.location:
        scope.append(f"at {f.location}")
    if f.source_system:
        scope.append(f"in {f.source_system.value}")
    if f.match_key:
        scope.append(f"for {f.match_key}")
    suffix = (" " + " ".join(scope)) if scope else ""

    parts = []
    for r in results:
        s = r.summary
        if plan.dataset == Dataset.DISCREPANCIES:
            noun = "discrepancy" if s["discrepancies"] == 1 else "discrepancies"
            what = f"{f.discrepancy_type.value} {noun}" if f.discrepancy_type else noun
            parts.append(f"{r.org}: {s['discrepancies']} {what}")
            continue
        text = f"{r.org}: {s['system_a_records']} System A records and {s['system_b_entries']} System B entries"
        if s["disputed_records"]:
            text += f"; {len(s['disputed_records'])} records disputed ({', '.join(s['disputed_records'])})"
        if s["complete"]:
            text += f"; agreed total {s['agreed_amount_total']} over {s['agreed_records']} undisputed records"
        else:
            text += f"; more than {MAX_ROWS} rows, so no totals are stated"
        parts.append(text)

    answer = f"{'Discrepancies' if plan.dataset == Dataset.DISCREPANCIES else 'Events'}{suffix}. " + ". ".join(parts) + "."
    if plan.dataset == Dataset.DISCREPANCIES:
        answer += " Discrepancies are between System A and System B within each org."
    return answer
