from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CommonBase, str_enum
from app.db.enums import Status


class Tenant(Base, CommonBase):
    __tablename__ = "tenant"

    external_org_id: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[Status] = mapped_column(str_enum(Status, "status"), default=Status.ACTIVE)
