import uuid
from typing import Any

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CommonBase, str_enum
from app.db.enums import QueryStatus


class QueryLog(Base, CommonBase):
    __tablename__ = "query_log"

    question: Mapped[str] = mapped_column(Text)
    external_org_id: Mapped[str | None] = mapped_column(String(64))
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenant.id"), index=True)
    status: Mapped[QueryStatus] = mapped_column(str_enum(QueryStatus, "status"))
    error_message: Mapped[str | None] = mapped_column(Text)
    plan: Mapped[dict[str, Any] | None]
    row_count: Mapped[int | None]
    latency_ms: Mapped[int]

    model: Mapped[str | None] = mapped_column(String(100))
