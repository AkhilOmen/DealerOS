import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CommonBase, str_enum
from app.db.enums import Environment, Status


class TenantCredential(Base, CommonBase):
    __tablename__ = "tenant_credential"
    __table_args__ = (
        Index(
            "uq_tenant_credential_active_environment",
            "tenant_id",
            "environment",
            unique=True,
            postgresql_where="status = 'ACTIVE'",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), index=True)
    client_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, default=uuid.uuid4)
    client_secret_hash: Mapped[str] = mapped_column(String(255))
    environment: Mapped[Environment] = mapped_column(str_enum(Environment, "environment"))
    status: Mapped[Status] = mapped_column(str_enum(Status, "status"), default=Status.ACTIVE)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
