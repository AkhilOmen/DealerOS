import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text

from app.core.config import settings
from app.db.models import Discrepancy, Event, Location
from app.db.session import AsyncDataStore
from app.query.plan import Filters

MAX_ROWS = 500


async def fetch_events(ads: AsyncDataStore, tenant_id: uuid.UUID, f: Filters) -> list[dict[str, Any]]:
    query = (
        select(
            Event.external_event_id,
            Event.source_system,
            Event.match_key,
            Location.external_location_id.label("location"),
            Event.event_date,
            Event.amount,
            Event.status,
        )
        .join(Location, Location.id == Event.location_id)
        .where(Event.tenant_id == tenant_id)
        .order_by(Event.event_date, Event.external_event_id)
        .limit(MAX_ROWS + 1)
    )

    if f.date_from:
        query = query.where(Event.event_date >= f.date_from)
    if f.date_to:
        query = query.where(Event.event_date <= f.date_to)
    if f.location:
        query = query.where(Location.external_location_id == f.location.upper())
    if f.source_system:
        query = query.where(Event.source_system == f.source_system)
    if f.match_key:
        query = query.where(Event.match_key == f.match_key.upper())

    return await _run(ads, query)


async def fetch_discrepancies(ads: AsyncDataStore, tenant_id: uuid.UUID, f: Filters) -> list[dict[str, Any]]:
    query = (
        select(
            Discrepancy.match_key,
            Discrepancy.type.label("discrepancy_type"),
            Discrepancy.values_by_system,
            Discrepancy.status,
            Discrepancy.first_detected_at,
        )
        .where(Discrepancy.tenant_id == tenant_id)
        .order_by(Discrepancy.match_key, Discrepancy.type)
        .limit(MAX_ROWS + 1)
    )

    if f.match_key:
        query = query.where(Discrepancy.match_key == f.match_key.upper())
    if f.discrepancy_type:
        query = query.where(Discrepancy.type == f.discrepancy_type)

    return await _run(ads, query)


async def _run(ads: AsyncDataStore, query) -> list[dict[str, Any]]:
    await ads.db.execute(
        text(f"SET LOCAL statement_timeout = {int(settings.QUERY_STATEMENT_TIMEOUT_MS)}")
    )
    rows = (
        await ads.db.execute(query)
    ).mappings().all()

    return [
        {
            key: _json_safe(value) for key, value in row.items()
        } for row in rows
    ]


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value"):  # enums
        return value.value
    return value
