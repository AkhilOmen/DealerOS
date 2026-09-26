import logging
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.db.base import utcnow
from app.db.models import Event
from app.db.repositories.discrepancy import discrepancy_repository
from app.db.repositories.event import event_repository
from app.db.session import AsyncDataStore
from app.reconciliation.engine import DiscrepancyDraft, EventView, reconcile

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReconciliationSummary:
    events: int
    visible_events: int
    hidden_events: int
    tenant_changes: int
    open_discrepancies: int
    resolved_discrepancies: int
    by_type: dict[str, int] = field(default_factory=dict)


def _decimal_or_none(value: object) -> Decimal | None:
    try:
        return None if value is None else Decimal(str(value))
    except InvalidOperation:
        return None


def _to_view(event: Event, location_code: str, location_tenant_id) -> EventView:
    return EventView(
        id=event.id,
        source_system=event.source_system,
        external_event_id=event.external_event_id,
        match_key=event.match_key,
        location_id=event.location_id,
        location_code=location_code,
        location_tenant_id=location_tenant_id,
        event_date=event.event_date,
        amount=event.amount,
        status=event.status,
        base_value=_decimal_or_none((event.attributes or {}).get("base_value")),
    )


def _to_row(draft: DiscrepancyDraft) -> dict:
    return {
        "tenant_id": draft.tenant_id,
        "match_key": draft.match_key,
        "type": draft.type,
        "field": draft.field,
        "event_ids": [str(event_id) for event_id in draft.event_ids],
        "values_by_system": draft.values_by_system,
        "details": draft.details,
        "source": "reconciliation",
    }


async def run_reconciliation(ads: AsyncDataStore) -> ReconciliationSummary:
    run_at = utcnow()
    try:
        await discrepancy_repository.lock_for_reconciliation(ads)
        rows = await event_repository.list_with_location(ads)
        result = reconcile(
            [
                _to_view(
                    event=event,
                    location_code=code,
                    location_tenant_id=tenant_id
                ) for event, code, tenant_id in rows
            ]
        )

        tenant_changes = await event_repository.assign_tenants(
            ads=ads, tenant_by_event=result.tenant_by_event
        )
        resolved = await discrepancy_repository.sync(
            ads=ads,
            rows=[
                _to_row(d) for d in result.discrepancies
            ],
            run_at=run_at
        )
    except BaseException:
        await ads.db.rollback()
        raise

    hidden = sum(
        1 for tenant_id in result.tenant_by_event.values() if tenant_id is None
    )
    summary = ReconciliationSummary(
        events=len(rows),
        visible_events=len(rows) - hidden,
        hidden_events=hidden,
        tenant_changes=tenant_changes,
        open_discrepancies=len(result.discrepancies),
        resolved_discrepancies=resolved,
        by_type=dict(Counter(d.type.value for d in result.discrepancies)),
    )
    logger.info("reconciliation done %s", summary)
    return summary
