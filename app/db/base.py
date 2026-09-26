import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any, ClassVar

from sqlalchemy import JSON, DateTime, MetaData, String, Uuid, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.config import settings

SYSTEM_INGEST = "system:ingest"
SYSTEM_RECONCILE = "system:reconcile"

NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "ix": "idx_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}

# JSONB on Postgres, generic JSON (STRING/VARIANT) elsewhere.
PortableJSON = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


def str_enum(enum_cls: type[Enum], name: str) -> SAEnum:
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda members: [m.value for m in members],
    )


class Base(DeclarativeBase):
    metadata = MetaData(
        schema=settings.DB_SCHEMA,
        naming_convention=NAMING_CONVENTION
    )
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        dict[str, Any]: PortableJSON,
        list[Any]: PortableJSON,
    }


class CommonBase:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4, sort_order=-100)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), sort_order=100
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), sort_order=101
    )
    created_by: Mapped[str] = mapped_column(String(100), default=SYSTEM_INGEST, sort_order=102)
    updated_by: Mapped[str] = mapped_column(String(100), default=SYSTEM_INGEST, sort_order=103)
    source: Mapped[str | None] = mapped_column(String(100), sort_order=104)
