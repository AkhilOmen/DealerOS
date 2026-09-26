import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CommonBase, str_enum
from app.db.enums import Status


class Location(Base, CommonBase):
    __tablename__ = "location"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), index=True)
    external_location_id: Mapped[str] = mapped_column(String(64), unique=True)
    location_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[Status] = mapped_column(str_enum(Status, "status"), default=Status.ACTIVE)
