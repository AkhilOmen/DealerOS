import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CommonBase, str_enum
from app.db.enums import MatchKeyMethod, SourceSystem


class Event(Base, CommonBase):
    __tablename__ = "event"
    __table_args__ = (
        UniqueConstraint("source_system", "external_event_id"),
        Index("idx_event_tenant_id_match_key", "tenant_id", "match_key"),
        Index("idx_event_tenant_id_event_date", "tenant_id", "event_date"),
        # TODO(phase-2): partition by source_system if volume grows.
    )

    source_system: Mapped[SourceSystem] = mapped_column(str_enum(SourceSystem, "source_system"))
    external_event_id: Mapped[str] = mapped_column(String(128))
    match_key: Mapped[str | None] = mapped_column(String(128), index=True)
    match_key_method: Mapped[MatchKeyMethod] = mapped_column(str_enum(MatchKeyMethod, "match_key_method"))

    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("location.id"), index=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenant.id"))

    event_date: Mapped[date | None] = mapped_column(Date)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    status: Mapped[str | None] = mapped_column(String(32))
    # TODO(phase-2): categories / actors tables once master data exists.
    attributes: Mapped[dict[str, Any]] = mapped_column(default=dict)
    raw_row: Mapped[dict[str, Any]]

    ingestion_job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingestion_job.id"), index=True)
    source_row_number: Mapped[int]
