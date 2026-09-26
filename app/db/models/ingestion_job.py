import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CommonBase, str_enum
from app.db.enums import DatasetType, IngestionJobStatus, TriggerType


class IngestionJob(Base, CommonBase):
    __tablename__ = "ingestion_job"
    __table_args__ = (
        Index(
            "uq_ingestion_job_dataset_type_file_hash_loaded",
            "dataset_type",
            "file_hash",
            unique=True,
            postgresql_where="status IN ('SUCCEEDED', 'PARTIALLY_SUCCEEDED')",
        ),
    )

    dataset_type: Mapped[DatasetType] = mapped_column(str_enum(DatasetType, "dataset_type"))
    trigger_type: Mapped[TriggerType] = mapped_column(str_enum(TriggerType, "trigger_type"))
    triggered_by: Mapped[str] = mapped_column(String(100))
    file_name: Mapped[str] = mapped_column(String(255))
    file_uri: Mapped[str] = mapped_column(String(1024))
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[IngestionJobStatus] = mapped_column(
        str_enum(IngestionJobStatus, "status"), default=IngestionJobStatus.QUEUED
    )
    duplicate_of_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ingestion_job.id"))

    rows_received: Mapped[int | None]
    rows_loaded: Mapped[int | None]
    rows_rejected: Mapped[int | None]
    # TODO(phase-2): move to a separate table if files get large.
    rejected_row_details: Mapped[list[Any] | None]

    error_message: Mapped[str | None] = mapped_column(Text)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
