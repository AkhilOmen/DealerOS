import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CommonBase, str_enum, utcnow
from app.db.enums import DiscrepancyField, DiscrepancyStatus, DiscrepancyType


class Discrepancy(Base, CommonBase):
    __tablename__ = "discrepancy"
    __table_args__ = (
        UniqueConstraint("match_key", "type", "field"),
        Index("idx_discrepancy_tenant_id_status_type", "tenant_id", "status", "type"),
    )

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenant.id"))
    match_key: Mapped[str] = mapped_column(String(128))
    type: Mapped[DiscrepancyType] = mapped_column(str_enum(DiscrepancyType, "type"))
    field: Mapped[DiscrepancyField] = mapped_column(str_enum(DiscrepancyField, "field"))
    event_ids: Mapped[list[Any]]
    values_by_system: Mapped[dict[str, Any]] = mapped_column(default=dict)
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[DiscrepancyStatus] = mapped_column(
        str_enum(DiscrepancyStatus, "status"), default=DiscrepancyStatus.OPEN
    )
    first_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
